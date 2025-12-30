
import os, sys, json, re, shutil, subprocess, random, time
from typing import Any, Dict, List, Optional, Tuple

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SRC_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from db.db_client import query_menu
#從環境變數讀取設定
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
OLLAMA_BIN = os.getenv("OLLAMA_BIN", "ollama")

def _cli_available() -> bool:
    return shutil.which(OLLAMA_BIN) is not None #檢查路徑是否找到執行檔

# 靜默啟動 daemon
_DAEMON_SPAWNED = False
def ensure_daemon() -> None:
    global _DAEMON_SPAWNED
    if _DAEMON_SPAWNED: #避免重複啟動
        return
    if not _cli_available():
        raise RuntimeError(f"找不到 ollama 可執行檔，請設定 PATH 或 OLLAMA_BIN（目前：{OLLAMA_BIN}）。")
    kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    #Windows啟動子行程時隱藏視窗並背景執行而設計的參數設定
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            # |= (or)-> a = a or b
            creationflags |= subprocess.CREATE_NO_WINDOW
        #讓子行程與父行程的主控台分離，成為背景行程，不會跟隨父行程的主控台顯示與訊號（例如 Ctrl+C）而受影響。
        DETACHED_PROCESS = 0x00000008
        creationflags |= DETACHED_PROCESS
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = creationflags
    subprocess.Popen([OLLAMA_BIN, "serve"], **kwargs)
    time.sleep(0.3) #給服務0.3秒時間啟動
    _DAEMON_SPAWNED = True


#呼叫外部的 ollama 可執行檔並回傳結果
def _cli_run(args: List[str], input_text: Optional[str] = None, timeout: float = 120.0) -> str:
    try:
        ensure_daemon()
    except Exception:
        pass
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(
        [OLLAMA_BIN, *args],
        input=(input_text.encode("utf-8") if input_text is not None else None),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    out = proc.stdout.decode("utf-8", errors="ignore").strip()
    err = proc.stderr.decode("utf-8", errors="ignore").strip()
    if proc.returncode != 0:
        raise RuntimeError(f"ollama 命令失敗: {' '.join([OLLAMA_BIN, *args])}\n{err}")
    return out or err


#把一串對話訊息 messages組裝成一段適合丟給 CLI/文字模型的提示字串
def _build_prompt_from_messages(messages: List[Dict[str, str]]) -> str:
    parts: List[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            parts.append(f"[系統] {content}")
        elif role == "assistant":
            parts.append(f"助理: {content}")
        else:
            parts.append(f"使用者: {content}")
    parts.append("助理:")
    return "\n".join(parts)
#把多輪對話messages轉成一段提示字串，再用CLI方式呼叫Ollama
def chat(messages: List[Dict[str, str]], model: Optional[str] = None, timeout: float = 120.0) -> str:
    mdl = model or DEFAULT_MODEL
    prompt = _build_prompt_from_messages(messages)
    return _cli_run(["run", mdl], input_text=prompt, timeout=timeout)

def _extract_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"(\{.*\}|\[.*\])", text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None

def recommend(menu: Dict[str, Any], prefs: Optional[Dict[str, Any]] = None, top_k: int = 5, model: Optional[str] = None) -> Dict[str, Any]:
    """根據偏好與資料庫內容做推薦（不辣 / 要飲料 / 人數感知）。

    規則重點：
    - 不辣：排除多種「辣度」相關標籤，而不是只排除單一「辣」。
    - 要飲料：盡量在推薦清單中加入至少 1 個飲料類品項（依分類名稱關鍵字判斷）。
    - 人數：依 people 動態調整推薦數量 top_k，避免 5 個人只推薦 2 道菜的情況。
    """
    prefs = prefs or {}

    # 0) 人數感知：動態決定 top_k
    people = prefs.get("people")
    if isinstance(people, int) and people > 0:
        # 例如 1 人 → 至少 3 道；3 人 → 約 5 道；上限 8 道
        top_k = max(3, min(people + 2, 8))

    # 1) 解析偏好 → 轉成查詢條件
    budget: Optional[float] = None
    if isinstance(prefs.get("budget"), (int, float, str)):
        try:
            budget = float(prefs["budget"])  # type: ignore[index]
        except Exception:
            budget = None

    must_tags: List[str] = []  # 預留未來需要「必須包含」的標籤
    exclude_tags: List[str] = []
    if isinstance(prefs.get("excludes"), list):
        exclude_tags = [str(x) for x in prefs["excludes"]]  # type: ignore[index]

    # 不辣 → 排除常見的辣度標籤（資料驅動，可再擴充）
    SPICY_TAGS = ["辣", "小辣", "中辣", "大辣", "微辣", "香辣", "麻辣"]
    if prefs.get("spiceLevel") == "不辣":
        for t in SPICY_TAGS:
            if t not in exclude_tags:
                exclude_tags.append(t)

    # 2) 呼叫資料庫，取得一批候選清單
    try:
        rows = query_menu(budget=budget, must_tags=must_tags, exclude_tags=exclude_tags)
    except Exception as e:
        return {"items": [], "notes": f"資料庫查詢失敗：{e}"}

    if not rows:
        return {"items": [], "notes": "找不到符合預算或口味的餐點"}

    # 輔助：判斷菜色類型，方便後續輸出語氣
    BEVERAGE_CATEGORY_KEYWORDS = ["酒", "啤酒", "清酒", "紅酒", "果汁", "茶", "飲料"]
    VEGGIE_KEYWORDS = ["菜", "蔬", "青", "涼拌", "沙拉", "時蔬", "水蓮", "筍"]
    SWEET_KEYWORDS = ["甜", "水果", "冰", "糕", "糖", "甜品", "水果", "冰淇淋"]
    CORE_KEYWORDS = ["鍋", "煲", "主食", "主菜", "拼盤", "鵝", "排骨", "牛", "豬", "雞"]

    def is_beverage_row(row: Dict[str, Any]) -> bool:
        cat = str(row.get("CategoryName") or "")
        return any(k in cat for k in BEVERAGE_CATEGORY_KEYWORDS)

    def classify_item(row: Dict[str, Any]) -> str:
        cat = str(row.get("CategoryName") or "")
        name = str(row.get("ProductName") or "")
        text = f"{cat}{name}"
        if is_beverage_row(row):
            return "drink"
        if any(k in text for k in VEGGIE_KEYWORDS):
            return "veggie"
        if any(k in text for k in SWEET_KEYWORDS):
            return "sweet"
        if any(k in text for k in CORE_KEYWORDS):
            return "core"
        return "main"

    # 3) 嚴格控制合計不超過預算（貪婪累加），同時預留空間給飲料
    MARKET_PRICE_ASSUME = 350.0  # 時價估值，可調
    items: List[Dict[str, Any]] = []
    total = 0.0

    need_drink = bool(prefs.get("needDrink"))

    # 先把候選拆成「主菜」與「飲料」兩組，方便控制比例
    main_rows: List[Dict[str, Any]] = []
    drink_rows: List[Dict[str, Any]] = []
    for r in rows:
        (drink_rows if is_beverage_row(r) else main_rows).append(r)

    # 先挑主菜
    for row in main_rows:
        name = row.get("ProductName")
        price = row.get("Price")
        category = row.get("CategoryName")
        try:
            p = None if price in (None, "", "時價") else float(price)
        except Exception:
            p = None

        if p is None or p == 0:
            p = MARKET_PRICE_ASSUME

        if isinstance(budget, (int, float)) and budget > 0:
            # 若需要飲料，保留一點預算空間給飲料（粗略保留 MARKET_PRICE_ASSUME）
            reserve_for_drink = MARKET_PRICE_ASSUME if need_drink else 0.0
            if total + p + reserve_for_drink > float(budget):
                continue

        effective_price = None if price in (None, 0, "", "時價") else float(price)

        items.append({
            "name": name,
            "price": effective_price,
            "category": category,
            "reason": "依據您的條件挑選的主菜",
            "type": classify_item(row),
            "effectivePrice": p,
        })
        total += p
        if len(items) >= top_k:
            break

    # 如果需要飲料，儘量加入 1~2 個飲料品項
    if need_drink and drink_rows:
        # 先隨機打散飲料候選，避免每次都同一瓶
        random.shuffle(drink_rows)
        max_drinks = 2 if (isinstance(people, int) and people >= 4) else 1
        drinks_added = 0
        for row in drink_rows:
            if drinks_added >= max_drinks:
                break
            if len(items) >= top_k:
                break

            name = row.get("ProductName")
            price = row.get("Price")
            category = row.get("CategoryName")
            try:
                p = None if price in (None, "", "時價") else float(price)
            except Exception:
                p = None

            if p is None or p == 0:
                p = MARKET_PRICE_ASSUME

            if isinstance(budget, (int, float)) and budget > 0:
                if total + p > float(budget):
                    continue

            effective_price = None if price in (None, 0, "", "時價") else float(price)

            items.append({
                "name": name,
                "price": effective_price,
                "category": category,
                "reason": "幫您搭配一款飲品",
                "type": "drink",
                "effectivePrice": p,
            })
            total += p
            drinks_added += 1

    # 若因預算太嚴或資料有限導致一樣選不到任何品項 → 退而求其次：挑最便宜的前 top_k（不控合計）
    if not items:
        rows_sorted = sorted(rows, key=lambda r: (float(r.get("Price") or 1e9)))
        for row in rows_sorted[:top_k]:
            raw_price = row.get("Price")
            fallback = None if raw_price in (None, 0, "", "時價") else float(raw_price)
            approx = fallback if fallback is not None else MARKET_PRICE_ASSUME
            items.append({
                "name": row.get("ProductName"),
                "price": fallback,
                "category": row.get("CategoryName"),
                "reason": "預算較緊，改為推薦較實惠的選項",
                "type": classify_item(row),
                "effectivePrice": approx,
            })

    notes = "" if items else "找不到符合預算的組合"
    return {
        "items": items,
        "notes": notes,
        "meta": {
            "people": people,
            "budget": budget,
            "needDrink": need_drink,
        },
    }