import os, sys, json
from typing import Dict, List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 確保可以從 src/ 匯入模組
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# 從主程式匯入
from main import (
    Menu, Preferences, ConversationTurn,
    _validate_menu, normalize_menu, write_menu_json,
    generate_conversation,
)
# 專案路徑設定
PROJECT_ROOT = os.path.abspath(os.path.join(SRC_DIR, os.pardir))
WEB_DIR = os.path.join(PROJECT_ROOT, "web")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

BASE_DIR = PROJECT_ROOT  # 舊變數名稱向下相容

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
# 提供 /web/* 靜態檔案
app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")

# 載入菜單
MENU_PATHS = [
    os.path.join(PROJECT_ROOT, "db", "menu.json"),
    os.path.join(PROJECT_ROOT, "menu.json"),
]
MENU_PATH = None
for p in MENU_PATHS:
    if os.path.exists(p):
        MENU_PATH = p
        break

menu: Menu
if MENU_PATH is None:
    # 雲端部署時若沒有帶 menu.json，不要讓整個服務直接掛掉。
    # 仍可啟動前端與 /health，並提示使用者缺菜單資料。
    print(f"[WARN] 找不到菜單檔案 (menu.json)。已嘗試的路徑: {MENU_PATHS}")
    menu = {"categories": []}
else:
    try:
        with open(MENU_PATH, "r", encoding="utf-8") as f:
            menu = json.load(f)  # 把JSON讀成Python物件並存到menu
    except Exception as e:
        raise RuntimeError(f"載入菜單檔案失敗: {MENU_PATH} -> {e}")

    _validate_menu(menu)
    stats = normalize_menu(menu)
    if stats.get("market_price_tagged", 0) > 0 or stats.get("removed_salt_tags", 0) > 0:
        write_menu_json(menu, MENU_PATH)
        # 覆寫後重新讀一次，確保記憶體與檔案一致
        with open(MENU_PATH, "r", encoding="utf-8") as f:
            menu = json.load(f)
        _validate_menu(menu)

# 簡單 session 記憶
SESSIONS: Dict[str, Dict[str, object]] = {}


def _log_chat(session_id: str, user_text: str, reply: str, prefs: Preferences) -> None:
    """將每次對話紀錄成一行 JSON 方便之後分析。

    格式：一行一筆 JSON，包含 sessionId、user_text、reply、prefs 等。
    檔案位置：專案根目錄下 logs/chat_log.jsonl
    """
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        log_path = os.path.join(LOG_DIR, "chat_log.jsonl")

        record = {
            "sessionId": session_id,
            "user_text": user_text,
            "reply": reply,
            "prefs": prefs,
        }

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        # 日誌失敗不影響主流程
        pass

class ChatReq(BaseModel):
    sessionId: str
    text: str

class ChatResp(BaseModel):
    reply: str

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "web.html"))

@app.post("/api/chat", response_model=ChatResp)
def api_chat(req: ChatReq):
    s = SESSIONS.setdefault(req.sessionId, {"prefs": {}, "history": []})
    prefs: Preferences = s["prefs"]  # type: ignore[assignment]
    history: List[ConversationTurn] = s["history"]  # type: ignore[assignment]
    reply, _ = generate_conversation(history, req.text, menu, prefs)

    # 寫入簡單對話日誌，方便之後分析「大家怎麼問」、「實際推薦了什麼」
    _log_chat(req.sessionId, req.text, reply, prefs)

    return {"reply": reply}