"""Model gateway and validated restaurant recommendation planner.

The public functions in this module intentionally stay compatible with the old
implementation: ``chat``, ``vision_chat``, ``recommend`` and ``ensure_daemon``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib import error, request


def _load_env_file() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("$env:"):
                line = line[len("$env:") :]
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and value and not os.environ.get(key):
                os.environ[key] = value
    except Exception:
        pass


_load_env_file()

DEFAULT_MODEL = os.getenv("API_MODEL") or os.getenv("AI_MODEL") or os.getenv("MODEL") or "mistral-small-4"
VISION_MODEL = os.getenv("VISION_MODEL", "gemma-4-31b")
OLLAMA_BIN = os.getenv("OLLAMA_BIN", "ollama")
API_BASE_URL = os.getenv("API_BASE_URL", "").rstrip("/")
API_KEY = os.getenv("API_KEY", "")
_DAEMON_SPAWNED = False


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def ensure_daemon() -> None:
    """Start local Ollama once when the remote API is not configured."""
    global _DAEMON_SPAWNED
    if _DAEMON_SPAWNED or API_BASE_URL:
        return
    if not shutil.which(OLLAMA_BIN):
        raise RuntimeError(f"找不到 ollama 可執行檔（目前：{OLLAMA_BIN}）")
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | 0x00000008
    subprocess.Popen([OLLAMA_BIN, "serve"], **kwargs)
    time.sleep(0.3)
    _DAEMON_SPAWNED = True


def _cli_run(model: str, prompt: str, timeout: float) -> str:
    ensure_daemon()
    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(
        [OLLAMA_BIN, "run", model],
        input=prompt.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        **kwargs,
    )
    if proc.returncode:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip())
    return proc.stdout.decode("utf-8", errors="replace").strip()


def _api_chat(
    messages: List[Dict[str, Any]],
    model: str,
    *,
    timeout: float = 180.0,
    temperature: Optional[float] = None,
) -> str:
    url = API_BASE_URL
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": _float_env("API_TEMPERATURE", 0.7) if temperature is None else float(temperature),
    }
    req = request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"model API request failed: HTTP {exc.code} {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"model API request failed: {exc.reason}") from exc
    obj = json.loads(body)
    choices = obj.get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"model API returned no text: {body[:500]}")
    return content.strip()


def _messages_prompt(messages: Sequence[Dict[str, Any]]) -> str:
    return "\n".join(f"[{m.get('role', 'user')}] {m.get('content', '')}" for m in messages)


def chat(
    messages: List[Dict[str, Any]],
    model: Optional[str] = None,
    timeout: float = 180.0,
    temperature: Optional[float] = None,
) -> str:
    """Text chat. An explicitly supplied model always wins over API_MODEL."""
    selected_model = model or DEFAULT_MODEL
    if API_BASE_URL and API_KEY:
        return _api_chat(messages, selected_model, timeout=timeout, temperature=temperature)
    return _cli_run(selected_model, _messages_prompt(messages), timeout)


def vision_chat(
    prompt: str,
    image_url: str | Sequence[str],
    model: Optional[str] = None,
    timeout: float = 180.0,
    temperature: Optional[float] = None,
) -> str:
    """Call an OpenAI-compatible vision model with one or more images."""
    if not API_BASE_URL or not API_KEY:
        raise RuntimeError("vision model requires API_BASE_URL and API_KEY")
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    urls = [image_url] if isinstance(image_url, str) else list(image_url)
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in urls)
    return _api_chat(
        [{"role": "user", "content": content}],
        model or VISION_MODEL,
        timeout=timeout,
        temperature=_float_env("VISION_TEMPERATURE", 0.0) if temperature is None else temperature,
    )


def _extract_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        pass
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text or "", flags=re.I | re.S)
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char not in "[{":
            continue
        try:
            return decoder.raw_decode(cleaned[index:])[0]
        except json.JSONDecodeError:
            continue
    return None


_PROTEIN_KEYWORDS = {
    "beef": ("牛", "菲力", "沙朗", "紐約客", "丁骨"),
    "pork": ("豬", "松阪"),
    "chicken": ("雞",),
    "seafood": ("魚", "鮭", "鱈", "鯖", "蝦", "中卷", "花枝", "魷", "海鮮"),
}
_MAIN_KEYWORDS = (
    "牛排", "豬排", "雞排", "魚排", "排餐", "菲力", "沙朗", "紐約客", "丁骨",
    "鮭魚", "鱈魚", "鯖魚", "中卷", "漢堡", "義大利麵", "燉飯", "飯", "麵", "鍋",
)
_SIDE_KEYWORDS = ("薯條", "沙拉", "濃湯", "吐司", "麵包", "小菜", "時蔬", "單點")
_DRINK_KEYWORDS = ("茶", "咖啡", "可樂", "汽水", "果汁", "啤酒", "飲料", "飲品")
_DESSERT_KEYWORDS = ("蛋糕", "布丁", "冰淇淋", "甜點", "奶酪")


def _protein(name: str) -> str:
    for protein, keywords in _PROTEIN_KEYWORDS.items():
        if any(word in name for word in keywords):
            return protein
    return "other"


def _item_type(name: str, category: str) -> str:
    text = f"{category} {name}"
    if any(word in text for word in _DRINK_KEYWORDS):
        return "drink"
    if any(word in text for word in _DESSERT_KEYWORDS):
        return "dessert"
    if any(word in text for word in _SIDE_KEYWORDS):
        return "side"
    if any(word in text for word in _MAIN_KEYWORDS) or _protein(text) != "other":
        return "main"
    return "other"


def _numeric_price(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    match = re.search(r"\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def _flatten_menu(menu: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(menu.get("restaurants"), dict):
        restaurant_entries = menu["restaurants"].values()
        category_sources = []
        for restaurant in restaurant_entries:
            if isinstance(restaurant, dict):
                category_sources.append(restaurant.get("categories", {}))
    else:
        category_sources = [menu.get("categories", [])]
    for source in category_sources:
        if isinstance(source, dict):
            categories = [(str(name), value.get("items", []) if isinstance(value, dict) else []) for name, value in source.items()]
        elif isinstance(source, list):
            categories = [(str(cat.get("name") or "其他"), cat.get("items", [])) for cat in source if isinstance(cat, dict)]
        else:
            categories = []
        for category, items in categories:
            for raw in items if isinstance(items, list) else []:
                if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
                    continue
                name = str(raw["name"]).strip()
                rows.append({
                    "id": f"item-{len(rows) + 1:03d}",
                    "name": name,
                    "price": _numeric_price(raw.get("price")),
                    "category": category,
                    "type": _item_type(name, category),
                    "protein": _protein(f"{category} {name}"),
                })
    return rows


def build_menu_profile(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    mains = [item for item in items if item["type"] == "main"]
    proteins = {key: sum(item["protein"] == key for item in mains) for key in _PROTEIN_KEYWORDS}
    priced = sorted(item["price"] for item in mains if item["price"] is not None)
    dominant = max(proteins, key=proteins.get) if mains and max(proteins.values(), default=0) else "other"
    steakhouse = proteins["beef"] >= max(3, round(len(mains) * 0.35))
    signatures = sorted(mains, key=lambda item: _signature_score(item, steakhouse), reverse=True)[:5]
    return {
        "mainCount": len(mains),
        "dominantProtein": dominant,
        "isSteakhouse": steakhouse,
        "proteinCounts": proteins,
        "priceBand": [priced[0], priced[-1]] if priced else [None, None],
        "signatureItemIds": [item["id"] for item in signatures],
    }


def _signature_score(item: Dict[str, Any], steakhouse: bool) -> tuple:
    name = item["name"]
    signature_word = int(any(word in name for word in ("招牌", "犇頂", "本店", "特選", "經典")))
    beef_bonus = int(steakhouse and item["protein"] == "beef")
    main_bonus = int(item["type"] == "main")
    has_price = int(item["price"] is not None)
    # Stable final tie-breaker: menu order (smaller ID first).
    return signature_word, beef_bonus, main_bonus, has_price, -int(item["id"].split("-")[-1])


def _intent(user_text: str, prefs: Dict[str, Any]) -> Dict[str, Any]:
    text = user_text.casefold()
    excludes = [str(value).casefold() for value in prefs.get("excludes", []) if str(value).strip()]
    no_beef = any(word in text for word in ("不吃牛", "不要牛", "無牛")) or any("牛" in word for word in excludes)
    seafood = any(word in text for word in ("海鮮", "中卷", "魚", "蝦", "鮭", "鱈", "鯖"))
    pork = any(word in text for word in ("豬排", "豬肉"))
    chicken = any(word in text for word in ("雞排", "雞肉"))
    share = any(word in text for word in ("分享", "分著吃", "雙拼", "拼盤", "多份"))
    people = prefs.get("people") if isinstance(prefs.get("people"), int) else 1
    return {
        "noBeef": no_beef,
        "requestedProtein": "seafood" if seafood else "pork" if pork else "chicken" if chicken else None,
        "share": share,
        "people": max(1, int(people)),
        "budget": _numeric_price(prefs.get("budget")),
        "excludes": excludes,
    }


def _is_allowed(item: Dict[str, Any], intent: Dict[str, Any], profile: Dict[str, Any]) -> bool:
    text = item["name"].casefold()
    if intent["noBeef"] and item["protein"] == "beef":
        return False
    if any(exclude and exclude in text for exclude in intent["excludes"]):
        return False
    requested = intent["requestedProtein"]
    if requested and item["protein"] != requested:
        return False
    if not requested and profile["isSteakhouse"] and not intent["noBeef"] and item["protein"] != "beef":
        return False
    if (
        not requested
        and profile["isSteakhouse"]
        and not intent["noBeef"]
        and item["id"] not in set(profile["signatureItemIds"])
    ):
        return False
    budget = intent["budget"]
    if budget is not None and (item["price"] is None or item["price"] > budget):
        return False
    return item["type"] == "main"


def _deterministic_plan(items: List[Dict[str, Any]], intent: Dict[str, Any], profile: Dict[str, Any]) -> List[str]:
    candidates = [item for item in items if _is_allowed(item, intent, profile)]
    if not candidates and intent["budget"] is not None:
        relaxed = {**intent, "budget": None}
        candidates = [item for item in items if _is_allowed(item, relaxed, profile)]
    if not candidates:
        candidates = [item for item in items if item["type"] == "main" and not (intent["noBeef"] and item["protein"] == "beef")]
    signature_ids = set(profile["signatureItemIds"])
    candidates.sort(
        key=lambda item: (
            item["id"] in signature_ids,
            _signature_score(item, profile["isSteakhouse"]),
            -(item["price"] if item["price"] is not None else 10**9),
        ),
        reverse=True,
    )
    count = intent["people"] if intent["people"] > 1 else (2 if intent["share"] else 1)
    return [item["id"] for item in candidates[:count]]


def _llm_plan(items: List[Dict[str, Any]], intent: Dict[str, Any], profile: Dict[str, Any], user_text: str) -> List[str]:
    planner_model = os.getenv("RECOMMEND_MODEL", "mistral-small-4")
    public_items = [{key: item[key] for key in ("id", "name", "price", "category", "type", "protein")} for item in items]
    prompt = f"""你是餐廳點餐規劃器。只能從菜單 JSON 選擇 item ID，不得創造品項或價格。
使用者：{user_text}
解析意圖：{json.dumps(intent, ensure_ascii=False)}
菜單輪廓：{json.dumps(profile, ensure_ascii=False)}
完整菜單：{json.dumps(public_items, ensure_ascii=False)}

規則：一人預設只選一份主餐；多人每人一份。只有明確分享或雙拼才可為一人選兩份。
牛排館的一般晚餐推薦優先招牌牛排；不吃牛時排除牛，明確要海鮮時才優先中卷或魚排。
預算是整體上限。只回傳 JSON：{{"item_ids":["item-001"],"reason":"..."}}"""
    response = chat(
        [{"role": "user", "content": prompt}],
        model=planner_model,
        timeout=60.0,
        temperature=_float_env("RECOMMEND_TEMPERATURE", 0.2),
    )
    parsed = _extract_json(response)
    ids = parsed.get("item_ids", []) if isinstance(parsed, dict) else []
    return [str(value) for value in ids if isinstance(value, str)]


def recommend(
    menu: Dict[str, Any],
    prefs: Optional[Dict[str, Any]] = None,
    top_k: int = 5,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a model-planned, programmatically validated recommendation."""
    del model  # Task-specific model routing is controlled by RECOMMEND_MODEL.
    prefs = prefs or {}
    items = _flatten_menu(menu)
    if not items:
        return {"items": [], "notes": "菜單沒有可推薦的品項", "meta": {}}
    profile = build_menu_profile(items)
    user_text = str(prefs.get("notes") or "推薦我晚餐")
    intent = _intent(user_text, prefs)
    fallback_ids = _deterministic_plan(items, intent, profile)
    selected_ids: List[str] = []
    planner = "deterministic"
    try:
        proposed_ids = _llm_plan(items, intent, profile, user_text)
        by_id = {item["id"]: item for item in items}
        max_count = intent["people"] if intent["people"] > 1 else (2 if intent["share"] else 1)
        running_total = 0.0
        for item_id in proposed_ids:
            item = by_id.get(item_id)
            if not item or item_id in selected_ids or not _is_allowed(item, intent, profile):
                continue
            price = item["price"]
            if intent["budget"] is not None and (price is None or running_total + price > intent["budget"]):
                continue
            selected_ids.append(item_id)
            running_total += price or 0.0
            if len(selected_ids) >= min(max_count, top_k):
                break
        if selected_ids:
            planner = "mistral-small-4"
    except Exception as exc:
        print(f"[推薦] 主要模型不可用，使用 deterministic 評分：{exc}")
    if not selected_ids:
        selected_ids = fallback_ids[:top_k]
    by_id = {item["id"]: item for item in items}
    chosen = []
    for item_id in selected_ids:
        item = by_id.get(item_id)
        if not item:
            continue
        chosen.append({
            "id": item["id"],
            "name": item["name"],
            "price": item["price"],
            "category": item["category"],
            "type": "main",
            "protein": item["protein"],
            "reason": "符合目前需求且已通過菜單、預算與忌口驗證",
        })
    return {
        "items": chosen,
        "notes": "" if chosen else "沒有符合預算或忌口的主餐",
        "meta": {
            "budget": intent["budget"],
            "people": intent["people"],
            "needDrink": bool(prefs.get("needDrink", False)),
            "priceComplete": bool(chosen) and all(item["price"] is not None for item in chosen),
            "planner": planner,
            "profile": profile,
        },
    }
