import os, sys, json
from typing import Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import asyncio

if sys.platform.startswith('win32'):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
   
# 確保可以從 src/ 匯入模組
SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# 確保可以從根目錄匯入模組
PROJECT_ROOT = os.path.abspath(os.path.join(SRC_DIR, os.pardir))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 從主程式匯入
from main import (
    Menu, Preferences, ConversationTurn,
    _validate_menu, normalize_menu, write_menu_json,
    generate_conversation,
)

# 匯入爬蟲模組
try:
    import crawl_menu
    CRAWLER_AVAILABLE = True
except ImportError as e:
    print(f"[警告] 無法匯入 crawl_menu: {e}")
    CRAWLER_AVAILABLE = False

# 專案路徑設定（PROJECT_ROOT 已在上方第16行定義）
WEB_DIR = os.path.join(PROJECT_ROOT, "web")
LOG_DIR = os.path.join(PROJECT_ROOT, "logs")

BASE_DIR = PROJECT_ROOT  # 舊變數名稱向下相容

# 啟動時顯示路徑資訊
print(f"\n{'='*60}")
print(f"[路徑設定]")
print(f"{'='*60}")
print(f"SRC_DIR      = {SRC_DIR}")
print(f"PROJECT_ROOT = {PROJECT_ROOT}")
print(f"WEB_DIR      = {WEB_DIR}")
print(f"LOG_DIR      = {LOG_DIR}")
print(f"當前工作目錄  = {os.getcwd()}")
print(f"{'='*60}\n")

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

# 啟動時自動載入最近爬取的菜單
def load_latest_crawled_menu() -> Optional[Menu]:
    """檢查是否有最近爬取的菜單檔案，並自動載入"""
    import glob
    
    # 尋找所有 menu_*.json 檔案
    menu_files = glob.glob(os.path.join(PROJECT_ROOT, "menu_*.json"))
    
    if not menu_files:
        return None
    
    # 找到最新的檔案（依修改時間）
    latest_file = max(menu_files, key=os.path.getmtime)
    
    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # 提取餐廳名稱
        filename = os.path.basename(latest_file)
        restaurant_name = filename.replace("menu_", "").replace(".json", "")
        
        # 轉換為系統菜單格式
        if "menu_items" in data and isinstance(data["menu_items"], list):
            crawled_menu: Menu = {
                "restaurants": {
                    restaurant_name: {
                        "name": data.get('name', restaurant_name),
                        "categories": {
                            "全部菜色": {
                                "items": [
                                    {
                                        "name": item.get('name', ''),
                                        "price": item.get('price', '價格未提供').replace('$', '').replace(',', '').strip() if isinstance(item.get('price'), str) else item.get('price')
                                    }
                                    for item in data.get('menu_items', [])
                                ]
                            }
                        }
                    }
                }
            }
            print(f" 自動載入最近爬取的菜單：{restaurant_name} ({len(data.get('menu_items', []))} 項)")
            return crawled_menu
    except Exception as e:
        print(f" 載入爬取菜單失敗：{e}")
    
    return None

# 多餐廳支援：使用字典管理所有餐廳菜單
RESTAURANT_MENUS: Dict[str, Menu] = {}
ACTIVE_RESTAURANT: Optional[str] = None

# 1️ 先載入預設菜單 (menu.json - 大肥鵝)
if MENU_PATH and os.path.exists(MENU_PATH):
    try:
        with open(MENU_PATH, "r", encoding="utf-8") as f:
            default_menu_data = json.load(f)
        
        # 判斷是舊格式還是新格式
        if "categories" in default_menu_data:
            # 舊格式：轉換成新格式
            RESTAURANT_MENUS["大肥鵝"] = {
                "restaurants": {
                    "大肥鵝": {
                        "name": "大肥鵝",
                        "categories": {
                            cat["name"]: {
                                "items": cat.get("items", [])
                            }
                            for cat in default_menu_data.get("categories", [])
                        }
                    }
                }
            }
            print(f" 載入預設餐廳：大肥鵝")
        elif "restaurants" in default_menu_data:
            # 新格式：直接使用
            for rest_name in default_menu_data["restaurants"]:
                RESTAURANT_MENUS[rest_name] = default_menu_data
                print(f" 載入餐廳：{rest_name}")
    except Exception as e:
        print(f" 載入預設菜單失敗：{e}")

# 2️⃣ 載入所有爬取的菜單 (menu_*.json)
import glob
menu_files = glob.glob(os.path.join(PROJECT_ROOT, "menu_*.json"))
for menu_file in menu_files:
    try:
        with open(menu_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        filename = os.path.basename(menu_file)
        restaurant_name = filename.replace("menu_", "").replace(".json", "")
        
        if "menu_items" in data and isinstance(data["menu_items"], list):
            crawled_menu: Menu = {
                "restaurants": {
                    restaurant_name: {
                        "name": data.get('name', restaurant_name),
                        "categories": {
                            "全部菜色": {
                                "items": [
                                    {
                                        "name": item.get('name', ''),
                                        "price": item.get('price', '價格未提供').replace('$', '').replace(',', '').strip() if isinstance(item.get('price'), str) else item.get('price')
                                    }
                                    for item in data.get('menu_items', [])
                                ]
                            }
                        }
                    }
                }
            }
            RESTAURANT_MENUS[restaurant_name] = crawled_menu
            print(f" 載入餐廳菜單：{restaurant_name} ({len(data.get('menu_items', []))} 項)")
    except Exception as e:
        print(f" 載入 {menu_file} 失敗：{e}")

# 設定預設活動餐廳（最新修改的）
if RESTAURANT_MENUS and menu_files:  # 確保 menu_files 不是空列表
    latest_file = max(menu_files, key=os.path.getmtime)
    latest_name = os.path.basename(latest_file).replace("menu_", "").replace(".json", "")
    ACTIVE_RESTAURANT = latest_name
    menu = RESTAURANT_MENUS[latest_name]
    print(f" 當前活動餐廳：{ACTIVE_RESTAURANT}")
else:
    # 使用預設 menu
    if MENU_PATH and os.path.exists(MENU_PATH):
        ACTIVE_RESTAURANT = "預設餐廳"
        RESTAURANT_MENUS["預設餐廳"] = menu

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

class UpdateMenuReq(BaseModel):
    """遠端觸發爬蟲更新菜單"""
    restaurant_name: str  # 餐廳名稱（例如：肯德基大甲）

class UpdateMenuResp(BaseModel):
    status: str  # "success" 或 "error"
    message: str
    restaurant_name: Optional[str] = None
    menu_items_count: Optional[int] = None

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "web.html"))

@app.get("/api/current-menu")
def get_current_menu():
    """
    回傳當前活動餐廳的菜單資料
    """
    try:
        if not ACTIVE_RESTAURANT or not menu:
            return {
                "success": False,
                "message": "目前未載入任何菜單",
                "restaurantName": None,
                "categories": []
            }
        
        # 獲取當前餐廳的菜單資料
        restaurant_data = menu.get("restaurants", {}).get(ACTIVE_RESTAURANT, {})
        categories_dict = restaurant_data.get("categories", {})
        
        # 轉換成前端友善的格式（陣列）
        categories_array = []
        for category_name, category_data in categories_dict.items():
            categories_array.append({
                "name": category_name,
                "items": category_data.get("items", [])
            })
        
        return {
            "success": True,
            "restaurantName": ACTIVE_RESTAURANT,
            "categories": categories_array
        }
    
    except Exception as e:
        print(f"[錯誤] 獲取菜單失敗: {e}")
        return {
            "success": False,
            "message": f"獲取菜單時發生錯誤: {str(e)}",
            "restaurantName": None,
            "categories": []
        }


@app.post("/api/search-foodpanda", response_model=FoodpandaSearchResp)
async def api_search_foodpanda(req: FoodpandaSearchReq):
    """
    搜尋餐廳（改用 Google 菜單爬蟲）
    """
    return FoodpandaSearchResp(
        success=True,
        message=f" 找到餐廳：{req.query}",
        restaurants=[{
            "name": req.query,
            "vendorCode": req.query,  # 直接用搜尋關鍵字
            "rating": None,
            "deliveryTime": None,
            "url": f"https://www.google.com/search?q={req.query}"
        }]
    )

@app.post("/api/crawl-foodpanda", response_model=FoodpandaResp)
async def api_crawl_foodpanda(req: FoodpandaReq):
    """
    爬取餐廳菜單（使用 crawl_menu.py）
    
    這個 API 會呼叫 crawl_menu.py 進行半自動爬蟲：
    1. 自動開啟 Chrome 並搜尋餐廳
    2. 需要手動點擊菜單頁面（避免反爬蟲機制）
    3. 自動爬取菜單資料
    """
    
    if not CRAWLER_AVAILABLE:
        return FoodpandaResp(
            success=False,
            message="爬蟲模組未安裝或無法匯入"
        )
    
    try:
        import json
        from pathlib import Path
        from dataclasses import asdict
        
        restaurant_name = req.vendorCode  # vendorCode 就是餐廳名稱
        
        print(f"\n{'='*60}")
        print(f" 開始爬取：{restaurant_name}")
        print(f"{'='*60}")
        print(f"[調試] PROJECT_ROOT = {PROJECT_ROOT}")
        print(f"[調試] 當前工作目錄 = {os.getcwd()}")
        print(f"[調試] CRAWLER_AVAILABLE = {CRAWLER_AVAILABLE}")
        
        # 使用  .quick_crawl() 進行爬取
        restaurant = await crawl_menu.quick_crawl(restaurant_name)
        
        if restaurant and restaurant.menu_items:
            print(f"[爬蟲] 成功爬取 {len(restaurant.menu_items)} 道菜")
            
            # 儲存菜單 JSON（使用絕對路徑）
            output_filename = f'menu_{restaurant.name.replace(" ", "_")}.json'
            json_file = Path(PROJECT_ROOT) / output_filename
            
            print(f"[調試] 準備儲存至: {json_file}")
            
            json_file.write_text(
                json.dumps(asdict(restaurant), ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
            print(f"[爬蟲] 菜單已儲存: {json_file}")
            
            # 驗證檔案確實存在
            if json_file.exists():
                print(f"[驗證] ✓ 檔案存在，大小: {json_file.stat().st_size} bytes")
            else:
                print(f"[錯誤] ✗ 檔案未找到: {json_file}")
            
            # 讀取保存的 JSON 檔案
            data = json.loads(json_file.read_text(encoding='utf-8'))
            print(f"讀取到菜單項目數: {len(data.get('menu_items', []))}")
            
            # 轉換為前端格式
            menu_items = []
            for item in data.get('menu_items', []):
                menu_items.append({
                    "dish": item.get('name', ''),
                    "price": item.get('price', '價格未提供')
                })
            
            # 如果菜單為空，返回失敗
            if len(menu_items) == 0:
                return FoodpandaResp(
                    success=False,
                    message=f" 爬蟲執行成功但找不到菜單資料\n\n 可能原因：\n1. 餐廳沒有在 Google Maps 上架菜單\n2. 餐廳名稱不完整\n3. 沒有手動點擊菜單頁面\n\n 解決方法：\n確保在 Chrome 中手動點擊了「菜單」標籤"
                )
            
            # 將爬取的菜單轉換為系統菜單格式並保存到全域變數
            global menu, RESTAURANT_MENUS, ACTIVE_RESTAURANT
            
            crawled_menu: Menu = {
                "restaurants": {
                    restaurant.name: {
                        "name": data.get('name', restaurant.name),
                        "categories": {
                            "全部菜色": {
                                "items": [
                                    {
                                        "name": item.get('name', ''),
                                        "price": item.get('price', '價格未提供').replace('$', '').replace(',', '').strip()
                                    }
                                    for item in data.get('menu_items', [])
                                ]
                            }
                        }
                    }
                }
            }
            
            # 更新多餐廳管理
            RESTAURANT_MENUS[restaurant.name] = crawled_menu
            ACTIVE_RESTAURANT = restaurant.name
            menu = crawled_menu
            print(f" 已將 {restaurant.name} 加入餐廳列表並設為當前活動餐廳")
            
            return FoodpandaResp(
                success=True,
                message=f" 成功爬取 {restaurant.name} 的菜單",
                restaurant={
                    "name": data.get('name', restaurant.name),
                    "rating": None,
                    "deliveryTime": None
                },
                menuItems=menu_items
            )
        else:
            # 爬蟲返回但沒有菜單資料
            print(f"[爬蟲] 爬取失敗：未取得菜單資料")
            return FoodpandaResp(
                success=False,
                message=f" 爬取失敗：未取得菜單資料\n\n 可能原因：\n1. 沒有手動點擊菜單頁面\n2. 餐廳沒有在 Google Maps 上架菜單\n3. 餐廳名稱不正確：「{restaurant_name}」\n\n 提示：\n確保在 Chrome 彈出後，手動點擊「菜單」標籤"
            )
    
    except Exception as e:
        error_msg = str(e)
        print(f"[錯誤] 爬蟲執行失敗: {error_msg}")
        import traceback
        traceback.print_exc()
        
        return FoodpandaResp(
            success=False,
            message=f" 系統錯誤：{error_msg}\n\n請檢查後端日誌"
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

# 多餐廳管理 API
@app.get("/api/restaurants")
def list_restaurants():
    """列出所有可用的餐廳"""
    restaurants_list = []
    
    for name, menu_data in RESTAURANT_MENUS.items():
        # 計算總菜品數量（遍歷所有分類）
        total_items = 0
        restaurant_data = menu_data.get("restaurants", {}).get(name, {})
        categories = restaurant_data.get("categories", {})
        
        for category_name, category_data in categories.items():
            items = category_data.get("items", [])
            total_items += len(items)
        
        restaurants_list.append({
            "name": name,
            "active": name == ACTIVE_RESTAURANT,
            "itemCount": total_items
        })
    
    return {
        "restaurants": restaurants_list,
        "activeRestaurant": ACTIVE_RESTAURANT
    }

@app.post("/api/switch-restaurant")
def switch_restaurant(restaurant_name: str):
    """切換當前活動餐廳"""
    global ACTIVE_RESTAURANT, menu
    
    if restaurant_name not in RESTAURANT_MENUS:
        raise HTTPException(404, f"餐廳 '{restaurant_name}' 不存在")
    
    ACTIVE_RESTAURANT = restaurant_name
    menu = RESTAURANT_MENUS[restaurant_name]
    
    return {
        "success": True,
        "message": f" 已切換至 {restaurant_name}",
        "activeRestaurant": ACTIVE_RESTAURANT
    }

@app.delete("/api/menu/{restaurant_name}")
def delete_menu(restaurant_name: str):
    """刪除指定餐廳的菜單（從記憶體和磁碟）"""
    global ACTIVE_RESTAURANT, menu, RESTAURANT_MENUS
    
    # 檢查餐廳是否存在
    if restaurant_name not in RESTAURANT_MENUS:
        raise HTTPException(404, f"餐廳 '{restaurant_name}' 不存在")
    
    # 1. 從記憶體中移除
    del RESTAURANT_MENUS[restaurant_name]
    
    # 2. 刪除對應的 JSON 檔案
    menu_file = os.path.join(PROJECT_ROOT, f"menu_{restaurant_name}.json")
    
    if os.path.exists(menu_file):
        try:
            os.remove(menu_file)
            print(f"[刪除] 已刪除檔案: {menu_file}")
        except Exception as e:
            print(f"[錯誤] 刪除檔案失敗: {e}")
            raise HTTPException(500, f"刪除檔案失敗: {str(e)}")
    
    # 3. 如果刪除的是當前活動餐廳，切換到其他餐廳
    if ACTIVE_RESTAURANT == restaurant_name:
        if RESTAURANT_MENUS:
            # 切換到第一個可用的餐廳
            ACTIVE_RESTAURANT = next(iter(RESTAURANT_MENUS.keys()))
            menu = RESTAURANT_MENUS[ACTIVE_RESTAURANT]
            print(f"[切換] 已自動切換至: {ACTIVE_RESTAURANT}")
        else:
            # 沒有其他餐廳了
            ACTIVE_RESTAURANT = None
            menu = {"restaurants": {}}
            print(f"[警告] 已無可用餐廳")
    
    return {
        "success": True,
        "message": f"已成功刪除 {restaurant_name}",
        "activeRestaurant": ACTIVE_RESTAURANT
    }

@app.post("/api/update-menu", response_model=UpdateMenuResp)
async def update_menu(req: UpdateMenuReq):
    """遠端觸發爬蟲更新菜單"""
    
    if not CRAWLER_AVAILABLE:
        return UpdateMenuResp(
            status="error",
            message="爬蟲模組未安裝或無法匯入"
        )
    
    restaurant_name = req.restaurant_name
    print(f"\n{'='*60}")
    print(f"[API] 收到遠端爬蟲請求")
    print(f"[API] 目標餐廳: {restaurant_name}")
    print(f"{'='*60}\n")
    
    try:
        # 執行爬蟲
        print(f"[爬蟲] 開始爬取: {restaurant_name}")
        print(f"[調試] PROJECT_ROOT = {PROJECT_ROOT}")
        print(f"[調試] 當前工作目錄 = {os.getcwd()}")
        
        restaurant = await crawl_menu.quick_crawl(restaurant_name)
        
        if restaurant and restaurant.menu_items:
            print(f"[爬蟲] 成功爬取 {len(restaurant.menu_items)} 道菜")
            
            # 儲存菜單 JSON（確保使用絕對路徑）
            import json
            from pathlib import Path
            from dataclasses import asdict
            
            # 使用絕對路徑確保儲存到專案根目錄
            output_filename = f'menu_{restaurant.name.replace(" ", "_")}.json'
            output_file = os.path.abspath(os.path.join(PROJECT_ROOT, output_filename))
            
            print(f"[調試] 準備儲存至: {output_file}")
            
            # 確保目錄存在
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            
            Path(output_file).write_text(
                json.dumps(asdict(restaurant), ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
            print(f"[爬蟲] 菜單已儲存: {output_file}")
            
            # 驗證檔案確實存在
            if os.path.exists(output_file):
                print(f"[驗證] ✓ 檔案存在，大小: {os.path.getsize(output_file)} bytes")
            else:
                print(f"[錯誤] ✗ 檔案未找到: {output_file}")
            
            # 重新載入菜單到系統中
            try:
                global RESTAURANT_MENUS, ACTIVE_RESTAURANT
                
                # 重新讀取剛儲存的菜單檔案
                with open(output_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                
                if "menu_items" in data and isinstance(data["menu_items"], list):
                    crawled_menu: Menu = {
                        "restaurants": {
                            restaurant.name: {
                                "name": data.get('name', restaurant.name),
                                "categories": {
                                    "全部菜色": {
                                        "items": [
                                            {
                                                "name": item.get('name', ''),
                                                "price": item.get('price', '價格未提供').replace('$', '').replace(',', '').strip() if isinstance(item.get('price'), str) else item.get('price')
                                            }
                                            for item in data.get('menu_items', [])
                                        ]
                                    }
                                }
                            }
                        }
                    }
                    RESTAURANT_MENUS[restaurant.name] = crawled_menu
                    ACTIVE_RESTAURANT = restaurant.name
                    menu = crawled_menu
                    print(f"[系統] 已將 {restaurant.name} 設為活動餐廳")
            except Exception as e:
                print(f"[警告] 重新載入菜單失敗: {e}")
            
            return UpdateMenuResp(
                status="success",
                message=f"成功爬取 {restaurant.name} 的菜單",
                restaurant_name=restaurant.name,
                menu_items_count=len(restaurant.menu_items)
            )
        else:
            print(f"[爬蟲] 爬取失敗：未取得菜單資料")
            return UpdateMenuResp(
                status="error",
                message="爬取失敗：未取得菜單資料，請確認是否手動點擊了菜單頁面"
            )
            
    except Exception as e:
        error_msg = str(e)
        print(f"[錯誤] 爬蟲執行失敗: {error_msg}")
        import traceback
        traceback.print_exc()
        
        return UpdateMenuResp(
            status="error",
            message=f"爬蟲執行失敗: {error_msg}"
        )

if __name__ == "__main__":
    import uvicorn
    
    # 可以用環境變數自訂 host 和 port
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "7890"))
    
    print(f" 啟動後端服務...")
    print(f" 訪問: http://localhost:{port}")
    print(f" 靜態檔案: ../web")
    print(f" 爬蟲: crawler_google_bwzfsc.py")
    print(f"\n按 Ctrl+C 停止服務\n")
    uvicorn.run(app, host=host, port=port)