import os, sys, json, re, threading, time, uuid
from typing import Any, Dict, List, Optional
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import asyncio
import concurrent.futures

if sys.platform.startswith('win32'):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

def _run_crawler(restaurant_name: str):
    """在獨立執行緒 + 全新 ProactorEventLoop 跑爬蟲，避免與 uvicorn SelectorEventLoop 衝突"""
    import asyncio
    loop = asyncio.new_event_loop()
    if sys.platform.startswith('win32'):
        loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(crawl_menu.quick_crawl(restaurant_name))
    finally:
        loop.close()
   
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

from restaurant_reviews import (
    identify_restaurant_candidates,
    load_review_cache,
    refresh_restaurant_reviews,
)
from menu_vision import (
    MAX_IMAGE_BYTES,
    analyze_menu_image,
    apply_manual_correction,
    format_menu_preview,
    looks_like_manual_correction,
    to_persisted_document,
)
from line_bot import (
    extract_restaurant_name,
    get_message_content,
    push_target,
    push_text,
    reply_text,
    source_key,
    verify_signature,
)

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

# LINE 對話的暫存狀態。正式多機部署時可替換成 Redis；單機版不需額外服務。
LINE_STATES: Dict[str, Dict[str, object]] = {}
LINE_STATE_LOCK = threading.Lock()
LINE_EVENT_IDS: Dict[str, None] = {}
PENDING_ANALYSES: Dict[str, Dict[str, object]] = {}
PENDING_ANALYSIS_LOCK = threading.Lock()
PENDING_ANALYSIS_TTL_SECONDS = 15 * 60

DEFAULT_RESTAURANT_NAME = os.getenv("DEFAULT_RESTAURANT_NAME", "預設餐廳")

# 1️ 先載入預設菜單 (menu.json)
if MENU_PATH and os.path.exists(MENU_PATH):
    try:
        with open(MENU_PATH, "r", encoding="utf-8") as f:
            default_menu_data = json.load(f)
        
        # 判斷是舊格式還是新格式
        if "categories" in default_menu_data:
            # 舊格式：轉換成新格式
            RESTAURANT_MENUS[DEFAULT_RESTAURANT_NAME] = {
                "restaurants": {
                    DEFAULT_RESTAURANT_NAME: {
                        "name": DEFAULT_RESTAURANT_NAME,
                        "categories": {
                            cat["name"]: {
                                "items": cat.get("items", [])
                            }
                            for cat in default_menu_data.get("categories", [])
                        }
                    }
                }
            }
            print(f" 載入預設餐廳：{DEFAULT_RESTAURANT_NAME}")
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
        filename_name = filename.replace("menu_", "").replace(".json", "")
        restaurant_name = str(data.get("name") or filename_name).strip() or filename_name

        # 舊版 VLM 檔案沒有人工確認與可靠品質資訊，不能直接進入推薦。
        # 爬蟲菜單維持原本相容行為。
        if data.get("source") == "user_photo_vlm":
            quality = data.get("quality") if isinstance(data.get("quality"), dict) else {}
            if data.get("schemaVersion") != 2 or not data.get("confirmedAt") or float(quality.get("score") or 0) <= 0:
                print(f" 跳過未確認的舊版照片菜單：{restaurant_name}（請重新上傳並確認）")
                continue
        
        if "menu_items" in data and isinstance(data["menu_items"], list):
            grouped_categories: Dict[str, dict] = {}
            for item in data.get("menu_items", []):
                category_name = str(item.get("category") or "全部菜色")
                grouped_categories.setdefault(category_name, {"items": []})["items"].append({
                    "name": item.get("name", ""),
                    "price": item.get("price", "價格未提供").replace("$", "").replace(",", "").strip()
                    if isinstance(item.get("price"), str) else item.get("price"),
                    **({"description": item.get("description")} if item.get("description") else {}),
                })
            crawled_menu: Menu = {
                "restaurants": {
                    restaurant_name: {
                        "name": data.get('name', restaurant_name),
                        "categories": grouped_categories,
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
    try:
        with open(latest_file, "r", encoding="utf-8") as latest_handle:
            latest_data = json.load(latest_handle)
        latest_name = str(latest_data.get("name") or latest_name).strip() or latest_name
    except Exception:
        pass
    if latest_name not in RESTAURANT_MENUS:
        latest_name = next(iter(RESTAURANT_MENUS))
    ACTIVE_RESTAURANT = latest_name
    menu = RESTAURANT_MENUS[latest_name]
    print(f" 當前活動餐廳：{ACTIVE_RESTAURANT}")
else:
    # 使用預設 menu
    if MENU_PATH and os.path.exists(MENU_PATH):
        ACTIVE_RESTAURANT = DEFAULT_RESTAURANT_NAME
        RESTAURANT_MENUS[DEFAULT_RESTAURANT_NAME] = menu

# 簡單 session 記憶
SESSIONS: Dict[str, Dict[str, object]] = {}


def _safe_menu_path(restaurant_name: str) -> str:
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", restaurant_name).strip(" ._")
    safe_name = re.sub(r"\s+", "_", safe_name)[:100]
    if not safe_name:
        raise ValueError("餐廳名稱無法作為檔名")
    return os.path.join(PROJECT_ROOT, f"menu_{safe_name}.json")


def _prune_pending_analyses(now: Optional[float] = None) -> None:
    current = now if now is not None else time.time()
    expired = [
        analysis_id for analysis_id, entry in PENDING_ANALYSES.items()
        if float(entry.get("expires_at") or 0) <= current
    ]
    for analysis_id in expired:
        PENDING_ANALYSES.pop(analysis_id, None)


def _store_pending_analysis(result: Dict[str, Any]) -> str:
    analysis_id = uuid.uuid4().hex
    now = time.time()
    with PENDING_ANALYSIS_LOCK:
        _prune_pending_analyses(now)
        PENDING_ANALYSES[analysis_id] = {
            "result": result,
            "created_at": now,
            "expires_at": now + PENDING_ANALYSIS_TTL_SECONDS,
        }
    return analysis_id


def _take_pending_analysis(analysis_id: str) -> Dict[str, Any]:
    with PENDING_ANALYSIS_LOCK:
        _prune_pending_analyses()
        entry = PENDING_ANALYSES.pop(analysis_id, None)
    if not entry or not isinstance(entry.get("result"), dict):
        raise ValueError("辨識結果不存在或已超過 15 分鐘，請重新上傳照片")
    return entry["result"]  # type: ignore[return-value]


def _correct_pending_analysis(analysis_id: str, instruction: str) -> Dict[str, Any]:
    with PENDING_ANALYSIS_LOCK:
        _prune_pending_analyses()
        entry = PENDING_ANALYSES.get(analysis_id)
        if not entry or not isinstance(entry.get("result"), dict):
            raise ValueError("辨識結果不存在或已超過 15 分鐘，請重新上傳照片")
        correction = apply_manual_correction(entry["result"], instruction)  # type: ignore[arg-type]
        entry["expires_at"] = time.time() + PENDING_ANALYSIS_TTL_SECONDS
        return correction


def _analysis_response(result: Dict[str, Any], analysis_id: str) -> Dict[str, Any]:
    quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
    item_count = int(quality.get("itemCount") or sum(
        len(category.get("items", [])) for category in result.get("categories", []) if isinstance(category, dict)
    ))
    return {
        "success": True,
        "analysisId": analysis_id,
        "expiresInSeconds": PENDING_ANALYSIS_TTL_SECONDS,
        "restaurantNameCandidate": result.get("detected_restaurant_name") or result.get("restaurant_name") or "",
        "requestedRestaurantName": (result.get("identity") or {}).get("userHint", "")
        if isinstance(result.get("identity"), dict) else "",
        "itemCount": item_count,
        "categories": result.get("categories", []),
        "sourceType": result.get("source_type"),
        "quality": quality,
        "priceCoverage": quality.get("priceCoverage", 0),
        "conflicts": result.get("conflicts", []),
        "identity": result.get("identity", {}),
        "identityConflict": bool(result.get("identityConflict")),
        "needsAcceptance": bool(
            result.get("identityConflict")
            or result.get("conflicts")
            or float(quality.get("score") or 0) < 0.75
        ),
        "warnings": result.get("warnings", []),
    }


def _register_vision_menu(result: Dict[str, object]) -> Dict[str, object]:
    """Persist one normalized VLM result and make it the active restaurant."""
    global ACTIVE_RESTAURANT, menu
    restaurant_name = str(result.get("restaurant_name") or "").strip()
    if not restaurant_name:
        raise ValueError("無法確認餐廳名稱，請先輸入餐廳名稱")

    category_map = {
        str(category.get("name") or "其他"): {"items": list(category.get("items") or [])}
        for category in result.get("categories", [])  # type: ignore[union-attr]
        if isinstance(category, dict) and category.get("items")
    }
    item_count = sum(len(value["items"]) for value in category_map.values())
    if not item_count:
        raise ValueError("沒有可新增的菜單項目")

    runtime_menu: Menu = {
        "restaurants": {
            restaurant_name: {
                "name": restaurant_name,
                "categories": category_map,
            }
        }
    }
    output_path = _safe_menu_path(restaurant_name)
    temporary_path = f"{output_path}.tmp"
    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(to_persisted_document(result), file, ensure_ascii=False, indent=2)
    os.replace(temporary_path, output_path)

    RESTAURANT_MENUS[restaurant_name] = runtime_menu
    ACTIVE_RESTAURANT = restaurant_name
    menu = runtime_menu
    return {"restaurantName": restaurant_name, "itemCount": item_count, "categories": list(category_map)}


def _crawler_error_message(restaurant) -> Optional[str]:
    if not restaurant:
        return None
    if getattr(restaurant, "error", "") == "google_verification_required":
        return "Google 要求人機驗證，請在自動開啟的 Chrome 完成驗證後，重新爬取同一間餐廳。"
    return None


def _crawler_no_menu_message(restaurant_name: str) -> str:
    return (
        f"找不到足夠菜單資料：{restaurant_name} 的文字菜單、Google 圖片搜尋結果截圖與候選圖片解析，"
        "都沒有達到 8 項以上的可用菜單門檻。"
    )


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

class RestaurantReviewRefreshReq(BaseModel):
    restaurant_name: str
    restaurant_identity: Optional[Dict[str, object]] = None

class RestaurantReviewIdentifyReq(BaseModel):
    restaurant_name: str


class VisionConfirmReq(BaseModel):
    restaurant_name: str = ""
    accept_conflicts: bool = False


class VisionCorrectionReq(BaseModel):
    instruction: str


def _guess_image_type(image: bytes) -> str:
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"RIFF") and image[8:12] == b"WEBP":
        return "image/webp"
    if image[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1"):
        return "image/heic"
    return "image/jpeg"


def _remember_line_event(event_id: str) -> None:
    if not event_id:
        return
    with LINE_STATE_LOCK:
        LINE_EVENT_IDS[event_id] = None
        while len(LINE_EVENT_IDS) > 1000:
            LINE_EVENT_IDS.pop(next(iter(LINE_EVENT_IDS)))


def _line_process_image(source: Dict[str, object], message_id: str) -> None:
    """Long-running VLM work happens after the webhook has already acknowledged LINE."""
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    key = source_key(source)
    target = push_target(source)
    try:
        image_bytes = get_message_content(message_id, token)
        with LINE_STATE_LOCK:
            state = dict(LINE_STATES.get(key, {}))
        hint = str(state.get("restaurant_name") or "")
        result = analyze_menu_image(image_bytes, _guess_image_type(image_bytes), hint)
        expires_at = time.time() + PENDING_ANALYSIS_TTL_SECONDS
        with LINE_STATE_LOCK:
            LINE_STATES[key] = {
                **state,
                "pending_result": result,
                "pending_expires_at": expires_at,
            }
        quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
        candidate = str(result.get("detected_restaurant_name") or result.get("restaurant_name") or "看不出店名")
        item_count = int(quality.get("itemCount") or 0)
        price_coverage = round(float(quality.get("priceCoverage") or 0) * 100)
        category_names = [
            str(category.get("name")) for category in result.get("categories", [])
            if isinstance(category, dict) and category.get("name")
        ][:5]
        menu_preview = format_menu_preview(result, max_items=35)
        warnings = []
        if result.get("identityConflict"):
            identity = result.get("identity") if isinstance(result.get("identity"), dict) else {}
            warnings.append(
                f"⚠️ 你先前輸入「{identity.get('userHint', '')}」，但圖片較像「{identity.get('detectedName', candidate)}」"
            )
        conflicts = result.get("conflicts") if isinstance(result.get("conflicts"), list) else []
        if conflicts:
            warnings.append(f"⚠️ 有 {len(conflicts)} 組 OCR 衝突，確認前請留意")
        push_text(
            target,
            "辨識完成，尚未存檔。\n"
            f"可能店名：{candidate}\n"
            f"品項：{item_count} 道｜價格覆蓋率：{price_coverage}%\n"
            f"分類：{'、'.join(category_names) if category_names else '未分類'}\n"
            f"\n暫存菜單：\n{menu_preview}\n"
            + (("\n".join(warnings) + "\n") if warnings else "")
            + "有錯請統一輸入「改 3 菜名 豬腳飯」或「改 3 價格 90」。\n"
            + "店名用「餐廳：店名」修改；完成後回覆「確認」，放棄請回覆「取消」。15 分鐘後失效。",
            token,
        )
    except Exception as exc:
        print(f"[LINE] 圖片處理失敗: {exc}")
        if target and token:
            try:
                push_text(target, f"照片辨識失敗：{str(exc)[:300]}\n請確認照片清晰且包含菜單或菜色後再試一次。", token)
            except Exception as push_exc:
                print(f"[LINE] 錯誤通知失敗: {push_exc}")


def _find_line_restaurant(text: str, state: Dict[str, object]) -> str:
    remembered = str(state.get("restaurant_name") or "")
    if remembered in RESTAURANT_MENUS:
        return remembered
    folded_text = text.casefold()
    matches = [name for name in RESTAURANT_MENUS if name.casefold() in folded_text]
    return max(matches, key=len) if matches else ""


def _line_process_recommendation(source: Dict[str, object], user_text: str, restaurant_name: str) -> None:
    token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    key = source_key(source)
    target = push_target(source)
    try:
        target_menu = RESTAURANT_MENUS.get(restaurant_name)
        if not target_menu:
            raise ValueError(f"找不到餐廳「{restaurant_name}」的菜單")
        with LINE_STATE_LOCK:
            state = dict(LINE_STATES.get(key, {}))
            history = list(state.get("history") or [])
            prefs = dict(state.get("prefs") or {})
        reply, updated_history = generate_conversation(history, user_text, target_menu, prefs)
        with LINE_STATE_LOCK:
            latest = dict(LINE_STATES.get(key, {}))
            LINE_STATES[key] = {
                **latest,
                "restaurant_name": restaurant_name,
                "history": updated_history[-12:],
                "prefs": prefs,
            }
        push_text(target, reply, token)
    except Exception as exc:
        print(f"[LINE] 推薦失敗: {exc}")
        if target and token:
            try:
                push_text(target, f"推薦失敗：{str(exc)[:300]}", token)
            except Exception as push_exc:
                print(f"[LINE] 推薦錯誤通知失敗: {push_exc}")


@app.post("/api/menu/vision")
async def create_menu_from_photo(
    restaurant_name: str = Form(default=""),
    image: UploadFile = File(...),
):
    """Analyze a photo and keep the result pending until explicit confirmation."""
    image_bytes = await image.read(MAX_IMAGE_BYTES + 1)
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "圖片不可超過 10 MB")
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: analyze_menu_image(image_bytes, image.content_type or "", restaurant_name),
        )
        analysis_id = _store_pending_analysis(result)
        return _analysis_response(result, analysis_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, f"VLM 服務錯誤：{exc}") from exc


@app.post("/api/menu/vision/{analysis_id}/confirm")
def confirm_menu_from_photo(analysis_id: str, req: VisionConfirmReq):
    """Persist a pending analysis as schema v2 after the user accepts it."""
    try:
        result = _take_pending_analysis(analysis_id)
        restaurant_name = (req.restaurant_name or str(result.get("restaurant_name") or "")).strip()
        if not restaurant_name:
            restaurant_name = str(result.get("detected_restaurant_name") or "").strip()
        if not restaurant_name:
            raise ValueError("請提供餐廳名稱後再確認")
        quality = result.get("quality") if isinstance(result.get("quality"), dict) else {}
        needs_acceptance = bool(
            result.get("identityConflict")
            or result.get("conflicts")
            or float(quality.get("score") or 0) < 0.75
        )
        if needs_acceptance and not req.accept_conflicts:
            # Put it back for another confirmation attempt.
            with PENDING_ANALYSIS_LOCK:
                PENDING_ANALYSES[analysis_id] = {
                    "result": result,
                    "created_at": time.time(),
                    "expires_at": time.time() + PENDING_ANALYSIS_TTL_SECONDS,
                }
            raise HTTPException(409, "店名、OCR 或辨識品質需要人工檢查，請檢查摘要並明確接受後再確認")
        result["restaurant_name"] = restaurant_name
        summary = _register_vision_menu(result)
        return {"success": True, **summary, "quality": result.get("quality", {}), "warnings": result.get("warnings", [])}
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/menu/vision/{analysis_id}/correct")
def correct_menu_from_photo(analysis_id: str, req: VisionCorrectionReq):
    """Correct a pending OCR result without touching persisted restaurant data."""
    try:
        correction = _correct_pending_analysis(analysis_id, req.instruction)
        result = correction["result"]
        return {
            **_analysis_response(result, analysis_id),
            "correctionMessage": correction["message"],
            "manualCorrections": result.get("manualCorrections", []),
        }
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/line/webhook")
async def line_webhook(request: Request, background_tasks: BackgroundTasks):
    """Receive LINE events, acknowledge quickly, then process photos in background."""
    channel_secret = os.getenv("LINE_CHANNEL_SECRET", "")
    channel_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not channel_secret or not channel_token:
        raise HTTPException(503, "LINE Bot 尚未設定")

    body = await request.body()
    if not verify_signature(body, request.headers.get("x-line-signature", ""), channel_secret):
        raise HTTPException(400, "LINE webhook 簽章無效")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "Webhook JSON 無效") from exc

    for event in payload.get("events", []):
        if event.get("type") != "message" or not event.get("replyToken"):
            continue
        event_id = str(event.get("webhookEventId") or "")
        with LINE_STATE_LOCK:
            if event_id and event_id in LINE_EVENT_IDS:
                continue
        message = event.get("message") or {}
        source = event.get("source") or {}
        key = source_key(source)
        reply_token = event["replyToken"]
        if message.get("type") == "image" and key:
            reply_text(reply_token, "收到照片，正在辨識菜單與菜色。完成後會再通知你。", channel_token)
            background_tasks.add_task(_line_process_image, source, str(message.get("id", "")))
            _remember_line_event(event_id)
            continue
        if message.get("type") == "text" and key:
            user_text = str(message.get("text") or "").strip()
            normalized_command = re.sub(r"\s+", "", user_text)
            if normalized_command == "取消":
                with LINE_STATE_LOCK:
                    state = dict(LINE_STATES.get(key, {}))
                    had_pending = isinstance(state.pop("pending_result", None), dict)
                    state.pop("pending_expires_at", None)
                    LINE_STATES[key] = state
                reply_text(reply_token, "已取消這次菜單辨識結果。" if had_pending else "目前沒有等待確認的菜單。", channel_token)
                _remember_line_event(event_id)
                continue

            if normalized_command == "確認":
                with LINE_STATE_LOCK:
                    state = dict(LINE_STATES.get(key, {}))
                    pending = state.get("pending_result")
                    expires_at = float(state.get("pending_expires_at") or 0)
                if not isinstance(pending, dict) or expires_at <= time.time():
                    with LINE_STATE_LOCK:
                        state.pop("pending_result", None)
                        state.pop("pending_expires_at", None)
                        LINE_STATES[key] = state
                    reply_text(reply_token, "沒有可確認的辨識結果，或結果已超過 15 分鐘。請重新上傳照片。", channel_token)
                    _remember_line_event(event_id)
                    continue
                chosen_name = str(state.get("restaurant_name") or pending.get("restaurant_name") or pending.get("detected_restaurant_name") or "").strip()
                if not chosen_name:
                    reply_text(reply_token, "還缺餐廳名稱，請先回覆「餐廳：店名」，再輸入「確認」。", channel_token)
                    _remember_line_event(event_id)
                    continue
                pending["restaurant_name"] = chosen_name
                try:
                    summary = _register_vision_menu(pending)
                    with LINE_STATE_LOCK:
                        latest = dict(LINE_STATES.get(key, {}))
                        latest.pop("pending_result", None)
                        latest.pop("pending_expires_at", None)
                        LINE_STATES[key] = {**latest, "restaurant_name": chosen_name}
                    reply_text(
                        reply_token,
                        f"已確認並建立「{chosen_name}」，共 {summary['itemCount']} 道菜。現在可以說「推薦我晚餐」。",
                        channel_token,
                    )
                except Exception as exc:
                    reply_text(reply_token, f"建立餐廳失敗：{str(exc)[:300]}", channel_token)
                _remember_line_event(event_id)
                continue

            with LINE_STATE_LOCK:
                correction_state = dict(LINE_STATES.get(key, {}))
                correction_pending = correction_state.get("pending_result")
                correction_expires_at = float(correction_state.get("pending_expires_at") or 0)
            if isinstance(correction_pending, dict) and looks_like_manual_correction(user_text):
                if correction_expires_at <= time.time():
                    with LINE_STATE_LOCK:
                        correction_state.pop("pending_result", None)
                        correction_state.pop("pending_expires_at", None)
                        LINE_STATES[key] = correction_state
                    reply_text(reply_token, "辨識結果已超過 15 分鐘，請重新上傳照片。", channel_token)
                else:
                    try:
                        correction = apply_manual_correction(correction_pending, user_text)
                        with LINE_STATE_LOCK:
                            latest = dict(LINE_STATES.get(key, {}))
                            latest["pending_result"] = correction_pending
                            latest["pending_expires_at"] = time.time() + PENDING_ANALYSIS_TTL_SECONDS
                            LINE_STATES[key] = latest
                        reply_text(
                            reply_token,
                            f"{correction['message']}。\n\n更新後菜單：\n{format_menu_preview(correction_pending, max_items=35)}\n\n"
                            "還有問題可以繼續修改；完成後回覆「確認」。",
                            channel_token,
                        )
                    except ValueError as exc:
                        reply_text(
                            reply_token,
                            f"修正失敗：{exc}\n請使用「改 3 菜名 豬腳飯」或「改 3 價格 90」。",
                            channel_token,
                        )
                _remember_line_event(event_id)
                continue

            restaurant_name = extract_restaurant_name(user_text)
            if not restaurant_name:
                with LINE_STATE_LOCK:
                    state = dict(LINE_STATES.get(key, {}))
                if isinstance(state.get("pending_result"), dict):
                    reply_text(reply_token, "這份菜單還沒確認。請先回覆「確認」後才能用它推薦，或回覆「取消」。", channel_token)
                    _remember_line_event(event_id)
                    continue
                selected_restaurant = _find_line_restaurant(user_text, state)
                if selected_restaurant:
                    reply_text(reply_token, f"正在根據「{selected_restaurant}」的菜單準備推薦，請稍候。", channel_token)
                    background_tasks.add_task(
                        _line_process_recommendation,
                        source,
                        user_text,
                        selected_restaurant,
                    )
                else:
                    reply_text(
                        reply_token,
                        "目前還沒有選定餐廳。請先傳菜單照片，或輸入「餐廳：店名」後再傳照片。",
                        channel_token,
                    )
                _remember_line_event(event_id)
                continue
            with LINE_STATE_LOCK:
                state = dict(LINE_STATES.get(key, {}))
                pending = state.get("pending_result")
                LINE_STATES[key] = {**state, "restaurant_name": restaurant_name}
            if isinstance(pending, dict):
                pending["restaurant_name"] = restaurant_name
                identity = pending.get("identity") if isinstance(pending.get("identity"), dict) else {}
                identity["confirmedName"] = restaurant_name
                pending["identity"] = identity
                reply_text(
                    reply_token,
                    f"這次辨識結果將使用店名「{restaurant_name}」，尚未存檔。請回覆「確認」正式建立，或回覆「取消」。",
                    channel_token,
                )
            else:
                if restaurant_name in RESTAURANT_MENUS:
                    reply_text(reply_token, f"已切換至「{restaurant_name}」。你可以直接說「推薦我晚餐」。", channel_token)
                else:
                    reply_text(reply_token, f"已記住餐廳「{restaurant_name}」。現在請傳送菜單或菜色照片。", channel_token)
            _remember_line_event(event_id)
    return {"ok": True}

@app.get("/health")
def health():
    return {
        "ok": True,
        "visionConfigured": bool(os.getenv("API_BASE_URL") and os.getenv("API_KEY") and os.getenv("VISION_MODEL")),
        "lineConfigured": bool(os.getenv("LINE_CHANNEL_SECRET") and os.getenv("LINE_CHANNEL_ACCESS_TOKEN")),
    }

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
        
        # 使用獨立 thread + ProactorEventLoop 跑爬蟲（避免與 uvicorn event loop 衝突）
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            restaurant = await loop.run_in_executor(pool, _run_crawler, restaurant_name)

        crawler_error = _crawler_error_message(restaurant)
        if crawler_error:
            return FoodpandaResp(
                success=False,
                message=crawler_error
            )
        
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
                message=_crawler_no_menu_message(restaurant_name)
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

@app.get("/api/restaurant-review")
def get_restaurant_review(restaurant_name: Optional[str] = None):
    """讀取餐廳評價快取；不觸發網路搜尋。"""
    target = restaurant_name or ACTIVE_RESTAURANT
    if not target:
        return {
            "success": False,
            "message": "尚未選擇餐廳",
            "restaurantName": None,
            "schemaVersion": 2,
            "restaurantIdentity": None,
            "needsIdentity": True,
            "needsRefresh": False,
            "updatedAt": None,
            "recommendationScore": 0,
            "confidenceScore": 0,
            "overallScore": 0,
            "sentiment": "unknown",
            "summary": "",
            "pros": [],
            "cons": [],
            "prosEvidence": [],
            "consEvidence": [],
            "recommendedFor": [],
            "aspects": {},
            "riskLevel": "unknown",
            "riskReasons": ["尚未選擇餐廳"],
            "riskSignals": [],
            "evidence": [],
            "sources": [],
        }
    return load_review_cache(PROJECT_ROOT, target)

@app.post("/api/restaurant-review/identify")
async def identify_restaurant_review(req: RestaurantReviewIdentifyReq):
    """搜尋可能的餐廳分店，交由使用者確認後才更新評價。"""
    restaurant_name = (req.restaurant_name or "").strip()
    if not restaurant_name:
        raise HTTPException(400, "restaurant_name 不可為空")
    if RESTAURANT_MENUS and restaurant_name not in RESTAURANT_MENUS:
        raise HTTPException(404, f"餐廳 '{restaurant_name}' 不存在")
    loop = asyncio.get_event_loop()
    try:
        candidates = await loop.run_in_executor(
            None,
            lambda: identify_restaurant_candidates(restaurant_name),
        )
        return {
            "success": bool(candidates),
            "restaurantName": restaurant_name,
            "candidates": candidates,
            "message": "請確認要分析的餐廳分店" if candidates else "找不到可確認的餐廳分店",
        }
    except Exception as e:
        print(f"[評價] 店家辨識失敗: {e}")
        raise HTTPException(500, f"店家辨識失敗: {str(e)}")

@app.post("/api/restaurant-review/refresh")
async def refresh_restaurant_review(req: RestaurantReviewRefreshReq):
    """手動更新餐廳評價情報，完成後寫入本機 JSON 快取。"""
    restaurant_name = (req.restaurant_name or "").strip()
    if not restaurant_name:
        raise HTTPException(400, "restaurant_name 不可為空")
    if RESTAURANT_MENUS and restaurant_name not in RESTAURANT_MENUS:
        raise HTTPException(404, f"餐廳 '{restaurant_name}' 不存在")

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(
            None,
            lambda: refresh_restaurant_reviews(
                PROJECT_ROOT,
                restaurant_name,
                req.restaurant_identity,
            ),
        )
    except Exception as e:
        print(f"[評價] 更新失敗: {e}")
        raise HTTPException(500, f"更新評價失敗: {str(e)}")

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
    menu_file = _safe_menu_path(restaurant_name)
    
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
        
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            restaurant = await loop.run_in_executor(pool, _run_crawler, restaurant_name)

        crawler_error = _crawler_error_message(restaurant)
        if crawler_error:
            return UpdateMenuResp(
                status="error",
                message=crawler_error
            )
        
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
                message=_crawler_no_menu_message(restaurant_name)
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
