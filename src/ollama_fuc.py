
import os, json, re, shutil, subprocess, random, time
from typing import Any, Dict, List, Optional, Tuple

# 修正導入路徑（src 目錄下要用 db.db_client）


#從環境變數讀取設定
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "gemma3:12b")
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
def chat(messages: List[Dict[str, str]], model: Optional[str] = None, timeout: float = 180.0) -> str:
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
    從傳入的 menu 參數（爬蟲抓取的菜單）進行推薦，而不是從資料庫查詢。
    這樣才能推薦正確的餐廳菜色。
    """
    # 調試：查看傳入的菜單結構
    print(f"\n [DEBUG] recommend() 被呼叫")
    print(f" [DEBUG] menu 的 keys: {list(menu.keys()) if isinstance(menu, dict) else 'NOT A DICT'}")
    if "restaurants" in menu:
        print(f" [DEBUG] 餐廳列表: {list(menu['restaurants'].keys())}")
    
    prefs = prefs or {}

    # 1) 解析偏好
    budget: Optional[float] = None
    if isinstance(prefs.get("budget"), (int, float, str)):
        try:
            budget = float(prefs["budget"])  # type: ignore[index]
        except Exception:
            budget = None

    exclude_keywords: List[str] = []
    if isinstance(prefs.get("excludes"), list):
        exclude_keywords = [str(x).lower() for x in prefs["excludes"]]  # type: ignore[index]
    
    # 不辣 → 排除含「辣」的品項
    if prefs.get("spiceLevel") == "不辣":
        exclude_keywords.append("辣")

    # 2) 從 menu 中提取所有菜品
    all_items: List[Dict[str, Any]] = []
    
    # 支援兩種菜單格式：
    # 格式1: {"restaurants": {"餐廳名": {"categories": {"分類": {"items": [...]}}}}}
    # 格式2: {"categories": [{"name": "分類", "items": [...]}]}
    
    if "restaurants" in menu and isinstance(menu["restaurants"], dict):
        # 格式1: 從 restaurants 中提取
        for restaurant_name, restaurant_data in menu["restaurants"].items():
            if isinstance(restaurant_data, dict) and "categories" in restaurant_data:
                categories = restaurant_data["categories"]
                if isinstance(categories, dict):
                    # categories 是字典格式
                    for cat_name, cat_data in categories.items():
                        if isinstance(cat_data, dict) and "items" in cat_data:
                            for item in cat_data["items"]:
                                if isinstance(item, dict):
                                    all_items.append({
                                        "name": item.get("name", ""),
                                        "price": item.get("price"),
                                        "category": cat_name,
                                        "restaurant": restaurant_name
                                    })
    elif "categories" in menu and isinstance(menu["categories"], list):
        # 格式2: 直接從 categories 列表提取
        for cat in menu["categories"]:
            if isinstance(cat, dict) and "items" in cat:
                cat_name = cat.get("name", "未分類")
                for item in cat["items"]:
                    if isinstance(item, dict):
                        all_items.append({
                            "name": item.get("name", ""),
                            "price": item.get("price"),
                            "category": cat_name
                        })

    print(f" [DEBUG] 從菜單提取了 {len(all_items)} 個項目")
    if all_items:
        print(f" [DEBUG] 前3個項目: {[item['name'] for item in all_items[:3]]}")

    if not all_items:
        return {
            "items": [],
            "notes": "菜單中沒有找到任何菜品",
            "meta": {
                "budget": budget,
                "people": prefs.get("people"),
                "needDrink": prefs.get("needDrink", False),
                "spiceLevel": prefs.get("spiceLevel"),
                "cuisine": prefs.get("cuisine"),
            }
        }

    # 3) 過濾：排除不想要的項目
    filtered_items = []
    for item in all_items:
        name = str(item.get("name", "")).lower()
        # 檢查是否包含排除關鍵字
        should_exclude = any(kw in name for kw in exclude_keywords)
        if not should_exclude:
            filtered_items.append(item)

    if not filtered_items:
        return {
            "items": [],
            "notes": "根據您的條件，沒有找到合適的菜品",
            "meta": {
                "budget": budget,
                "people": prefs.get("people"),
                "needDrink": prefs.get("needDrink", False),
                "spiceLevel": prefs.get("spiceLevel"),
                "cuisine": prefs.get("cuisine"),
            }
        }

    # 4) 價格提取函數
    def get_price(item: Dict[str, Any]) -> float:
        price = item.get("price")
        if price is None:
            return 999999.0  # 無價格的排最後
        if isinstance(price, str):
            # 提取價格數字，例如 "$109.00" -> 109.0
            import re
            match = re.search(r'[\d.]+', price)
            if match:
                try:
                    return float(match.group())
                except:
                    return 999999.0
            return 999999.0
        try:
            return float(price)
        except:
            return 999999.0

    # 5) 智能分類：將菜品分為主食、飲料、甜點、配菜、其他
    def classify_items_batch_with_llm(items: List[Dict[str, Any]]) -> Dict[str, str]:
        """ 使用 LLM 批次智能分類菜品（一次處理多個，提升效率）"""
        try:
            # 使用更小更快的模型
            model = os.environ.get("CLASSIFY_MODEL", "gemma3:12b")
            
            # 建立菜品列表字串
            items_text = "\n".join([f"{i+1}. {item.get('name', '')}" for i, item in enumerate(items)])
            
            prompt = f"""請分類以下菜品，每個菜品只回答一個分類代碼：
- main: 主食/主餐（漢堡、套餐、吐司、貝果、米飯、麵食等）
- side: 配菜/小食（薯條、雞塊、魚圈、蝦塊、沙拉等）
- drink: 飲料（茶、咖啡、可樂、果汁、奶茶、啤酒、紅酒、白酒、各種酒類等）
- dessert: 甜點（蛋撻、蛋糕、冰淇淋、派等）
- other: 其他

重要：所有酒類（啤酒、紅酒、白酒、威士忌等）都應分類為 drink（飲料）

菜品列表：
{items_text}

請依序回答每個菜品的分類，每行一個代碼（只寫 main/side/drink/dessert/other），範例：
main
drink
side
"""
            
            response = chat([{"role": "user", "content": prompt}], model=model, timeout=30.0)
            
            # 解析回應
            lines = [line.strip().lower() for line in response.split('\n') if line.strip()]
            result_map = {}
            
            for i, item in enumerate(items):
                if i < len(lines) and lines[i] in ["main", "side", "drink", "dessert", "other"]:
                    result_map[item.get("name", "")] = lines[i]
                else:
                    # 如果 LLM 回覆不完整，使用關鍵字分類
                    result_map[item.get("name", "")] = classify_item_keyword(item)
            
            return result_map
            
        except Exception as e:
            print(f" [LLM批次分類] 錯誤: {e}，降級使用關鍵字分類")
            # 降級：使用關鍵字分類
            return {item.get("name", ""): classify_item_keyword(item) for item in items}
    
    def classify_item_with_llm(item: Dict[str, Any]) -> str:
        """ 使用 LLM 智能分類單個菜品（僅在必要時使用）"""
        name = str(item.get("name", ""))
        
        try:
            model = os.environ.get("CLASSIFY_MODEL", "gemma3:12b")
            
            prompt = f"""請分類這道菜品屬於哪一類，只回答一個代碼：
- main: 主食/主餐（漢堡、套餐、吐司、貝果、米飯、麵食等）
- side: 配菜/小食（薯條、雞塊、魚圈、蝦塊、沙拉等）
- drink: 飲料（茶、咖啡、可樂、果汁、奶茶、啤酒、紅酒、白酒、各種酒類等）
- dessert: 甜點
- other: 其他

重要：所有酒類都應分類為 drink（飲料）

菜品名稱：{name}

只回答一個代碼（main/side/drink/dessert/other）："""
            
            response = chat([{"role": "user", "content": prompt}], model=model, timeout=20.0)
            result = response.strip().lower()
            
            if result in ["main", "side", "drink", "dessert", "other"]:
                return result
            else:
                return classify_item_keyword(item)
        except Exception as e:
            print(f" [LLM分類] 錯誤: {e}，使用關鍵字分類")
            return classify_item_keyword(item)
    
    def classify_item_keyword(item: Dict[str, Any]) -> str:
        """關鍵字分類（作為備用）"""
        name = str(item.get("name", "")).lower()
        
        # 飲料關鍵字（包含酒類）
        if any(kw in name for kw in ["茶", "飲料", "果汁", "咖啡", "奶茶", "可樂", "汽水", "豆漿", "拿鐵", "摩卡", "雪碧", "芬達", "氣泡", "啤酒", "紅酒", "白酒", "威士忌", "酒", "beer", "wine"]):
            return "drink"
        
        # 配菜/小食關鍵字（優先於主食判斷）
        if any(kw in name for kw in ["薯條", "雞塊", "魚圈", "蝦塊", "上校雞塊", "黃金", "青花椒", "沙拉", "蔬菜棒"]):
            return "side"
        
        # 甜點關鍵字
        if any(kw in name for kw in ["冰淇淋", "蛋糕", "甜點", "派", "可頌", "甜甜圈", "煉乳", "蛋撻", "起司", "大福", "QQ球", "比司吉"]):
            return "dessert"
        
        # 主食關鍵字（漢堡、吐司、貝果等）
        if any(kw in name for kw in ["堡", "漢堡", "burger", "吐司", "貝果", "三明治", "套餐", "義大利麵", "燉飯", "米堡", "麵", "飯", "獨享餐"]):
            return "main"
        
        return "other"
    
    def classify_item(item: Dict[str, Any]) -> str:
        """主要入口：優先使用 LLM，失敗時降級到關鍵字"""
        # 可以用環境變數控制是否啟用 LLM 分類
        USE_LLM_CLASSIFICATION = os.environ.get("USE_LLM_CLASSIFICATION", "true").lower() == "true"
        
        if USE_LLM_CLASSIFICATION:
            return classify_item_with_llm(item)
        else:
            return classify_item_keyword(item)

    # 檢查菜品是否符合使用者偏好
    def matches_preference(item: Dict[str, Any]) -> bool:
        preferred = prefs.get("preferredDish")
        if not preferred:
            return True  # 沒有指定偏好，都符合
        
        name = str(item.get("name", "")).lower()
        
        if preferred == "漢堡":
            return any(kw in name for kw in ["堡", "漢堡", "burger", "芝加哥"])
        elif preferred == "吐司":
            return "吐司" in name or "toast" in name
        elif preferred == "貝果":
            return "貝果" in name or "bagel" in name
        elif preferred == "套餐":
            return "套餐" in name or "combo" in name
        
        return True

    # 使用批次 LLM 分類所有菜品（更高效）
    print(f" [分類] 開始智能分類 {len(filtered_items)} 個菜品...")
    
    USE_LLM_CLASSIFICATION = os.environ.get("USE_LLM_CLASSIFICATION", "true").lower() == "true"
    
    if USE_LLM_CLASSIFICATION:
        # 批次分類：一次處理所有菜品
        classification_map = classify_items_batch_with_llm(filtered_items)
        print(f" [分類] LLM 批次分類完成")
    else:
        # 使用關鍵字分類
        classification_map = {item.get("name", ""): classify_item_keyword(item) for item in filtered_items}
        print(f" [分類] 關鍵字分類完成")
    
    # 將菜品分類到不同列表
    preferred_main = []
    other_main = []
    drink_items = []
    side_items = []
    dessert_items = []
    other_items = []
    
    for item in filtered_items:
        item_name = item.get("name", "")
        item_type = classification_map.get(item_name, "other")
        
        if item_type == "main":
            if matches_preference(item):
                preferred_main.append(item)
            else:
                other_main.append(item)
        elif item_type == "drink":
            drink_items.append(item)
        elif item_type == "side":
            side_items.append(item)
        elif item_type == "dessert":
            dessert_items.append(item)
        else:
            other_items.append(item)
    
    print(f" [分類結果] 主食:{len(preferred_main)+len(other_main)} 飲料:{len(drink_items)} 配菜:{len(side_items)} 甜點:{len(dessert_items)} 其他:{len(other_items)}")
    
    # 合併主食：優先推薦符合偏好的
    main_items = preferred_main + other_main

    # 排序邏輯改進：
    # - 如果有偏好，preferred_main 保持順序（或按價格排），other_main 按價格排
    # - 如果沒有偏好，所有主食按價格排
    has_preference = prefs.get("preferredDish") is not None
    
    import random
    
    if has_preference and preferred_main:
        # 有偏好：符合偏好的按價格排序，其他的也按價格排序
        preferred_main_sorted = sorted(preferred_main, key=get_price)
        other_main_sorted = sorted(other_main, key=get_price)
        # 優先選符合偏好的，然後才是其他的
        main_items_sorted = preferred_main_sorted + other_main_sorted
        print(f" [推薦] 有偏好，優先推薦符合偏好的主食（共 {len(preferred_main)} 項）")
    else:
        # 沒有偏好：按價格排序後添加隨機性
        main_items_sorted = sorted(main_items, key=get_price)
        
        # 添加隨機性：從前面較便宜的選項中隨機選擇
        if len(main_items_sorted) > 5:
            top_items = main_items_sorted[:min(15, len(main_items_sorted))]
            random.shuffle(top_items)
            main_items_sorted = top_items + main_items_sorted[15:]
    
    # 對其他類別也添加隨機性，避免每次推薦相同組合
    def add_randomness(items_list):
        """對排序後的列表添加隨機性"""
        if len(items_list) <= 3:
            return items_list
        sorted_items = sorted(items_list, key=get_price)
        # 從前 10 個中隨機選擇順序
        top_items = sorted_items[:min(10, len(sorted_items))]
        random.shuffle(top_items)
        return top_items + sorted_items[10:]
    
    drink_items_sorted = add_randomness(drink_items)
    side_items_sorted = add_randomness(side_items)
    dessert_items_sorted = add_randomness(dessert_items)
    other_items_sorted = sorted(other_items, key=get_price)  # 其他類別不需要隨機

    if preferred_main:
        print(f" [推薦] 符合偏好的前3項: {[item['name'] for item in preferred_main[:3]]}")
    if main_items_sorted:
        print(f" [推薦] 將推薦的主食前3項: {[item['name'] for item in main_items_sorted[:3]]}")

    # 6) 智能選擇：主食 + 配菜/飲料 組合
    selected_items: List[Dict[str, Any]] = []
    total_cost = 0.0
    
    # 調試輸出預算
    if budget and isinstance(budget, (int, float)):
        print(f" [預算控制] 使用者預算: ${budget:.0f}")

    # 優先選擇 1-2 個主食（套餐/主餐）
    main_count = 0
    for item in main_items_sorted:
        price = get_price(item)
        
        if budget and isinstance(budget, (int, float)):
            # 主食預算控制：確保總花費不超過預算的 60-65%（留空間給配菜/飲料）
            # 第一個主食：最多用 40% 預算
            # 第二個主食：確保兩個主食加起來不超過 60% 預算
            if main_count == 0:
                max_first_main = budget * 0.4
                if price > max_first_main:
                    print(f" [預算控制] 跳過主食 {item.get('name')} (${price:.0f}) - 超過第一主食限額 ${max_first_main:.0f}")
                    continue
            else:
                max_total_main = budget * 0.65
                if total_cost + price > max_total_main:
                    print(f" [預算控制] 跳過主食 {item.get('name')} (${price:.0f}) - 主食總額會超過 ${max_total_main:.0f}")
                    continue
        
        selected_items.append({
            "name": item.get("name"),
            "price": price if price < 999999.0 else None,
            "category": item.get("category", "未分類"),
            "reason": "主餐推薦"
        })
        
        total_cost += price
        main_count += 1
        
        if main_count >= 2:  # 最多選 2 個主食
            break

    print(f" [推薦] 已選主食 {main_count} 項，目前花費 ${total_cost:.0f}")

    # 選擇 0-1 個配菜（如果有預算空間）
    side_count = 0
    for item in side_items_sorted:
        price = get_price(item)
        
        if budget and isinstance(budget, (int, float)):
            # 嚴格檢查：加入此配菜後不能超過預算
            max_with_side = budget * 0.90  # 最多用到 90% 預算（留 10% 緩衝）
            if total_cost + price > max_with_side:
                print(f" [預算控制] 跳過配菜 {item.get('name')} (${price:.0f}) - 會超過 90% 預算限額")
                continue
        
        selected_items.append({
            "name": item.get("name"),
            "price": price if price < 999999.0 else None,
            "category": item.get("category", "未分類"),
            "reason": "搭配配菜"
        })
        
        total_cost += price
        side_count += 1
        
        if side_count >= 1:  # 最多 1 個配菜
            break

    if side_count > 0:
        print(f" [推薦] 已選配菜 {side_count} 項，目前花費 ${total_cost:.0f}")

    # 選擇飲料：檢查使用者是否要飲料
    need_drink = prefs.get("needDrink", True)  # 預設為 True
    drink_count = 0
    
    if need_drink:
        # 使用者要飲料，選擇 1-2 個
        for item in drink_items_sorted:
            price = get_price(item)
            
            if budget and isinstance(budget, (int, float)):
                # 嚴格檢查：不能超過預算
                if total_cost + price > budget:
                    print(f" [預算控制] 跳過飲料 {item.get('name')} (${price:.0f}) - 會超過預算 ${budget:.0f}")
                    continue
            
            selected_items.append({
                "name": item.get("name"),
                "price": price if price < 999999.0 else None,
                "category": item.get("category", "未分類"),
                "reason": "搭配飲品"
            })
            
            total_cost += price
            drink_count += 1
            
            if drink_count >= 2:  # 最多選 2 個飲料
                break
        
        if drink_count > 0:
            print(f" [推薦] 已選飲料 {drink_count} 項，目前花費 ${total_cost:.0f}")
    else:
        print(f" [推薦] 使用者不要飲料，跳過飲料推薦")

    if drink_count > 0:
        print(f" [推薦] 已選飲料 {drink_count} 項，目前花費 ${total_cost:.0f}")

    # 如果還有預算，考慮加入甜點
    dessert_count = 0
    for item in dessert_items_sorted:
        if len(selected_items) >= top_k:
            break
            
        price = get_price(item)
        
        if budget and isinstance(budget, (int, float)):
            if total_cost + price > budget:
                continue
        
        selected_items.append({
            "name": item.get("name"),
            "price": price if price < 999999.0 else None,
            "category": item.get("category", "未分類"),
            "reason": "搭配甜點"
        })
        
        total_cost += price
        dessert_count += 1
        
        if dessert_count >= 1:  # 最多 1 個甜點
            break

    if dessert_count > 0:
        print(f" [推薦] 已選甜點 {dessert_count} 項，目前花費 ${total_cost:.0f}")

    # 如果還有預算空間，加入其他項目
    for item in other_items_sorted:
        if len(selected_items) >= top_k:
            break
            
        price = get_price(item)
        
        if budget and isinstance(budget, (int, float)):
            if total_cost + price > budget:
                continue
        
        selected_items.append({
            "name": item.get("name"),
            "price": price if price < 999999.0 else None,
            "category": item.get("category", "未分類"),
            "reason": "額外推薦"
        })
        
        total_cost += price

    # 7) 如果一個都選不到（預算太低或沒有主食），就推薦最便宜的幾個主食
    if not selected_items:
        if main_items_sorted:
            for item in main_items_sorted[:min(2, top_k)]:
                selected_items.append({
                    "name": item.get("name"),
                    "price": get_price(item) if get_price(item) < 999999.0 else None,
                    "category": item.get("category", "未分類"),
                    "reason": "最經濟實惠的主餐"
                })
        # 如果還是沒有，就隨便推薦幾個
        if not selected_items and filtered_items:
            sorted_all = sorted(filtered_items, key=get_price)
            for item in sorted_all[:top_k]:
                selected_items.append({
                    "name": item.get("name"),
                    "price": get_price(item) if get_price(item) < 999999.0 else None,
                    "category": item.get("category", "未分類"),
                    "reason": "為您精選推薦"
                })

    notes = "" if selected_items else "找不到符合條件的菜品"
    
    # 組裝 meta 資訊
    meta = {
        "budget": budget,
        "people": prefs.get("people"),
        "needDrink": prefs.get("needDrink", False),
        "spiceLevel": prefs.get("spiceLevel"),
        "cuisine": prefs.get("cuisine"),
    }
    
    return {"items": selected_items, "notes": notes, "meta": meta}