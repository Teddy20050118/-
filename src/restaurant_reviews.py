from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from difflib import SequenceMatcher
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request
from urllib.parse import parse_qs, quote_plus, urlencode, unquote, urlparse, urlunparse


ReviewReport = Dict[str, Any]
ReviewSource = Dict[str, Any]


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


# Review intelligence schema v2. The search adapters above remain intentionally
# reusable; this layer adds identity confirmation, source quality and evidence.
SCHEMA_VERSION = 2
ASPECT_DEFINITIONS = {
    "taste": {
        "label": "口味",
        "weight": 0.30,
        "keywords": ["好吃", "美味", "味道", "口味", "新鮮", "油膩", "難吃", "餐點", "料理"],
    },
    "value": {
        "label": "價格",
        "weight": 0.20,
        "keywords": [
            "價格", "價位", "便宜", "划算", "昂貴", "太貴", "CP值", "消費",
            "套餐", "人均", "低消", "元",
        ],
    },
    "service": {
        "label": "服務",
        "weight": 0.15,
        "keywords": ["服務", "店員", "態度", "親切", "招呼", "出餐"],
    },
    "environment": {
        "label": "環境",
        "weight": 0.15,
        "keywords": ["環境", "座位", "乾淨", "整潔", "吵", "舒適", "停車", "裝潢"],
    },
    "portion": {
        "label": "份量",
        "weight": 0.10,
        "keywords": [
            "份量", "分量", "吃飽", "很少", "很多", "大份", "小份", "吃到飽", "自助吧",
        ],
    },
    "waitTime": {
        "label": "等候時間",
        "weight": 0.10,
        "keywords": [
            "等很久", "等待", "排隊", "候位", "出餐快", "出餐慢", "訂位", "尖峰",
            "客滿", "人多", "限時", "用餐時間", "預約",
        ],
    },
}
POSITIVE_TERMS = [
    "好吃", "美味", "新鮮", "划算", "便宜", "親切", "乾淨", "舒適", "推薦",
    "值得", "快速", "充足", "很大", "方便", "回訪",
]
NEGATIVE_TERMS = [
    "難吃", "普通", "油膩", "不新鮮", "太貴", "昂貴", "態度差", "髒", "吵",
    "等很久", "出餐慢", "很少", "失望", "踩雷", "不值",
]
REVIEW_PLATFORM_HOSTS = {
    "google.com", "www.google.com", "maps.google.com", "ubereats.com", "www.ubereats.com",
    "foodpanda.com.tw", "www.foodpanda.com.tw", "inline.app", "www.inline.app",
    "openrice.com", "tw.openrice.com",
}
AGGREGATOR_MARKERS = [
    "footinder", "timetables.tw", "gotoformosa", "ifoodie", "restaurantguru",
    "wanderlog", "tripadvisor",
]
TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "ref_src", "source",
}


def _v2_empty_report(
    restaurant_name: str,
    message: str = "尚未更新評價",
    identity: Optional[Dict[str, Any]] = None,
) -> ReviewReport:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "success": False,
        "message": message,
        "restaurantName": restaurant_name,
        "restaurantIdentity": identity,
        "needsIdentity": identity is None,
        "needsRefresh": False,
        "updatedAt": None,
        "recommendationScore": 0,
        "confidenceScore": 0,
        "overallScore": 0,
        "scoreBasis": "aspects",
        "platformRating": None,
        "sentiment": "unknown",
        "summary": "",
        "pros": [],
        "cons": [],
        "prosEvidence": [],
        "consEvidence": [],
        "recommendedFor": [],
        "aspects": _empty_aspects(),
        "riskLevel": "unknown",
        "riskReasons": ["資料不足，尚無法判斷評價可信度"],
        "riskSignals": [],
        "evidence": [],
        "sources": [],
    }


def _empty_aspects() -> Dict[str, Dict[str, Any]]:
    return {
        key: {
            "label": spec["label"],
            "score": None,
            "confidence": 0,
            "mentionCount": 0,
            "evidenceIds": [],
            "status": "insufficient",
        }
        for key, spec in ASPECT_DEFINITIONS.items()
    }


def _identity_id(name: str, address: str) -> str:
    raw = f"{name.strip().lower()}|{address.strip().lower()}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _normalize_identity(value: Any, fallback_name: str) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    name = str(value.get("officialName") or value.get("name") or fallback_name).strip()
    address = str(value.get("address") or "").strip()
    if not name:
        return None
    maps_url = str(value.get("mapsUrl") or "").strip()
    if not maps_url:
        maps_url = (
            "https://www.google.com/maps/search/?api=1&query="
            + quote_plus(" ".join(part for part in [name, address] if part))
        )
    return {
        "identityId": str(value.get("identityId") or _identity_id(name, address)),
        "officialName": name[:120],
        "queryName": str(value.get("queryName") or fallback_name)[:120],
        "address": address[:180],
        "mapsUrl": maps_url,
        "website": str(value.get("website") or ""),
        "evidence": str(value.get("evidence") or "")[:500],
        "confidence": max(0, min(100, int(value.get("confidence") or 0))),
        "confirmed": bool(value.get("confirmed", True)),
    }


def _extract_address(text: str) -> str:
    clean = re.sub(r"\s+", " ", text)
    patterns = [
        r"(?:\d{3}\s*)?(?:台|臺)[北中南東][市縣][^,，。|]{1,36}?(?:路|街|大道|巷)(?:[一二三四五六七八九十\d]+段)?\s*\d+(?:之\d+)?號",
        r"(?:\d{3}\s*)?(?:台|臺)灣[^,，。|]{2,40}?(?:路|街|大道|巷)(?:[一二三四五六七八九十\d]+段)?\s*\d+(?:之\d+)?號",
    ]
    for pattern in patterns:
        match = re.search(pattern, clean)
        if match:
            return match.group(0).strip()
    return ""


def _candidate_name(title: str, restaurant_name: str) -> str:
    cleaned = re.sub(r"\s*[-|｜].*$", "", title).strip()
    cleaned = re.sub(r"(地址|電話|菜單|評價|食記|訂位|營業時間).*$", "", cleaned).strip(" -|｜")
    if (
        len(cleaned) < 2
        or len(cleaned) > max(30, len(restaurant_name) * 2)
        or any(marker in cleaned for marker in ["【", "】", "？", "?", "怎樣", "推薦懶人包"])
    ):
        return restaurant_name
    return cleaned


def identify_restaurant_candidates(restaurant_name: str) -> List[Dict[str, Any]]:
    name = restaurant_name.replace("_", " ").strip()
    if not name:
        return []
    queries = [f"{name} 地址", f"{name} Google Maps 分店"]
    sources = _collect_review_sources_via_html_search(queries, set())
    candidates: List[Dict[str, Any]] = []
    seen: set[str] = set()
    normalized_target = re.sub(r"\W+", "", name).lower()

    for source in sources:
        text = f"{source.get('title', '')} {source.get('excerpt', '')}"
        candidate_name = _candidate_name(str(source.get("title", "")), name)
        address = _extract_address(text)
        normalized_candidate = re.sub(r"\W+", "", candidate_name).lower()
        similarity = SequenceMatcher(None, normalized_target, normalized_candidate).ratio()
        if name not in text and similarity < 0.35:
            continue
        compact_address = re.sub(r"\s+", "", address)
        key = f"{normalized_candidate}|{compact_address}"
        if key in seen:
            continue
        seen.add(key)
        query = " ".join(part for part in [candidate_name, address] if part)
        candidates.append(
            {
                "identityId": _identity_id(candidate_name, address),
                "officialName": candidate_name,
                "address": address,
                "mapsUrl": f"https://www.google.com/maps/search/?api=1&query={quote_plus(query)}",
                "website": str(source.get("url") or ""),
                "confidence": min(95, round(45 + similarity * 35 + (15 if address else 0))),
                "evidence": str(source.get("excerpt") or "")[:220],
            }
        )
        if len(candidates) >= 5:
            break

    candidates.sort(
        key=lambda item: (
            bool(item.get("address")),
            int(item.get("confidence") or 0),
        ),
        reverse=True,
    )
    if not candidates:
        candidates.append(
            {
                "identityId": _identity_id(name, ""),
                "officialName": name,
                "address": "",
                "mapsUrl": f"https://www.google.com/maps/search/?api=1&query={quote_plus(name)}",
                "website": "",
                "confidence": 20,
                "evidence": "搜尋不到明確地址，請先由 Google Maps 連結確認店家。",
            }
        )
    return candidates


def _canonicalize_url(url: str) -> str:
    parsed = urlparse(_clean_search_url(url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    query = [
        (key, value)
        for key, values in parse_qs(parsed.query, keep_blank_values=True).items()
        if key.lower() not in TRACKING_QUERY_KEYS
        for value in values
    ]
    path = re.sub(r"/+$", "", parsed.path) or "/"
    return urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, "", urlencode(query), "")
    )


def _source_type_and_quality(url: str, title: str) -> tuple[str, float]:
    host = urlparse(url).netloc.lower()
    haystack = f"{host} {title}".lower()
    if any(host == item or host.endswith("." + item) for item in REVIEW_PLATFORM_HOSTS):
        return "review_platform", 0.90
    if any(site in haystack for site in ["dcard", "ptt", "mobile01", "forum"]):
        return "forum", 0.80
    if any(marker in haystack for marker in AGGREGATOR_MARKERS):
        return "aggregator", 0.45
    if any(site in haystack for site in ["blog", "pixnet", "wordpress", "medium", "痞客邦"]):
        return "blog", 0.68
    return "web", 0.62


def _parse_date(value: str) -> Optional[str]:
    if not value:
        return None
    match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", value)
    if not match:
        return None
    try:
        return datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=timezone.utc
        ).isoformat()
    except ValueError:
        return None


def _extract_page(url: str) -> Dict[str, Any]:
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
    with request.urlopen(req, timeout=12) as resp:
        raw = resp.read(1_500_000)
        charset = resp.headers.get_content_charset() or "utf-8"
        content_type = resp.headers.get("Content-Type", "")
    if "html" not in content_type.lower():
        raise ValueError("來源不是 HTML")
    html = raw.decode(charset, errors="replace")

    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    canonical_url = canonical.get("href", "") if canonical else ""
    date_value = ""
    for key, attr in [
        ("article:published_time", "property"),
        ("datePublished", "itemprop"),
        ("date", "name"),
        ("pubdate", "name"),
    ]:
        node = soup.find("meta", attrs={attr: key})
        if node and node.get("content"):
            date_value = str(node.get("content"))
            break
    for node in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        node.decompose()
    container = soup.find("article") or soup.find("main") or soup.body
    text = " ".join(container.stripped_strings) if container else ""
    text = re.sub(r"\s+", " ", text)[:12000]
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    return {
        "title": title[:160],
        "content": text,
        "publishedAt": _parse_date(date_value) or _parse_date(text[:1200]),
        "canonicalUrl": canonical_url,
    }


def _enrich_source(source: ReviewSource, identity: Dict[str, Any]) -> ReviewSource:
    now = datetime.now(timezone.utc).isoformat()
    url = _canonicalize_url(str(source.get("url") or ""))
    title = str(source.get("title") or "")[:160]
    excerpt = re.sub(r"\s+", " ", str(source.get("excerpt") or ""))[:700]
    source_type, quality = _source_type_and_quality(url, title)
    result: ReviewSource = {
        "sourceId": "",
        "title": title,
        "url": url,
        "canonicalUrl": url,
        "excerpt": excerpt,
        "content": excerpt,
        "sourceType": source_type,
        "publishedAt": _parse_date(f"{title} {excerpt}"),
        "retrievedAt": now,
        "sourceQuality": quality,
        "sponsored": False,
        "duplicateOf": None,
        "fetchStatus": "snippet",
    }
    try:
        page = _extract_page(url)
        result["title"] = page.get("title") or title
        result["content"] = page.get("content") or excerpt
        result["publishedAt"] = page.get("publishedAt") or result["publishedAt"]
        canonical = _canonicalize_url(str(page.get("canonicalUrl") or ""))
        if canonical:
            result["canonicalUrl"] = canonical
            result["url"] = canonical
        result["fetchStatus"] = "full"
    except Exception:
        pass

    combined = f"{result['title']} {result['excerpt']} {result['content']}"
    result["sponsored"] = any(keyword in combined for keyword in SPONSORED_KEYWORDS)
    if result["sponsored"]:
        result["sourceQuality"] = round(float(result["sourceQuality"]) * 0.45, 3)
    host = urlparse(str(result["url"])).netloc
    source_key = f"{result['canonicalUrl']}|{host}|{result['title']}"
    result["sourceId"] = "SRC-" + hashlib.sha1(source_key.encode("utf-8")).hexdigest()[:10]
    return result


def _source_relevant(source: ReviewSource, identity: Dict[str, Any]) -> bool:
    title = str(source.get("title") or "")
    content = f"{title} {source.get('excerpt', '')} {source.get('content', '')[:1000]}"
    lowered = content.lower()
    if any(
        marker in lowered
        for marker in ["人力銀行", "職缺", "徵才", "服務員(兼職)", "104.com", "1111.com"]
    ):
        return False
    names = [
        str(identity.get("officialName") or ""),
        str(identity.get("queryName") or ""),
    ]
    tokens = []
    for name in names:
        tokens.extend(
            token
            for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", name)
            if token not in {"台中", "餐廳", "分店"}
        )
    if not tokens:
        return True
    if any(token.lower() in lowered for token in tokens):
        return True
    compact_title = re.sub(r"\W+", "", title).lower()
    compact_name = re.sub(r"\W+", "", str(identity.get("officialName") or "")).lower()
    return bool(compact_title and compact_name) and SequenceMatcher(
        None, compact_title, compact_name
    ).ratio() >= 0.32


def _content_fingerprint(source: ReviewSource) -> str:
    text = str(source.get("content") or source.get("excerpt") or "")
    return re.sub(r"[\W_]+", "", text.lower())[:1000]


def _mark_duplicates(sources: List[ReviewSource]) -> None:
    canonical_seen: Dict[str, str] = {}
    unique: List[ReviewSource] = []
    for source in sources:
        canonical = str(source.get("canonicalUrl") or source.get("url") or "")
        if canonical in canonical_seen:
            source["duplicateOf"] = canonical_seen[canonical]
            source["sourceQuality"] = 0.0
            continue
        fingerprint = _content_fingerprint(source)
        duplicate_id = None
        if len(fingerprint) >= 80:
            for previous in unique:
                previous_fp = _content_fingerprint(previous)
                if previous_fp and SequenceMatcher(None, fingerprint, previous_fp).ratio() >= 0.88:
                    duplicate_id = str(previous.get("sourceId"))
                    break
        if duplicate_id:
            source["duplicateOf"] = duplicate_id
            source["sourceQuality"] = 0.0
        else:
            canonical_seen[canonical] = str(source.get("sourceId"))
            unique.append(source)


def _collect_enriched_sources(identity: Dict[str, Any]) -> List[ReviewSource]:
    official_name = str(identity.get("officialName") or "").strip()
    query_name = str(identity.get("queryName") or "").strip()
    search_names = [
        official_name.replace("_", " "),
        query_name.replace("_", " "),
        official_name,
        query_name,
        " ".join(
            part for part in [identity.get("officialName"), identity.get("address")] if part
        ).strip(),
    ]
    raw_sources: List[ReviewSource] = []
    seen_urls: set[str] = set()
    website = _canonicalize_url(str(identity.get("website") or ""))
    if website:
        seen_urls.add(website)
        raw_sources.append(
            {
                "title": str(identity.get("officialName") or ""),
                "url": website,
                "excerpt": str(identity.get("evidence") or identity.get("officialName") or ""),
                "sourceType": "web",
            }
        )
    for search_name in dict.fromkeys(name for name in search_names if name):
        for source in asyncio.run(_collect_review_sources(search_name)):
            url = _canonicalize_url(str(source.get("url") or ""))
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            raw_sources.append(source)
            if len(raw_sources) >= 20:
                break
        if len(raw_sources) >= 20:
            break
    enriched: List[ReviewSource] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_enrich_source, source, identity) for source in raw_sources]
        for future in as_completed(futures):
            try:
                source = future.result()
                if source.get("url") and _source_relevant(source, identity):
                    enriched.append(source)
            except Exception:
                continue
    enriched.sort(key=lambda item: float(item.get("sourceQuality") or 0), reverse=True)
    _mark_duplicates(enriched)
    return enriched[:15]


def _recency_weight(published_at: Any) -> float:
    if not published_at:
        return 0.60
    try:
        date = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        age_days = max(0, (datetime.now(timezone.utc) - date).days)
    except Exception:
        return 0.60
    if age_days <= 183:
        return 1.0
    if age_days <= 548:
        return 0.85
    if age_days <= 1095:
        return 0.65
    return 0.40


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[。！？!?])|\s*[|｜]\s*", re.sub(r"\s+", " ", text))
    return [part.strip() for part in parts if 8 <= len(part.strip()) <= 220]


def _polarity(sentence: str) -> str:
    positive = sum(term in sentence for term in POSITIVE_TERMS)
    negative = sum(term in sentence for term in NEGATIVE_TERMS)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


def _extract_rule_evidence(sources: List[ReviewSource]) -> List[Dict[str, Any]]:
    evidence: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        if source.get("duplicateOf"):
            continue
        text = str(source.get("content") or source.get("excerpt") or "")
        for sentence in _split_sentences(text):
            for aspect, spec in ASPECT_DEFINITIONS.items():
                if not any(keyword.lower() in sentence.lower() for keyword in spec["keywords"]):
                    continue
                key = (str(source.get("sourceId")), sentence[:100])
                if key in seen:
                    continue
                seen.add(key)
                evidence.append(
                    {
                        "evidenceId": f"EV-{len(evidence) + 1:03d}",
                        "sourceId": source.get("sourceId"),
                        "aspect": aspect,
                        "polarity": _polarity(sentence),
                        "text": sentence[:220],
                        "weight": round(
                            float(source.get("sourceQuality") or 0)
                            * _recency_weight(source.get("publishedAt")),
                            3,
                        ),
                    }
                )
                if len(evidence) >= 60:
                    return evidence
    return evidence


def _calculate_aspects(evidence: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result = _empty_aspects()
    polarity_scores = {"positive": 82, "neutral": 55, "negative": 28}
    for aspect in ASPECT_DEFINITIONS:
        rows = [item for item in evidence if item.get("aspect") == aspect]
        source_ids = {str(item.get("sourceId")) for item in rows if item.get("sourceId")}
        result[aspect]["mentionCount"] = len(rows)
        result[aspect]["evidenceIds"] = [str(item["evidenceId"]) for item in rows[:8]]
        if not source_ids:
            continue
        total_weight = sum(max(0.05, float(item.get("weight") or 0)) for item in rows)
        weighted_score = sum(
            polarity_scores.get(str(item.get("polarity")), 55)
            * max(0.05, float(item.get("weight") or 0))
            for item in rows
        ) / total_weight
        avg_quality = total_weight / max(1, len(rows))
        result[aspect]["score"] = round(weighted_score)
        result[aspect]["confidence"] = min(
            100, round(8 + len(source_ids) * 16 + min(30, avg_quality * 35))
        )
        result[aspect]["status"] = "supported" if len(source_ids) >= 2 else "estimated"
    return result


def _recommendation_score(aspects: Dict[str, Dict[str, Any]]) -> int:
    available = [
        (key, value)
        for key, value in aspects.items()
        if isinstance(value.get("score"), (int, float))
    ]
    if not available:
        return 0
    weight_sum = sum(float(ASPECT_DEFINITIONS[key]["weight"]) for key, _ in available)
    return round(
        sum(
            float(value["score"]) * float(ASPECT_DEFINITIONS[key]["weight"])
            for key, value in available
        )
        / weight_sum
    )


def _platform_rating(sources: List[ReviewSource]) -> Optional[Dict[str, Any]]:
    rows = []
    for source in sources:
        if source.get("duplicateOf"):
            continue
        text = f"{source.get('title', '')} {source.get('excerpt', '')} {source.get('content', '')}"
        match = re.search(r"([1-5](?:\.\d{1,2})?)\s*/\s*5", text)
        if not match:
            match = re.search(r"評分(?:為)?\s*([1-5](?:\.\d{1,2})?)\s*星", text)
        if not match:
            continue
        rating = float(match.group(1))
        count_match = re.search(r"([\d,]+)\+?\s*(?:則評論|票|votes|reviews)", text, re.I)
        rows.append(
            {
                "rating": rating,
                "reviewCount": int(count_match.group(1).replace(",", "")) if count_match else None,
                "sourceId": source.get("sourceId"),
            }
        )
    if not rows:
        return None
    return {
        "average": round(sum(row["rating"] for row in rows) / len(rows), 2),
        "ratingCount": len(rows),
        "reviewCount": max((row["reviewCount"] or 0 for row in rows), default=0) or None,
        "sourceIds": [str(row["sourceId"]) for row in rows if row.get("sourceId")],
    }


def _confidence_score(
    identity: Optional[Dict[str, Any]],
    sources: List[ReviewSource],
    aspects: Dict[str, Dict[str, Any]],
) -> int:
    unique = [source for source in sources if not source.get("duplicateOf")]
    domains = {urlparse(str(source.get("url") or "")).netloc for source in unique}
    quality = (
        sum(float(source.get("sourceQuality") or 0) for source in unique) / len(unique)
        if unique else 0
    )
    dated_ratio = (
        sum(bool(source.get("publishedAt")) for source in unique) / len(unique)
        if unique else 0
    )
    scored_aspects = sum(value.get("score") is not None for value in aspects.values())
    score = (
        (20 if identity and identity.get("confirmed") else 0)
        + min(20, len(unique) * 4)
        + min(20, len(domains) * 4)
        + quality * 20
        + dated_ratio * 10
        + scored_aspects / len(ASPECT_DEFINITIONS) * 10
    )
    return min(100, round(score))


def _risk_evidence(
    sources: List[ReviewSource],
    base_evidence: List[Dict[str, Any]],
) -> tuple[str, List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    evidence = list(base_evidence)
    signals: List[Dict[str, Any]] = []

    def add_signal(label: str, level_points: int, source: ReviewSource, text: str) -> None:
        evidence_id = f"EV-{len(evidence) + 1:03d}"
        evidence.append(
            {
                "evidenceId": evidence_id,
                "sourceId": source.get("sourceId"),
                "aspect": "credibility",
                "polarity": "risk",
                "text": text[:220],
                "weight": float(source.get("sourceQuality") or 0),
            }
        )
        signals.append({"label": label, "points": level_points, "evidenceIds": [evidence_id]})

    for source in sources:
        text = f"{source.get('excerpt', '')} {source.get('content', '')}"
        hits = [keyword for keyword in INCENTIVE_KEYWORDS if keyword in text]
        if hits:
            add_signal(
                f"找到誘導評論線索：{', '.join(sorted(set(hits)))}",
                4,
                source,
                next((sentence for sentence in _split_sentences(text) if any(hit in sentence for hit in hits)), text),
            )
        if source.get("sponsored"):
            add_signal("來源標示為業配、合作或邀約，已降低分析權重", 0, source, text)

    duplicate_sources = [source for source in sources if source.get("duplicateOf")]
    if duplicate_sources:
        source = duplicate_sources[0]
        add_signal("部分內容高度相似或可能為重複轉載", min(3, len(duplicate_sources)), source, str(source.get("excerpt") or source.get("title")))

    review_like = [
        source for source in sources
        if source.get("sourceType") in {"review_platform", "forum"} and not source.get("duplicateOf")
    ]
    points = sum(int(signal["points"]) for signal in signals)
    if points >= 6:
        level = "high"
    elif points >= 3:
        level = "medium"
    elif len(review_like) >= 3:
        level = "low"
    else:
        level = "unknown"
    reasons = [str(signal["label"]) for signal in signals]
    if level == "unknown" and not reasons:
        reasons = ["缺少足夠的逐則評論，暫時無法判斷灌水風險"]
    if level == "low" and not reasons:
        reasons = ["目前可辨識的評論中未發現明顯誘導或重複訊號"]
    return level, reasons, signals, evidence


def _sentiment_from_evidence(evidence: List[Dict[str, Any]]) -> str:
    rows = [item for item in evidence if item.get("aspect") in ASPECT_DEFINITIONS]
    positive = sum(item.get("polarity") == "positive" for item in rows)
    negative = sum(item.get("polarity") == "negative" for item in rows)
    if not positive and not negative:
        return "unknown"
    if positive >= negative * 2 + 1:
        return "positive"
    if negative >= positive * 2 + 1:
        return "negative"
    return "mixed"


def _fallback_v2_summary(
    aspects: Dict[str, Dict[str, Any]],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    scored = [(key, value) for key, value in aspects.items() if value.get("score") is not None]
    positive = sorted(scored, key=lambda item: item[1]["score"], reverse=True)
    negative = sorted(scored, key=lambda item: item[1]["score"])
    pros_evidence = []
    cons_evidence = []
    if positive and positive[0][1]["score"] >= 60:
        key, value = positive[0]
        pros_evidence.append(
            {"text": f"{value['label']}相關評價較正面", "evidenceIds": value["evidenceIds"][:3]}
        )
    if negative and negative[0][1]["score"] < 55:
        key, value = negative[0]
        cons_evidence.append(
            {"text": f"{value['label']}是較需要留意的面向", "evidenceIds": value["evidenceIds"][:3]}
        )
    if not scored:
        summary = "目前來源尚不足以形成可靠的面向分數，建議直接查看來源內容。"
    else:
        labels = "、".join(value["label"] for _, value in positive[:2])
        summary = f"目前可比較的面向以{labels}為主；請搭配資料信心與來源證據一起判斷。"
    return {
        "summary": summary,
        "prosEvidence": pros_evidence,
        "consEvidence": cons_evidence or [{"text": "部分面向資料仍有限", "evidenceIds": []}],
        "recommendedFor": ["想依公開證據比較餐廳的客人"],
    }


def _summarize_v2(
    restaurant_name: str,
    aspects: Dict[str, Dict[str, Any]],
    evidence: List[Dict[str, Any]],
) -> Dict[str, Any]:
    fallback = _fallback_v2_summary(aspects, evidence)
    usable = [item for item in evidence if item.get("aspect") in ASPECT_DEFINITIONS][:24]
    if not usable:
        return fallback
    try:
        from ollama_fuc import chat

        prompt = f"""
你是餐廳評價整理助手。只能根據以下證據輸出繁體中文 JSON，不得加入證據沒有提到的事實。
餐廳：{restaurant_name}
證據：{json.dumps(usable, ensure_ascii=False)}
面向分數：{json.dumps(aspects, ensure_ascii=False)}

只輸出 JSON：
summary: 100字內客觀摘要
prosEvidence: 最多3項，每項含 text 與 evidenceIds
consEvidence: 最多3項，每項含 text 與 evidenceIds
recommendedFor: 最多3個客群
每個 evidenceIds 只能使用上方存在的證據編號。
"""
        obj = _extract_json_object(chat([{"role": "user", "content": prompt}], timeout=45.0))
        if not obj:
            return fallback
        valid_ids = {str(item["evidenceId"]) for item in usable}

        def clean_highlights(value: Any, fallback_value: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            if not isinstance(value, list):
                return fallback_value
            rows = []
            for item in value[:3]:
                if not isinstance(item, dict) or not str(item.get("text") or "").strip():
                    continue
                ids = [str(item_id) for item_id in item.get("evidenceIds", []) if str(item_id) in valid_ids]
                rows.append({"text": str(item["text"])[:100], "evidenceIds": ids})
            return rows or fallback_value

        return {
            "summary": str(obj.get("summary") or fallback["summary"])[:220],
            "prosEvidence": clean_highlights(obj.get("prosEvidence"), fallback["prosEvidence"]),
            "consEvidence": clean_highlights(obj.get("consEvidence"), fallback["consEvidence"]),
            "recommendedFor": _string_list(
                obj.get("recommendedFor"), fallback["recommendedFor"], limit=3
            ),
        }
    except Exception:
        return fallback


def analyze_review_signals(sources: List[ReviewSource]) -> Dict[str, Any]:
    if not sources:
        return {
            "aspects": _empty_aspects(),
            "evidence": [],
            "recommendationScore": 0,
            "overallScore": 0,
            "scoreBasis": "aspects",
            "platformRating": None,
            "confidenceScore": 0,
            "riskLevel": "unknown",
            "riskReasons": ["公開評價來源不足，暫時無法判斷可信度"],
            "riskSignals": [],
            "sentiment": "unknown",
            "incentiveHits": [],
            "duplicateCount": 0,
        }
    rule_evidence = _extract_rule_evidence(sources)
    aspects = _calculate_aspects(rule_evidence)
    risk_level, risk_reasons, risk_signals, evidence = _risk_evidence(sources, rule_evidence)
    recommendation = _recommendation_score(aspects)
    platform_rating = _platform_rating(sources)
    score_basis = "aspects"
    if recommendation == 0 and platform_rating:
        recommendation = round(float(platform_rating["average"]) * 20)
        score_basis = "platform_rating"
    return {
        "aspects": aspects,
        "evidence": evidence,
        "recommendationScore": recommendation,
        "overallScore": recommendation,
        "scoreBasis": score_basis,
        "platformRating": platform_rating,
        "confidenceScore": 0,
        "riskLevel": risk_level,
        "riskReasons": risk_reasons,
        "riskSignals": risk_signals,
        "sentiment": _sentiment_from_evidence(evidence),
        "incentiveHits": sorted(
            {
                keyword
                for source in sources
                for keyword in INCENTIVE_KEYWORDS
                if keyword in f"{source.get('excerpt', '')} {source.get('content', '')}"
            }
        ),
        "duplicateCount": sum(bool(source.get("duplicateOf")) for source in sources),
    }


def _normalize_highlights(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        {
            "text": str(item.get("text") or "")[:100],
            "evidenceIds": [str(eid) for eid in item.get("evidenceIds", [])][:8],
        }
        for item in value
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ][:4]


def _normalize_v2_report(
    data: Dict[str, Any],
    restaurant_name: str,
    default_success: bool = False,
) -> ReviewReport:
    legacy = int(data.get("schemaVersion") or 1) < SCHEMA_VERSION
    identity = _normalize_identity(data.get("restaurantIdentity"), restaurant_name)
    recommendation = (
        0
        if legacy
        else int(data.get("recommendationScore") or data.get("overallScore") or 0)
    )
    aspects = _empty_aspects()
    if isinstance(data.get("aspects"), dict):
        for key, default in aspects.items():
            incoming = data["aspects"].get(key)
            if not isinstance(incoming, dict):
                continue
            score = incoming.get("score")
            default.update(
                {
                    "label": str(incoming.get("label") or default["label"]),
                    "score": max(0, min(100, int(score))) if isinstance(score, (int, float)) else None,
                    "confidence": max(0, min(100, int(incoming.get("confidence") or 0))),
                    "mentionCount": max(0, int(incoming.get("mentionCount") or 0)),
                    "evidenceIds": [str(eid) for eid in incoming.get("evidenceIds", [])][:8],
                    "status": (
                        str(incoming.get("status"))
                        if incoming.get("status") in {"supported", "estimated", "insufficient"}
                        else ("supported" if isinstance(score, (int, float)) else "insufficient")
                    ),
                }
            )
    risk_level = "unknown" if legacy else str(data.get("riskLevel") or "unknown")
    if risk_level not in {"low", "medium", "high", "unknown"}:
        risk_level = "unknown"
    sources = []
    for index, source in enumerate(data.get("sources", [])):
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "")
        source_type, default_quality = _source_type_and_quality(url, str(source.get("title") or ""))
        sources.append(
            {
                "sourceId": str(source.get("sourceId") or f"SRC-LEGACY-{index + 1:03d}"),
                "title": str(source.get("title") or "")[:160],
                "url": url,
                "canonicalUrl": str(source.get("canonicalUrl") or url),
                "excerpt": str(source.get("excerpt") or "")[:700],
                "sourceType": str(source.get("sourceType") or source_type),
                "publishedAt": source.get("publishedAt"),
                "retrievedAt": source.get("retrievedAt") or data.get("updatedAt"),
                "sourceQuality": float(source.get("sourceQuality", default_quality)),
                "sponsored": bool(source.get("sponsored")),
                "duplicateOf": source.get("duplicateOf"),
                "fetchStatus": str(source.get("fetchStatus") or "legacy"),
            }
        )
    success = bool(data.get("success", default_success))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "success": success,
        "message": str(data.get("message") or ("評價已更新" if success else "尚未更新評價")),
        "restaurantName": str(data.get("restaurantName") or restaurant_name),
        "restaurantIdentity": identity,
        "needsIdentity": identity is None,
        "needsRefresh": bool(data.get("needsRefresh", legacy)),
        "updatedAt": data.get("updatedAt"),
        "recommendationScore": max(0, min(100, recommendation)),
        "confidenceScore": max(0, min(100, int(data.get("confidenceScore") or 0))),
        "overallScore": max(0, min(100, recommendation)),
        "scoreBasis": (
            str(data.get("scoreBasis"))
            if data.get("scoreBasis") in {"aspects", "platform_rating"}
            else "aspects"
        ),
        "platformRating": data.get("platformRating") if isinstance(data.get("platformRating"), dict) else None,
        "sentiment": data.get("sentiment") if data.get("sentiment") in {"positive", "mixed", "negative", "unknown"} else "unknown",
        "summary": str(data.get("summary") or ""),
        "pros": _string_list(data.get("pros"), [], 4),
        "cons": _string_list(data.get("cons"), [], 4),
        "prosEvidence": _normalize_highlights(data.get("prosEvidence")),
        "consEvidence": _normalize_highlights(data.get("consEvidence")),
        "recommendedFor": _string_list(data.get("recommendedFor"), [], 3),
        "aspects": aspects,
        "riskLevel": risk_level,
        "riskReasons": _string_list(data.get("riskReasons"), [], 6),
        "riskSignals": _normalize_highlights(
            [
                {"text": signal.get("label"), "evidenceIds": signal.get("evidenceIds", [])}
                for signal in data.get("riskSignals", [])
                if isinstance(signal, dict)
            ]
        ),
        "evidence": [
            {
                "evidenceId": str(item.get("evidenceId") or ""),
                "sourceId": str(item.get("sourceId") or ""),
                "aspect": str(item.get("aspect") or ""),
                "polarity": str(item.get("polarity") or "neutral"),
                "text": str(item.get("text") or "")[:220],
                "weight": float(item.get("weight") or 0),
            }
            for item in data.get("evidence", [])
            if isinstance(item, dict)
        ],
        "sources": sources,
    }


def load_review_cache(project_root: str | Path, restaurant_name: str) -> ReviewReport:
    path = _cache_path(project_root, restaurant_name)
    if not path.exists():
        return _v2_empty_report(restaurant_name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _v2_empty_report(restaurant_name, f"評價快取讀取失敗: {exc}")
    return _normalize_v2_report(data, restaurant_name, default_success=True)


def write_review_cache(project_root: str | Path, restaurant_name: str, report: ReviewReport) -> None:
    path = _cache_path(project_root, restaurant_name)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def refresh_restaurant_reviews(
    project_root: str | Path,
    restaurant_name: str,
    restaurant_identity: Optional[Dict[str, Any]] = None,
) -> ReviewReport:
    cached = load_review_cache(project_root, restaurant_name)
    identity = _normalize_identity(
        restaurant_identity or cached.get("restaurantIdentity"), restaurant_name
    )
    if restaurant_identity is None and identity and not identity.get("address"):
        normalized_identity_name = re.sub(
            r"\W+", "", str(identity.get("officialName") or "")
        ).lower()
        normalized_restaurant_name = re.sub(r"\W+", "", restaurant_name).lower()
        if normalized_identity_name == normalized_restaurant_name:
            identity = None
    if identity is None:
        candidates = identify_restaurant_candidates(restaurant_name)
        if candidates:
            top = candidates[0]
            second_confidence = (
                int(candidates[1].get("confidence") or 0) if len(candidates) > 1 else 0
            )
            top_confidence = int(top.get("confidence") or 0)
            name_similarity = SequenceMatcher(
                None,
                re.sub(r"\W+", "", restaurant_name).lower(),
                re.sub(r"\W+", "", str(top.get("officialName") or "")).lower(),
            ).ratio()
            can_auto_select = (
                top_confidence >= 70
                and (
                    bool(top.get("address"))
                    or top_confidence - second_confidence >= 8
                    or (top_confidence >= 78 and name_similarity >= 0.55)
                )
            )
            if can_auto_select:
                auto_identity = dict(top)
                auto_identity["confirmed"] = True
                auto_identity["queryName"] = restaurant_name
                identity = _normalize_identity(auto_identity, restaurant_name)
        if identity is None:
            report = _v2_empty_report(
                restaurant_name,
                "找到多個可能分店，請選擇正確店家",
                identity=None,
            )
            report["needsIdentity"] = True
            report["identityCandidates"] = candidates
            return report

    sources = _collect_enriched_sources(identity)
    signals = analyze_review_signals(sources)
    aspects = signals["aspects"]
    recommendation = int(signals["recommendationScore"])
    confidence = _confidence_score(identity, sources, aspects)
    summary = _summarize_v2(identity["officialName"], aspects, signals["evidence"])
    if signals.get("scoreBasis") == "platform_rating" and signals.get("platformRating"):
        rating = signals["platformRating"]
        review_count = rating.get("reviewCount")
        count_text = f"，約 {review_count} 則評分" if review_count else ""
        summary["summary"] = (
            f"平台顯示 {rating.get('average')}/5{count_text}；"
            "目前缺少可分析的文字內容，面向分數不做推測。"
        )
    pros_evidence = summary["prosEvidence"]
    cons_evidence = summary["consEvidence"]
    has_sources = bool(sources)
    report = _normalize_v2_report(
        {
            "schemaVersion": SCHEMA_VERSION,
            "success": has_sources,
            "message": "評價已更新" if has_sources else "找不到足夠的公開評價來源",
            "restaurantName": restaurant_name,
            "restaurantIdentity": identity,
            "needsIdentity": False,
            "needsRefresh": False,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "recommendationScore": recommendation,
            "confidenceScore": confidence,
            "overallScore": recommendation,
            "scoreBasis": signals.get("scoreBasis", "aspects"),
            "platformRating": signals.get("platformRating"),
            "sentiment": signals["sentiment"],
            "summary": summary["summary"],
            "pros": [item["text"] for item in pros_evidence],
            "cons": [item["text"] for item in cons_evidence],
            "prosEvidence": pros_evidence,
            "consEvidence": cons_evidence,
            "recommendedFor": summary["recommendedFor"],
            "aspects": aspects,
            "riskLevel": signals["riskLevel"],
            "riskReasons": signals["riskReasons"],
            "riskSignals": signals["riskSignals"],
            "evidence": signals["evidence"],
            "sources": sources,
        },
        restaurant_name,
        default_success=has_sources,
    )
    write_review_cache(project_root, restaurant_name, report)
    return report
