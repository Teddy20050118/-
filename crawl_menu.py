"""
全自動 Google 餐廳菜單爬蟲 v3.0
====================================
特點：
1. 完全自動化 - 無需手動點擊（除非失敗）
2. 自動啟動 Chrome 遠端調試
3. 智慧按鈕定位 - 多策略查找菜單按鈕
4. 搜尋不加「菜單」關鍵字
5. CSS 選擇器模組化 - 方便維護
"""

import asyncio
import json
import sys
import subprocess
import time
import socket
from dataclasses import dataclass, asdict
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ============================================================================
# CSS 選擇器常數
# ============================================================================

class Selectors:
    """Google 搜尋結果頁面的 CSS 選擇器"""
    INFO_PANEL = "#rhs"
    MENU_BTN_CLASS = ".aep93e"
    MENU_BTN_ROLE = "[role='button']"
    MENU_BTN_DIV = "div[role='button']"
    MENU_ITEM_NAME = ".bWZFsc"
    MENU_ITEM_PRICE = ".OCfJnf"

class Config:
    """爬蟲配置"""
    CDP_PORT = 9222
    CDP_URL = f"http://localhost:{CDP_PORT}"
    WAIT_PAGE_LOAD = 2000
    WAIT_BTN_CLICK = 1500
    WAIT_DATA_CHECK = 500
    MAX_CHECK_ATTEMPTS = 10
    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# ============================================================================
# 資料結構
# ============================================================================

@dataclass
class MenuItem:
    name: str
    price: str = "價格未提供"

@dataclass
class Restaurant:
    name: str
    menu_items: list = None

# ============================================================================
# 輔助函數
# ============================================================================

def check_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """檢查端口是否開啟"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((host, port))
        sock.close()
        return result == 0
    except:
        return False

def start_chrome_debug_mode():
    """啟動 Chrome 遠端調試模式"""
    print("\n[自動啟動] 嘗試啟動 Chrome 遠端調試模式...")
    
    if check_port_open('localhost', Config.CDP_PORT):
        print("  [OK] Chrome 遠端調試已在運行")
        return True
    
    try:
        # 使用 user-data-dir 來啟動獨立的 Chrome 實例
        import tempfile
        user_data_dir = tempfile.mkdtemp(prefix='chrome_debug_')
        
        chrome_cmd = [
            Config.CHROME_PATH,
            f"--remote-debugging-port={Config.CDP_PORT}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check"
        ]
        
        print(f"  => 啟動 Chrome...")
        subprocess.Popen(
            chrome_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        )
        
        print("  => 等待 Chrome 就緒...")
        for i in range(30):  # 增加到 30 秒
            time.sleep(1)
            if check_port_open('localhost', Config.CDP_PORT):
                print(f"  [OK] Chrome 已啟動（耗時 {i+1} 秒）")
                time.sleep(2)  # 額外等待 Chrome 完全就緒
                return True
            if i % 5 == 4:  # 每 5 秒顯示一次
                print(f"     等待中... {i+1}/30 秒")
        
        print("\n  [FAIL] 啟動超時")
        print("  提示：Chrome 可能已啟動但端口未就緒，請手動檢查")
        return False
    
    except Exception as e:
        print(f"  [FAIL] 啟動失敗: {e}")
        return False

async def wait_with_feedback(page, delay_ms: int, message: str = None):
    """等待並顯示進度反饋"""
    if message:
        print(f"  => {message}")
    await page.wait_for_timeout(delay_ms)

async def find_and_click_menu_button(page) -> bool:
    """【Phase 2: 智慧尋找並點擊菜單】"""
    print("\n" + "="*70)
    print("【Phase 2】智慧尋找菜單按鈕")
    print("="*70)
    
    await page.wait_for_load_state('domcontentloaded', timeout=10000)
    await wait_with_feedback(page, 1500, "等待 JavaScript 渲染完成...")
    
    # 策略 1: 檢查右側資訊欄
    print("\n[策略 1] 檢查右側資訊欄...")
    try:
        rhs = page.locator(Selectors.INFO_PANEL)
        
        if await rhs.count() > 0:
            print("  [OK] 找到右側資訊欄 (#rhs)")
            
            menu_btn = rhs.locator(Selectors.MENU_BTN_CLASS).filter(has_text="菜單")
            if await menu_btn.count() > 0 and await menu_btn.first.is_visible():
                print("  [OK] 找到 .aep93e 菜單按鈕")
                await menu_btn.first.click()
                await wait_with_feedback(page, Config.WAIT_BTN_CLICK, "點擊成功，等待內容載入...")
                return True
            
            menu_btn = rhs.locator(Selectors.MENU_BTN_DIV).filter(has_text="菜單")
            if await menu_btn.count() > 0 and await menu_btn.first.is_visible():
                print("  [OK] 找到 div[role='button'] 菜單按鈕")
                await menu_btn.first.evaluate("el => el.click()")
                await wait_with_feedback(page, Config.WAIT_BTN_CLICK, "JS 點擊成功，等待內容載入...")
                return True
            
            print("  [FAIL] 資訊欄內未找到菜單按鈕")
        else:
            print("  [FAIL] 未找到右側資訊欄")
    except Exception as e:
        print(f"  [FAIL] 策略 1 失敗: {str(e)[:80]}")
    
    # 策略 2: 全頁面搜尋
    print("\n[策略 2] 全頁面搜尋 role=button...")
    try:
        menu_btns = page.locator(Selectors.MENU_BTN_ROLE).filter(has_text="菜單")
        
        if await menu_btns.count() > 0:
            for i in range(await menu_btns.count()):
                btn = menu_btns.nth(i)
                if await btn.is_visible():
                    print(f"  [OK] 找到第 {i+1} 個菜單按鈕")
                    await btn.scroll_into_view_if_needed()
                    await btn.click()
                    await wait_with_feedback(page, Config.WAIT_BTN_CLICK, "點擊成功，等待內容載入...")
                    return True
        
        print("  [FAIL] 未找到可見的菜單按鈕")
    except Exception as e:
        print(f"  [FAIL] 策略 2 失敗: {str(e)[:80]}")
    
    # 策略 3: 導航列
    print("\n[策略 3] 檢查導航列...")
    try:
        nav_menu = page.get_by_text("菜單", exact=True)
        
        if await nav_menu.count() > 0 and await nav_menu.first.is_visible():
            print("  [OK] 找到導航列的「菜單」連結")
            await nav_menu.first.click()
            await wait_with_feedback(page, Config.WAIT_BTN_CLICK, "點擊成功，等待內容載入...")
            return True
        
        print("  [FAIL] 導航列無菜單連結")
    except Exception as e:
        print(f"  [FAIL] 策略 3 失敗: {str(e)[:80]}")
    
    print("\n" + "="*70)
    print("[WARNING] 所有自動點擊策略均失敗")
    print("="*70)
    
    try:
        await page.screenshot(path='debug_no_menu_button.png')
        print("[SCREENSHOT] 已儲存除錯截圖: debug_no_menu_button.png")
    except:
        pass
    
    return False

async def check_menu_loaded(page) -> bool:
    """檢查菜單內容是否已載入"""
    print("\n[檢查] 偵測菜單內容...")
    
    for attempt in range(Config.MAX_CHECK_ATTEMPTS):
        count = await page.locator(Selectors.MENU_ITEM_NAME).count()
        
        if count > 0:
            print(f"  [OK] 已偵測到 {count} 個菜單項目")
            return True
        
        dots = "." * (attempt + 1)
        print(f"  => 等待中{dots} ({attempt + 1}/{Config.MAX_CHECK_ATTEMPTS})")
        await page.wait_for_timeout(Config.WAIT_DATA_CHECK)
    
    print("  [FAIL] 未偵測到菜單內容")
    return False

async def extract_menu_data(page, restaurant_name: str) -> Restaurant:
    """【Phase 3: 資料抓取】"""
    print("\n" + "="*70)
    print("【Phase 3】資料抓取")
    print("="*70)
    
    menu_items = []
    seen_names = set()
    
    try:
        await page.wait_for_selector(Selectors.MENU_ITEM_NAME, timeout=10000)
        
        name_elements = page.locator(Selectors.MENU_ITEM_NAME)
        item_count = await name_elements.count()
        
        print(f"\n開始抓取 {item_count} 個菜單項目...")
        print("-" * 70)
        
        for i in range(item_count):
            try:
                name_elem = name_elements.nth(i)
                name = await name_elem.inner_text()
                name = name.strip()
                
                if not name or len(name) < 2 or name in seen_names:
                    continue
                
                price = "價格未提供"
                
                try:
                    parent = name_elem.locator('xpath=..')
                    next_sibling = parent.locator('xpath=following-sibling::*[1]')
                    
                    if await next_sibling.count() > 0:
                        class_name = await next_sibling.get_attribute('class')
                        
                        if class_name and 'OCfJnf' in class_name:
                            aria_label = await next_sibling.get_attribute('aria-label')
                            if aria_label:
                                price = aria_label.strip().rstrip('.')
                            else:
                                price_text = await next_sibling.inner_text()
                                if price_text:
                                    price = price_text.strip()
                except:
                    try:
                        all_prices = page.locator(Selectors.MENU_ITEM_PRICE)
                        if i < await all_prices.count():
                            price_elem = all_prices.nth(i)
                            aria_label = await price_elem.get_attribute('aria-label')
                            if aria_label:
                                price = aria_label.strip().rstrip('.')
                            else:
                                price_text = await price_elem.inner_text()
                                if price_text:
                                    price = price_text.strip()
                    except:
                        pass
                
                menu_items.append(MenuItem(name=name, price=price))
                seen_names.add(name)
                
                print(f"  {len(menu_items):3d}. {name[:45]:45s} │ {price}")
                
            except Exception as e:
                continue
        
        print("-" * 70)
        print(f"[SUCCESS] 成功抓取 {len(menu_items)} 道菜\n")
        
        return Restaurant(name=restaurant_name, menu_items=menu_items)
    
    except PlaywrightTimeout:
        print("[ERROR] 等待菜單元素超時")
        return Restaurant(name=restaurant_name, menu_items=[])
    except Exception as e:
        print(f"[ERROR] 抓取失敗: {e}")
        import traceback
        traceback.print_exc()
        return Restaurant(name=restaurant_name, menu_items=[])

async def crawl_google_menu(restaurant_name: str) -> Restaurant:
    """【主流程】全自動爬取 Google 餐廳菜單"""
    
    print("\n" + "="*70)
    print("全自動 Google 餐廳菜單爬蟲 v3.0")
    print("="*70)
    print(f"目標餐廳: {restaurant_name}")
    print(f"CDP 端口: {Config.CDP_PORT}")
    print("="*70)
    
    async with async_playwright() as p:
        try:
            # ================================================================
            # Phase 1: 連接 Chrome & 搜尋
            # ================================================================
            print("\n【Phase 1】連接 Chrome 並搜尋餐廳")
            print("="*70)
            
            # 確保 Chrome 遠端調試模式已啟動
            if not check_port_open('localhost', Config.CDP_PORT):
                print("[自動化] Chrome 遠端調試未運行，嘗試自動啟動...")
                if not start_chrome_debug_mode():
                    print("\n[ERROR] 無法自動啟動 Chrome")
                    print("\n請手動啟動 Chrome 遠端調試模式：")
                    print(f"  步驟 1: 關閉所有 Chrome 視窗")
                    print(f"  步驟 2: 在命令提示字元執行：")
                    print(f'    cd "C:\\Program Files\\Google\\Chrome\\Application"')
                    print(f'    chrome.exe --remote-debugging-port={Config.CDP_PORT}')
                    print("\n  或者直接執行：")
                    print(f"  '{Config.CHROME_PATH}' --remote-debugging-port={Config.CDP_PORT}")
                    return None
            else:
                print("[自動化] Chrome 遠端調試已在運行")
            
            # 連接到本機 Chrome
            print(f"\n[1/3] 連接到 Chrome (CDP: {Config.CDP_URL})...")
            try:
                browser = await p.chromium.connect_over_cdp(Config.CDP_URL)
                print("  [OK] 連接成功")
            except Exception as e:
                print(f"  [FAIL] 連接失敗: {e}")
                print("\n可能原因：")
                print("  1. Chrome 啟動中但尚未完全就緒")
                print("  2. 端口被其他程式佔用")
                print("  3. 防火牆阻擋連接")
                print("\n建議：請手動啟動 Chrome 後重試")
                return None
            
            # 取得或創建頁面
            print("\n[2/3] 取得瀏覽器頁面...")
            contexts = browser.contexts
            if not contexts:
                print("  [FAIL] 沒有可用的瀏覽器上下文")
                return None
            
            if contexts[0].pages:
                page = contexts[0].pages[0]
                print("  [OK] 使用現有頁面")
            else:
                page = await contexts[0].new_page()
                print("  [OK] 創建新頁面")
            
            # 搜尋餐廳（不加「菜單」關鍵字）
            print(f"\n[3/3] 搜尋餐廳: {restaurant_name}")
            print("  [NOTE] 搜尋參數不包含「菜單」關鍵字")
            
            search_url = f"https://www.google.com/search?q={restaurant_name}"
            print(f"  => 導航至: {search_url}")
            
            try:
                await page.goto(search_url, wait_until='domcontentloaded', timeout=30000)
                print("  [OK] 頁面載入成功")
            except Exception as e:
                print(f"  [FAIL] 頁面載入失敗: {e}")
                print("  => 嘗試重新載入...")
                try:
                    await page.goto(search_url, wait_until='networkidle', timeout=30000)
                    print("  [OK] 重新載入成功")
                except:
                    print("  [FAIL] 重新載入失敗")
                    return None
            
            await wait_with_feedback(page, Config.WAIT_PAGE_LOAD, "等待搜尋結果完全載入...")
            
            # 驗證是否在正確的頁面
            current_url = page.url
            if 'google.com/search' in current_url:
                print(f"  [OK] 確認在搜尋結果頁面")
            else:
                print(f"  [WARNING] 當前頁面: {current_url}")
            
            print("  [OK] Phase 1 完成\n")
            
            # ================================================================
            # Phase 2: 智慧點擊菜單按鈕
            # ================================================================
            click_success = await find_and_click_menu_button(page)
            
            # ================================================================
            # Phase 4: 錯誤處理 - 手動輔助模式
            # ================================================================
            if not click_success:
                print("\n" + "="*70)
                print("[WARNING] 自動化失敗，切換至【手動輔助模式】")
                print("="*70)
                print("請在瀏覽器中手動執行以下操作：")
                print("  1. 確認是否顯示餐廳資訊卡（右側）")
                print("  2. 手動點擊「菜單」標籤")
                print("  3. 完成後按 Enter 繼續抓取")
                print("="*70)
                input("\n按 Enter 繼續...")
            
            # 檢查菜單是否載入
            menu_loaded = await check_menu_loaded(page)
            
            if not menu_loaded:
                print("\n" + "="*70)
                print("[ERROR] 最終檢查失敗：無法偵測到菜單內容")
                print("="*70)
                return Restaurant(name=restaurant_name, menu_items=[])
            
            # ================================================================
            # Phase 3: 資料抓取
            # ================================================================
            restaurant = await extract_menu_data(page, restaurant_name)
            
            return restaurant
        
        except Exception as e:
            print(f"\n[ERROR] 爬蟲執行失敗: {e}")
            import traceback
            traceback.print_exc()
            return Restaurant(name=restaurant_name, menu_items=[])

# ============================================================================
# 對外介面
# ============================================================================

async def quick_crawl(restaurant_name: str) -> Restaurant:
    """快速爬取介面（供後端 API 調用）"""
    return await crawl_google_menu(restaurant_name)

# ============================================================================
# 命令列執行入口
# ============================================================================

async def main():
    """命令列執行主程式"""
    
    print("\n" + "="*70)
    print("Google 餐廳菜單爬蟲（全自動化版本）")
    print("="*70)
    
    if len(sys.argv) > 1:
        restaurant_name = sys.argv[1]
    else:
        restaurant_name = input("\n請輸入餐廳名稱（例如：麥當勞大甲）: ").strip()
    
    if not restaurant_name:
        print("[ERROR] 未輸入餐廳名稱，程式結束")
        return
    
    restaurant = await crawl_google_menu(restaurant_name)
    
    if restaurant and restaurant.menu_items and len(restaurant.menu_items) > 0:
        print("\n" + "="*70)
        print("💾 儲存結果")
        print("="*70)
        
        filename = f"menu_{restaurant.name.replace(' ', '_')}.json"
        file_path = Path(filename)
        
        file_path.write_text(
            json.dumps(asdict(restaurant), ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
        
        print(f"[SUCCESS] 已儲存: {filename}")
        print(f"[INFO] 菜單項目數: {len(restaurant.menu_items)}")
        print("="*70)
    else:
        print("\n" + "="*70)
        print("[ERROR] 爬取失敗或無資料")
        print("="*70)

if __name__ == '__main__':
    if sys.platform.startswith('win32'):
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    asyncio.run(main())
