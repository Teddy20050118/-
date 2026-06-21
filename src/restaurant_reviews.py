from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request
from urllib.parse import parse_qs, quote_plus, unquote, urlparse


ReviewReport = Dict[str, Any]
ReviewSource = Dict[str, str]


INCENTIVE_KEYWORDS = [
    "五星送",
    "5星送",
    "五顆星送",
    "評論送",
    "評價送",
    "打卡送",
    "按讚送",
    "好評送",
    "送飲料",
    "送小菜",
    "送甜點",
]
SPONSORED_KEYWORDS = ["業配", "合作", "邀約", "試吃邀約", "店家邀請", "本文與", "贊助"]
SHORT_PRAISE = ["好吃", "讚", "服務好", "很棒", "推", "推薦", "五星", "滿分"]
POSITIVE_KEYWORDS = ["好吃", "新鮮", "划算", "親切", "乾淨", "推薦", "回訪", "份量", "用心", "美味"]
NEGATIVE_KEYWORDS = ["難吃", "雷", "貴", "等很久", "態度差", "不新鮮", "失望", "踩雷", "油膩", "普通"]
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().strip(".")
    return cleaned or "unknown_restaurant"


def _cache_path(project_root: str | Path, restaurant_name: str) -> Path:
    return Path(project_root) / f"reviews_{_safe_filename(restaurant_name)}.json"


def _empty_report(restaurant_name: str, message: str = "尚未更新評價") -> ReviewReport:
    return {
        "success": False,
        "message": message,
        "restaurantName": restaurant_name,
        "updatedAt": None,
        "overallScore": 0,
        "sentiment": "unknown",
        "summary": "",
        "pros": [],
        "cons": [],
        "recommendedFor": [],
        "riskLevel": "low",
        "riskReasons": [],
        "sources": [],
    }


def load_review_cache(project_root: str | Path, restaurant_name: str) -> ReviewReport:
    path = _cache_path(project_root, restaurant_name)
    if not path.exists():
        return _empty_report(restaurant_name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _empty_report(restaurant_name, f"評價快取讀取失敗: {exc}")
    return _normalize_report(data, restaurant_name, success=True)


def write_review_cache(project_root: str | Path, restaurant_name: str, report: ReviewReport) -> None:
    path = _cache_path(project_root, restaurant_name)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_restaurant_reviews(project_root: str | Path, restaurant_name: str) -> ReviewReport:
    sources = asyncio.run(_collect_review_sources(restaurant_name))
    signals = analyze_review_signals(sources)
    try:
        llm_summary = summarize_reviews(restaurant_name, sources, signals)
    except Exception:
        llm_summary = _fallback_summary(restaurant_name, sources, signals)
    report = _build_report(restaurant_name, sources, signals, llm_summary)
    write_review_cache(project_root, restaurant_name, report)
    return report


async def _collect_review_sources(restaurant_name: str) -> List[ReviewSource]:
    queries = [
        f"{restaurant_name} 評價",
        f"{restaurant_name} 部落格 評價",
        f"{restaurant_name} dcard ptt google 評價",
    ]
    gathered: List[ReviewSource] = []
    seen_urls: set[str] = set()

    if os.getenv("USE_PLAYWRIGHT_REVIEW_SEARCH", "false").lower() != "true":
        return _collect_review_sources_via_html_search(queries, seen_urls)

    try:
        from playwright.async_api import async_playwright
    except Exception:
        return _collect_review_sources_via_html_search(queries, seen_urls)

    try:
        playwright_ctx = async_playwright()
        p = await playwright_ctx.__aenter__()
        browser = await p.chromium.launch(headless=True)
    except Exception:
        try:
            await playwright_ctx.__aexit__(None, None, None)  # type: ignore[name-defined]
        except Exception:
            pass
        return _collect_review_sources_via_html_search(queries, seen_urls)

    try:
        page = await browser.new_page(
            locale="zh-TW",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
        )
        for query in queries:
            try:
                search_url = f"https://www.google.com/search?hl=zh-TW&q={quote_plus(query)}"
                await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(1200)
                results = await page.evaluate(
                    """
                    () => {
                      const rows = [];
                      const nodes = Array.from(document.querySelectorAll('div.g, div[data-sokoban-container], a'));
                      for (const node of nodes) {
                        const link = node.matches('a') ? node : node.querySelector('a');
                        const href = link && link.href;
                        if (!href || !href.startsWith('http')) continue;
                        const titleNode = node.querySelector('h3') || link;
                        const title = (titleNode && titleNode.innerText || '').trim();
                        const text = (node.innerText || '').replace(/\\s+/g, ' ').trim();
                        if (!title || text.length < 12) continue;
                        rows.push({ title, url: href, snippet: text.slice(0, 500) });
                      }
                      return rows.slice(0, 12);
                    }
                    """
                )
                for row in results:
                    url = str(row.get("url", ""))
                    if not url or url in seen_urls or _is_youtube_url(url):
                        continue
                    seen_urls.add(url)
                    gathered.append(
                        {
                            "title": str(row.get("title", ""))[:120],
                            "url": url,
                            "excerpt": str(row.get("snippet", ""))[:500],
                            "sourceType": _classify_source(url, str(row.get("title", ""))),
                        }
                    )
                    if len(gathered) >= 12:
                        break
            except Exception:
                continue
        await browser.close()
    finally:
        try:
            await playwright_ctx.__aexit__(None, None, None)
        except Exception:
            pass

    if gathered:
        return gathered[:12]
    return _collect_review_sources_via_html_search(queries, seen_urls)


def _collect_review_sources_via_html_search(
    queries: List[str],
    seen_urls: set[str],
) -> List[ReviewSource]:
    gathered: List[ReviewSource] = []
    for query in queries:
        gathered.extend(_collect_review_sources_via_jina_google(query, seen_urls, 12 - len(gathered)))
        if len(gathered) >= 12:
            return gathered[:12]

        for search_url, parser_factory in [
            (
                f"https://duckduckgo.com/html/?q={quote_plus(query)}",
                DuckDuckGoHTMLParser,
            ),
            (
                f"https://www.bing.com/search?q={quote_plus(query)}&setlang=zh-TW",
                BingHTMLParser,
            ),
        ]:
            try:
                html = _fetch_search_html(search_url)
                parser = parser_factory()
                parser.feed(html)
                for row in parser.results:
                    url = _clean_search_url(row.get("url", ""))
                    title = unescape(row.get("title", "")).strip()
                    excerpt = unescape(row.get("excerpt", "")).strip()
                    if not url or url in seen_urls or _is_youtube_url(url):
                        continue
                    if not title or len(f"{title} {excerpt}") < 12:
                        continue
                    seen_urls.add(url)
                    gathered.append(
                        {
                            "title": title[:120],
                            "url": url,
                            "excerpt": re.sub(r"\s+", " ", excerpt or title)[:500],
                            "sourceType": _classify_source(url, title),
                        }
                    )
                    if len(gathered) >= 12:
                        return gathered
            except Exception:
                continue
    return gathered[:12]


def _collect_review_sources_via_jina_google(
    query: str,
    seen_urls: set[str],
    limit: int,
) -> List[ReviewSource]:
    if limit <= 0:
        return []
    url = f"https://r.jina.ai/http://www.google.com/search?q={quote_plus(query)}"
    try:
        markdown = _fetch_search_html(url)
    except Exception:
        return []

    rows: List[ReviewSource] = []
    lines = markdown.splitlines()
    for idx, line in enumerate(lines):
        match = re.search(r"\[([^\]]{2,160})\]\((https?://[^)]+)\)", line)
        if not match:
            continue
        title = _strip_markdown(match.group(1))
        target_url = _clean_search_url(match.group(2))
        if not target_url or target_url in seen_urls or _is_youtube_url(target_url):
            continue
        title_lower = title.lower()
        if (
            _is_search_noise_url(target_url)
            or title_lower.startswith(("image ", "translate this page"))
            or title_lower in {"read more", "website", "menu"}
        ):
            continue

        excerpt_parts: List[str] = []
        for follow in lines[idx + 1 : idx + 5]:
            clean = _strip_markdown(follow).strip()
            if not clean or clean.startswith(("### ", "[", "!", "http")):
                continue
            excerpt_parts.append(clean)
        excerpt = re.sub(r"\s+", " ", " ".join(excerpt_parts))[:500]
        if len(f"{title} {excerpt}") < 12:
            continue

        seen_urls.add(target_url)
        rows.append(
            {
                "title": title[:120],
                "url": target_url,
                "excerpt": excerpt or title,
                "sourceType": _classify_source(target_url, title),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def _strip_markdown(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[#*\s·-]+", "", text)
    return unescape(text).strip()


def _is_search_noise_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if not host:
        return True
    noise_hosts = {
        "www.google.com",
        "google.com",
        "support.google.com",
        "accounts.google.com",
        "webcache.googleusercontent.com",
    }
    return host in noise_hosts or host.endswith(".google.com")


def _fetch_search_html(url: str) -> str:
    req = request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            ),
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
        },
    )
    with request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        charset = resp.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def _clean_search_url(url: str) -> str:
    if not url:
        return ""
    url = unescape(url)
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        if uddg:
            return unquote(uddg)
    if "bing.com" in parsed.netloc and parsed.path.startswith("/ck/a"):
        target = parse_qs(parsed.query).get("u", [""])[0]
        if target:
            if target.startswith("a1"):
                target = target[2:]
            try:
                import base64

                padded = target + "=" * (-len(target) % 4)
                return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
            except Exception:
                return target
    if parsed.scheme in {"http", "https"}:
        return url
    return ""


class DuckDuckGoHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: List[ReviewSource] = []
        self._current: Optional[ReviewSource] = None
        self._capture: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attr = dict(attrs)
        classes = attr.get("class", "") or ""
        if tag == "a" and "result__a" in classes:
            self._current = {"title": "", "url": attr.get("href", "") or "", "excerpt": "", "sourceType": "web"}
            self._capture = "title"
        elif self._current is not None and tag in {"a", "div"} and "result__snippet" in classes:
            self._capture = "excerpt"

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._capture:
            self._current[self._capture] = (self._current.get(self._capture, "") + data).strip()

    def handle_endtag(self, tag: str) -> None:
        if self._current is not None and tag == "a" and self._capture == "title":
            self._capture = None
        elif self._current is not None and tag in {"a", "div"} and self._capture == "excerpt":
            self._capture = None
            if self._current.get("title"):
                self.results.append(self._current)
                self._current = None


class BingHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: List[ReviewSource] = []
        self._current: Optional[ReviewSource] = None
        self._in_result = False
        self._capture: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        attr = dict(attrs)
        classes = attr.get("class", "") or ""
        if tag == "li" and "b_algo" in classes:
            self._in_result = True
            self._current = {"title": "", "url": "", "excerpt": "", "sourceType": "web"}
        elif self._in_result and tag == "a" and self._current is not None and not self._current.get("url"):
            self._current["url"] = attr.get("href", "") or ""
            self._capture = "title"
        elif self._in_result and tag == "p" and self._current is not None:
            self._capture = "excerpt"

    def handle_data(self, data: str) -> None:
        if self._current is not None and self._capture:
            self._current[self._capture] = (self._current.get(self._capture, "") + data).strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture == "title":
            self._capture = None
        elif tag == "p" and self._capture == "excerpt":
            self._capture = None
        elif tag == "li" and self._in_result and self._current is not None:
            if self._current.get("title") and self._current.get("url"):
                self.results.append(self._current)
            self._current = None
            self._in_result = False


def _is_youtube_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in YOUTUBE_HOSTS or host.endswith(".youtube.com")


def _classify_source(url: str, title: str) -> str:
    host = urlparse(url).netloc.lower()
    haystack = f"{host} {title}".lower()
    if "google" in host:
        return "google"
    if any(site in haystack for site in ["blog", "pixnet", "medium", "wordpress", "痞客邦"]):
        return "blog"
    if any(site in haystack for site in ["dcard", "ptt", "forum", "mobile01"]):
        return "forum"
    return "web"


def analyze_review_signals(sources: List[ReviewSource]) -> Dict[str, Any]:
    if not sources:
        return {
            "positiveHits": 0,
            "negativeHits": 0,
            "shortPraiseHits": 0,
            "specificFoodMentions": 0,
            "duplicateCount": 0,
            "incentiveHits": [],
            "sponsoredHits": [],
            "riskPoints": 0,
            "riskLevel": "low",
            "riskReasons": ["公開評價來源不足，暫時無法判斷可信度"],
            "sentiment": "unknown",
            "overallScore": 0,
        }

    text = "\n".join(
        f"{s.get('title', '')}\n{s.get('excerpt', '')}" for s in sources
    )
    compact_text = re.sub(r"\s+", "", text)
    snippets = [
        re.sub(r"\s+", "", s.get("excerpt", ""))[:80]
        for s in sources
        if s.get("excerpt")
    ]

    incentive_hits = [kw for kw in INCENTIVE_KEYWORDS if kw in text]
    sponsored_hits = [kw for kw in SPONSORED_KEYWORDS if kw in text]
    positive_hits = sum(text.count(kw) for kw in POSITIVE_KEYWORDS)
    negative_hits = sum(text.count(kw) for kw in NEGATIVE_KEYWORDS)
    short_praise_hits = sum(1 for kw in SHORT_PRAISE if kw in compact_text)
    duplicate_count = sum(count - 1 for count in Counter(snippets).values() if count > 1)

    specific_food_mentions = len(re.findall(r"(牛肉|雞|豬|魚|麵|飯|湯|鍋|咖哩|燒肉|甜點|飲料|排|堡)", text))
    vague_positive_ratio = 0.0
    if short_praise_hits:
        vague_positive_ratio = short_praise_hits / max(1, short_praise_hits + specific_food_mentions)

    risk_points = 0
    risk_reasons: List[str] = []
    if incentive_hits:
        risk_points += 4
        risk_reasons.append(f"找到誘導評論線索: {', '.join(sorted(set(incentive_hits)))}")
    if duplicate_count:
        risk_points += min(3, duplicate_count)
        risk_reasons.append("多筆搜尋摘錄文字高度相似，建議查看來源確認是否重複轉載")
    if vague_positive_ratio >= 0.45 and short_praise_hits >= 3:
        risk_points += 2
        risk_reasons.append("正面詞偏短且缺少具體菜色細節，可信度需保守看待")
    if positive_hits >= 6 and negative_hits == 0 and sources:
        risk_points += 1
        risk_reasons.append("來源語氣過度一致，幾乎沒有中立或負面細節")
    if sponsored_hits:
        risk_points += 1
        risk_reasons.append(f"部分文章可能是合作或邀約內容: {', '.join(sorted(set(sponsored_hits)))}")

    if risk_points >= 6:
        risk_level = "high"
    elif risk_points >= 3:
        risk_level = "medium"
    else:
        risk_level = "low"

    if positive_hits == 0 and negative_hits == 0:
        sentiment = "unknown"
    elif positive_hits >= negative_hits * 2 + 1:
        sentiment = "positive"
    elif negative_hits >= positive_hits:
        sentiment = "negative"
    else:
        sentiment = "mixed"

    base_score = 60
    base_score += min(18, positive_hits * 2)
    base_score -= min(22, negative_hits * 4)
    base_score -= min(25, risk_points * 4)
    if sponsored_hits:
        base_score -= 4
    overall_score = max(0, min(100, base_score))

    return {
        "positiveHits": positive_hits,
        "negativeHits": negative_hits,
        "shortPraiseHits": short_praise_hits,
        "specificFoodMentions": specific_food_mentions,
        "duplicateCount": duplicate_count,
        "incentiveHits": sorted(set(incentive_hits)),
        "sponsoredHits": sorted(set(sponsored_hits)),
        "riskPoints": risk_points,
        "riskLevel": risk_level,
        "riskReasons": risk_reasons,
        "sentiment": sentiment,
        "overallScore": overall_score,
    }


def summarize_reviews(
    restaurant_name: str,
    sources: List[ReviewSource],
    signals: Dict[str, Any],
) -> Dict[str, Any]:
    fallback = _fallback_summary(restaurant_name, sources, signals)
    if not sources:
        return fallback

    try:
        from ollama_fuc import chat
    except Exception:
        return fallback

    source_lines = "\n".join(
        f"- [{s.get('sourceType')}] {s.get('title')}: {s.get('excerpt')}"
        for s in sources[:8]
    )
    prompt = f"""
你是餐廳評價整理助手。請根據搜尋來源摘錄，輸出客觀、保守、繁體中文 JSON。
不要宣稱店家造假；若有疑慮，只說「可信度風險」與可觀察線索。

餐廳: {restaurant_name}
規則訊號: {json.dumps(signals, ensure_ascii=False)}
來源摘錄:
{source_lines}

請只輸出 JSON，欄位:
summary: 80字內給客人看的摘要
pros: 2到4個優點字串
cons: 1到4個缺點或注意事項字串
recommendedFor: 1到3個適合客群字串
"""
    try:
        raw = chat([{"role": "user", "content": prompt}], timeout=45.0)
        obj = _extract_json_object(raw)
        if not obj:
            return fallback
        return {
            "summary": str(obj.get("summary") or fallback["summary"])[:180],
            "pros": _string_list(obj.get("pros"), fallback["pros"], limit=4),
            "cons": _string_list(obj.get("cons"), fallback["cons"], limit=4),
            "recommendedFor": _string_list(
                obj.get("recommendedFor"),
                fallback["recommendedFor"],
                limit=3,
            ),
        }
    except Exception:
        return fallback


def _extract_json_object(raw: str) -> Optional[Dict[str, Any]]:
    match = re.search(r"\{.*\}", raw, flags=re.S)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _string_list(value: Any, fallback: List[str], limit: int) -> List[str]:
    if not isinstance(value, list):
        return fallback
    cleaned = [str(item).strip()[:80] for item in value if str(item).strip()]
    return cleaned[:limit] or fallback


def _fallback_summary(
    restaurant_name: str,
    sources: List[ReviewSource],
    signals: Dict[str, Any],
) -> Dict[str, Any]:
    if not sources:
        return {
            "summary": "目前找不到足夠的公開評價來源，建議先以菜單、價格與現場狀況作判斷。",
            "pros": [],
            "cons": ["公開評價資料不足"],
            "recommendedFor": [],
        }

    sentiment = signals.get("sentiment")
    if sentiment == "positive":
        summary = "公開來源整體偏正面，但仍建議搭配風險提示與來源內容一起判斷。"
    elif sentiment == "negative":
        summary = "公開來源出現較多負面或保留意見，建議點餐前先查看近期來源。"
    elif sentiment == "mixed":
        summary = "公開來源評價有好有壞，適合先確認在意的餐點、價格與等待時間。"
    else:
        summary = "公開來源訊號有限，暫時只能提供保守參考。"

    pros = []
    if signals.get("positiveHits", 0) > 0:
        pros.append("有部分來源提到正面用餐體驗")
    if signals.get("specificFoodMentions", 0) > 0:
        pros.append("來源中有提到具體餐點")

    cons = []
    if signals.get("negativeHits", 0) > 0:
        cons.append("有來源提到負面或需留意的體驗")
    if signals.get("riskReasons"):
        cons.append("評價可信度有需要保守看待的訊號")

    return {
        "summary": summary,
        "pros": pros,
        "cons": cons or ["資料仍需搭配來源人工確認"],
        "recommendedFor": ["想快速比較餐廳口碑的客人"],
    }


def _build_report(
    restaurant_name: str,
    sources: List[ReviewSource],
    signals: Dict[str, Any],
    summary: Dict[str, Any],
) -> ReviewReport:
    has_sources = bool(sources)
    return _normalize_report(
        {
            "success": has_sources,
            "message": "評價已更新" if has_sources else "找不到足夠的公開評價來源",
            "restaurantName": restaurant_name,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "overallScore": signals.get("overallScore", 0),
            "sentiment": signals.get("sentiment", "unknown"),
            "summary": summary.get("summary", ""),
            "pros": summary.get("pros", []),
            "cons": summary.get("cons", []),
            "recommendedFor": summary.get("recommendedFor", []),
            "riskLevel": signals.get("riskLevel", "low"),
            "riskReasons": signals.get("riskReasons", []),
            "sources": sources,
        },
        restaurant_name,
        success=True,
    )


def _normalize_report(data: Dict[str, Any], restaurant_name: str, success: bool) -> ReviewReport:
    return {
        "success": bool(data.get("success", success)),
        "message": str(data.get("message") or ("評價已更新" if success else "尚未更新評價")),
        "restaurantName": str(data.get("restaurantName") or restaurant_name),
        "updatedAt": data.get("updatedAt"),
        "overallScore": int(data.get("overallScore") or 0),
        "sentiment": data.get("sentiment") if data.get("sentiment") in {"positive", "mixed", "negative", "unknown"} else "unknown",
        "summary": str(data.get("summary") or ""),
        "pros": _string_list(data.get("pros"), [], limit=4),
        "cons": _string_list(data.get("cons"), [], limit=4),
        "recommendedFor": _string_list(data.get("recommendedFor"), [], limit=3),
        "riskLevel": data.get("riskLevel") if data.get("riskLevel") in {"low", "medium", "high"} else "low",
        "riskReasons": _string_list(data.get("riskReasons"), [], limit=6),
        "sources": [
            {
                "title": str(s.get("title", ""))[:120],
                "url": str(s.get("url", "")),
                "excerpt": str(s.get("excerpt", ""))[:500],
                "sourceType": str(s.get("sourceType", "web")),
            }
            for s in data.get("sources", [])
            if isinstance(s, dict)
        ],
    }
