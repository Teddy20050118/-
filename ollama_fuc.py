
import os, json, re, shutil, subprocess, random, time
from typing import Any, Dict, List, Optional, Tuple
from db_client import query_menu
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
    """
    改用 SQL Server 進行推薦，取代原本的本地權重/多樣性邏輯。
    透過 query_menu(budget, must_tags, exclude_tags) 從資料庫取得候選清單。
    """
    prefs = prefs or {}

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
    # 不辣 → 排除含「辣」的品項
    if prefs.get("spiceLevel") == "不辣":
        exclude_tags.append("辣")

    # 2) 呼叫資料庫
    try:
        rows = query_menu(budget=budget, must_tags=must_tags, exclude_tags=exclude_tags)
    except Exception as e:
        return {"items": [], "notes": f"資料庫查詢失敗：{e}"}

    if not rows:
        return {"items": [], "notes": "找不到符合預算或口味的餐點"}

    # 3) 整理回傳格式（只取前 top_k 筆）
    items: List[Dict[str, Any]] = []
    for row in rows[:top_k]:
        # 兼容 Row/dict 兩種取值方式
        getv = (row.get if hasattr(row, "get") else lambda k, d=None: getattr(row, k, d))
        name = getv("ProductName")
        price = getv("Price")
        category = getv("CategoryName")
        try:
            price = None if price in (None, "", "時價") else float(price)
        except Exception:
            pass
        items.append({
            "name": name,
            "price": price,
            "category": category,
            "reason": "依據您的需求精選推薦",
        })

    return {"items": items, "notes": ""}
