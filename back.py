import os, json
from typing import Dict, List
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# 從你的主程式匯入
from main import (
    Menu, Preferences, ConversationTurn,
    _validate_menu, normalize_menu, write_menu_json,
    generate_conversation,
)

BASE_DIR = os.path.dirname(__file__)  # 專案根目錄

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
# 提供 /web/* 靜態檔案
app.mount("/web", StaticFiles(directory=os.path.join(BASE_DIR, "web")), name="web")

# 載入菜單
MENU_PATH = os.path.join(BASE_DIR, "db", "menu.json")
with open(MENU_PATH, "r", encoding="utf-8") as f:
    menu: Menu = json.load(f) #把JSON讀成Python物件並存到menu
_validate_menu(menu)
stats = normalize_menu(menu)
if stats["market_price_tagged"] > 0 or stats["removed_salt_tags"] > 0:
    write_menu_json(menu, MENU_PATH)

# 簡單 session 記憶
SESSIONS: Dict[str, Dict[str, object]] = {}

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
    return FileResponse(os.path.join(BASE_DIR, "web", "web.html"))

@app.post("/api/chat", response_model=ChatResp)
def api_chat(req: ChatReq):
    s = SESSIONS.setdefault(req.sessionId, {"prefs": {}, "history": []})
    prefs: Preferences = s["prefs"]  # type: ignore[assignment]
    history: List[ConversationTurn] = s["history"]  # type: ignore[assignment]
    reply, _ = generate_conversation(history, req.text, menu, prefs)
    return {"reply": reply}