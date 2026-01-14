import os, sys, json
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import asyncio

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

class CrawlReq(BaseModel):
    query: str
    maxShops: Optional[int] = 5
    maxItems: Optional[int] = 30

class CrawlResp(BaseModel):
    success: bool
    message: str
    itemCount: Optional[int] = None
    restaurants: Optional[List[dict]] = None

class FoodpandaSearchReq(BaseModel):
    """搜尋 Foodpanda 餐廳（只列出，不爬菜單）"""
    query: str
    city: Optional[str] = "taichung"
    maxResults: Optional[int] = 10

class FoodpandaSearchResp(BaseModel):
    """搜尋結果"""
    success: bool
    message: str
    restaurants: Optional[List[dict]] = None

class FoodpandaReq(BaseModel):
    """爬取特定餐廳的菜單"""
    vendorCode: str  # Foodpanda 餐廳代碼（例如：s1ab）
    restaurantName: Optional[str] = None  # 顯示用

class FoodpandaResp(BaseModel):
    success: bool
    message: str
    restaurant: Optional[dict] = None
    menuItems: Optional[List[dict]] = None

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "web.html"))

@app.post("/api/crawl", response_model=CrawlResp)
async def api_crawl(req: CrawlReq):
    """即時爬取 Google Maps 餐廳菜單。
    
    前端可以呼叫此 API，傳入搜尋關鍵字（例如 "台中 火鍋"），
    後端會使用 crawler.py 的邏輯去抓餐廳菜單，並回傳結果。
    
    注意：
    - 這是個耗時操作（5-30秒），建議前端顯示 loading 狀態
    - 結果不會自動寫入資料庫，僅回傳給前端參考
    - 若要整合到推薦系統，需手動合併到 menu.json 或 DB
    """
    try:
        # 動態導入 crawler 模組（避免啟動時就要求 playwright）
        try:
            sys.path.insert(0, PROJECT_ROOT)
            from crawler import crawl_google_maps, to_menu_json
        except ImportError as e:
            raise HTTPException(
                status_code=503,
                detail=f"爬蟲功能未啟用，缺少 playwright：{e}"
            )
        
        # 執行爬蟲（async）
        restaurants = await crawl_google_maps(
            req.query,
            max_shops=req.maxShops,
            max_items_per_shop=req.maxItems,
            headless=True,
        )
        
        # 轉成 JSON 格式
        menu_data = to_menu_json(restaurants)
        
        # 統計有菜單的餐廳數量
        restaurants_with_menu = sum(1 for r in restaurants if r.menu_items and len(r.menu_items) > 0)
        
        message = f"成功爬取 {len(restaurants)} 間餐廳"
        if restaurants_with_menu > 0:
            message += f"，其中 {restaurants_with_menu} 間有菜單資料（共 {len(menu_data)} 道菜）"
        else:
            message += "，但這些餐廳在 Google Maps 上沒有提供結構化的菜單資料"
        
        return CrawlResp(
            success=True,
            message=message,
            itemCount=len(menu_data),
            restaurants=[{
                "name": r.name,
                "rating": r.rating,
                "address": r.address,
                "menuItemCount": len(r.menu_items) if r.menu_items else 0,
            } for r in restaurants],
        )
        
    except Exception as e:
        return CrawlResp(
            success=False,
            message=f"爬取失敗：{str(e)}"
        )

@app.post("/api/search-foodpanda", response_model=FoodpandaSearchResp)
async def api_search_foodpanda(req: FoodpandaSearchReq):
    """
    在 Foodpanda 搜尋餐廳（不爬菜單，只列出餐廳）
    
    這是一個輕量級的搜尋端點，用於：
    1. 使用者輸入關鍵字（例如：「牛排」、「火鍋」）
    2. 快速返回餐廳清單（含 vendorCode）
    3. 使用者選擇後，再用 /api/crawl-foodpanda 爬取菜單
    
    優勢：
    - 快速（約 3-5 秒）
    - 不需要爬取菜單
    - 使用者可以先預覽餐廳再決定
    """
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from foodpanda_crawler import search_foodpanda
        from playwright.async_api import async_playwright
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            
            restaurants = await search_foodpanda(page, req.query, req.city)
            await browser.close()
            
            if not restaurants:
                return FoodpandaSearchResp(
                    success=False,
                    message=f"找不到「{req.query}」相關的餐廳"
                )
            
            return FoodpandaSearchResp(
                success=True,
                message=f"找到 {len(restaurants)} 間餐廳",
                restaurants=[{
                    "name": r.name,
                    "vendorCode": r.vendor_code,
                    "rating": r.rating,
                    "deliveryTime": r.delivery_time,
                    "url": r.url,
                } for r in restaurants[:req.maxResults]]
            )
    
    except Exception as e:
        return FoodpandaSearchResp(
            success=False,
            message=f"搜尋失敗：{str(e)}"
        )

@app.post("/api/crawl-foodpanda", response_model=FoodpandaResp)
async def api_crawl_foodpanda(req: FoodpandaReq):
    """
    從 Foodpanda 爬取指定餐廳的完整菜單
    
    前端流程：
    1. 使用者透過 /api/search-foodpanda 搜尋餐廳
    2. 選擇感興趣的餐廳（獲得 vendorCode）
    3. 呼叫此 API 爬取完整菜單
    
    注意：這是耗時操作（10-30秒），建議前端顯示 loading
    """
    try:
        sys.path.insert(0, PROJECT_ROOT)
        from foodpanda_crawler import crawl_foodpanda_menu, FoodpandaRestaurant, to_menu_json
        from playwright.async_api import async_playwright
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            
            # 建立餐廳物件
            restaurant = FoodpandaRestaurant(
                name=req.restaurantName or req.vendorCode,
                vendor_code=req.vendorCode,
                url=f"https://www.foodpanda.com.tw/restaurant/{req.vendorCode}",
                menu_items=[]
            )
            
            # 爬取菜單
            await crawl_foodpanda_menu(page, restaurant)
            await browser.close()
            
            if not restaurant.menu_items:
                return FoodpandaResp(
                    success=False,
                    message=f"無法爬取「{restaurant.name}」的菜單，可能餐廳暫停營業或網頁結構改變"
                )
            
            menu_data = to_menu_json(restaurant)
            
            return FoodpandaResp(
                success=True,
                message=f"成功爬取 {restaurant.name} 的菜單，共 {len(menu_data)} 道菜",
                restaurant={
                    "name": restaurant.name,
                    "rating": restaurant.rating,
                    "deliveryTime": restaurant.delivery_time,
                    "url": restaurant.url,
                },
                menuItems=menu_data
            )
        
    except Exception as e:
        return FoodpandaResp(
            success=False,
            message=f"爬取失敗：{str(e)}"
        )

@app.post("/api/chat", response_model=ChatResp)
def api_chat(req: ChatReq):
    s = SESSIONS.setdefault(req.sessionId, {"prefs": {}, "history": []})
    prefs: Preferences = s["prefs"]  # type: ignore[assignment]
    history: List[ConversationTurn] = s["history"]  # type: ignore[assignment]
    reply, _ = generate_conversation(history, req.text, menu, prefs)

    # 寫入簡單對話日誌，方便之後分析「大家怎麼問」、「實際推薦了什麼」
    _log_chat(req.sessionId, req.text, reply, prefs)

    return {"reply": reply}