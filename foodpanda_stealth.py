"""
Foodpanda 爬蟲 - 反檢測版本
使用多種技術繞過反爬蟲保護

策略：
1. 使用 Playwright Stealth 模式
2. 隱藏自動化特徵
3. 模擬真實使用者行為
4. 使用真實瀏覽器配置
"""
import asyncio
import json
import re
import random
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

try:
    from playwright.async_api import async_playwright, Browser, Page, BrowserContext
except ImportError as e:
    raise SystemExit("缺少 playwright，請先安裝：pip install playwright && playwright install chromium") from e


@dataclass
class FoodpandaMenuItem:
    name: str
    price: float
    description: Optional[str] = None
    category: Optional[str] = None


@dataclass
class FoodpandaRestaurant:
    name: str
    vendor_code: str
    url: str
    rating: Optional[float] = None
    delivery_time: Optional[str] = None
    menu_items: List[FoodpandaMenuItem] = None


async def create_stealth_context(browser: Browser) -> BrowserContext:
    """
    創建反檢測的瀏覽器上下文
    隱藏自動化特徵，模擬真實使用者
    """
    # 真實的 User-Agent（最新版 Chrome）
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ]
    
    context = await browser.new_context(
        user_agent=random.choice(user_agents),
        viewport={'width': 1920, 'height': 1080},
        locale='zh-TW',
        timezone_id='Asia/Taipei',
        # 真實的瀏覽器特徵
        extra_http_headers={
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
    )
    
    return context


async def apply_stealth_scripts(page: Page):
    """
    注入反檢測腳本
    隱藏 Playwright 的痕跡
    """
    # 隱藏 webdriver 屬性
    await page.add_init_script("""
        // 覆蓋 navigator.webdriver
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
        
        // 覆蓋 chrome 屬性
        window.chrome = {
            runtime: {}
        };
        
        // 覆蓋 permissions
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        
        // 覆蓋 plugins
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        
        // 覆蓋 languages
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-TW', 'zh', 'en-US', 'en']
        });
    """)


async def human_like_delay(min_ms: int = 500, max_ms: int = 2000):
    """模擬人類操作的隨機延遲"""
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))


async def search_foodpanda_stealth(page: Page, query: str, city: str = "taichung") -> List[FoodpandaRestaurant]:
    """
    反檢測版 Foodpanda 搜尋
    
    策略：
    1. 先訪問首頁建立 session
    2. 模擬真實使用者行為（滾動、移動滑鼠）
    3. 使用正常的導航流程
    """
    restaurants = []
    
    try:
        print("🌐 Step 1: 訪問首頁建立 session...")
        # 先訪問首頁，建立正常的 session
        await page.goto("https://www.foodpanda.com.tw", wait_until="domcontentloaded", timeout=30000)
        await human_like_delay(2000, 4000)
        
        # 模擬滾動行為
        print("🖱️  Step 2: 模擬真實使用者行為...")
        await page.evaluate("window.scrollBy(0, 300)")
        await human_like_delay(800, 1500)
        
        # 檢查是否有 CAPTCHA
        captcha_present = await page.query_selector('.px-captcha-container, .g-recaptcha')
        if captcha_present:
            print("⚠️  偵測到 CAPTCHA！")
            print("=" * 60)
            print("🔧 解決方案：")
            print("1. 手動完成驗證（瀏覽器會暫停 60 秒等待）")
            print("2. 或使用 2Captcha 等求解服務")
            print("=" * 60)
            
            # 等待使用者手動完成 CAPTCHA（60 秒）
            try:
                await page.wait_for_selector('.px-captcha-container', state='hidden', timeout=60000)
                print("✅ CAPTCHA 已完成！")
            except:
                print("❌ CAPTCHA 未完成，嘗試繼續...")
        
        # 方法 1：使用搜尋 URL（較不容易觸發檢測）
        print(f"🔍 Step 3: 搜尋「{query}」...")
        encoded_query = quote(query)
        
        # 使用城市特定的搜尋頁面
        search_urls = [
            f"https://www.foodpanda.com.tw/restaurants/new?q={encoded_query}&lat=24.1477&lng=120.6736",
            f"https://www.foodpanda.com.tw/restaurants/new?q={encoded_query}",
        ]
        
        for search_url in search_urls:
            print(f"📍 嘗試 URL: {search_url}")
            await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            await human_like_delay(3000, 5000)
            
            # 再次檢查 CAPTCHA
            captcha_present = await page.query_selector('.px-captcha-container, .g-recaptcha')
            if captcha_present:
                print("⚠️  再次偵測到 CAPTCHA，請手動完成...")
                await page.wait_for_timeout(60000)
            
            # 模擬滾動載入更多內容
            for _ in range(3):
                await page.evaluate("window.scrollBy(0, 500)")
                await human_like_delay(500, 1000)
            
            # 嘗試多種選擇器
            card_selectors = [
                'a[href*="/restaurant/"]',
                '[data-testid*="vendor"]',
                'a.vendor-item',
                'div[class*="vendor"]',
                'article',
            ]
            
            for selector in card_selectors:
                cards = await page.query_selector_all(selector)
                if cards and len(cards) > 0:
                    print(f"✅ 找到 {len(cards)} 個元素（選擇器：{selector}）")
                    
                    # 提取餐廳資訊
                    for card in cards[:10]:
                        try:
                            # 檢查是否包含 restaurant
                            href = await card.get_attribute('href')
                            if not href or '/restaurant/' not in href:
                                continue
                            
                            # 提取 vendor code
                            match = re.search(r'/restaurant/([^/?]+)', href)
                            if not match:
                                continue
                            
                            vendor_code = match.group(1)
                            url = f"https://www.foodpanda.com.tw{href}" if href.startswith('/') else href
                            
                            # 提取餐廳名稱
                            name = await card.inner_text()
                            name = name.split('\n')[0] if '\n' in name else name
                            name = name.strip()[:100]  # 限制長度
                            
                            if not name or len(name) < 2:
                                continue
                            
                            print(f"   📍 {name} ({vendor_code})")
                            
                            restaurants.append(FoodpandaRestaurant(
                                name=name,
                                vendor_code=vendor_code,
                                url=url,
                                menu_items=[]
                            ))
                            
                        except Exception as e:
                            continue
                    
                    if restaurants:
                        break
            
            if restaurants:
                break
        
        if not restaurants:
            print("❌ 未找到餐廳")
            # 保存 HTML 供除錯
            html = await page.content()
            Path("debug_foodpanda_stealth.html").write_text(html, encoding='utf-8')
            print("💾 已保存頁面到 debug_foodpanda_stealth.html")
        
        return restaurants
        
    except Exception as e:
        print(f"❌ 搜尋失敗：{e}")
        return []


async def crawl_menu_stealth(page: Page, restaurant: FoodpandaRestaurant) -> None:
    """反檢測版菜單爬取"""
    try:
        print(f"📖 爬取菜單：{restaurant.name}")
        
        await page.goto(restaurant.url, wait_until="domcontentloaded", timeout=30000)
        await human_like_delay(2000, 4000)
        
        # 模擬滾動
        for i in range(5):
            await page.evaluate(f"window.scrollBy(0, {random.randint(300, 800)})")
            await human_like_delay(500, 1200)
        
        # 嘗試多種菜單選擇器
        item_selectors = [
            'div[class*="dish"]',
            'li[class*="menu"]',
            'article[class*="product"]',
            '[data-testid*="product"]',
        ]
        
        items = []
        for selector in item_selectors:
            elements = await page.query_selector_all(selector)
            if elements and len(elements) > 0:
                print(f"   找到 {len(elements)} 個菜品元素")
                
                for elem in elements[:50]:  # 限制數量
                    try:
                        text = await elem.inner_text()
                        if not text or len(text) > 200:
                            continue
                        
                        lines = [l.strip() for l in text.split('\n') if l.strip()]
                        if not lines:
                            continue
                        
                        name = lines[0]
                        price = None
                        
                        # 尋找價格
                        for line in lines[1:]:
                            if any(p in line for p in ['NT', '$', '元']):
                                match = re.search(r'(\d+)', line.replace(',', ''))
                                if match:
                                    price = float(match.group(1))
                                    break
                        
                        if name and len(name) < 100:
                            items.append(FoodpandaMenuItem(
                                name=name,
                                price=price or 0
                            ))
                    
                    except Exception:
                        continue
                
                if items:
                    break
        
        restaurant.menu_items = items
        print(f"   ✅ 爬取到 {len(items)} 道菜")
        
    except Exception as e:
        print(f"   ❌ 爬取失敗：{e}")
        restaurant.menu_items = []


async def crawl_foodpanda_stealth(query: str, city: str = "taichung", headless: bool = False) -> List[FoodpandaRestaurant]:
    """
    完整的反檢測爬蟲流程
    
    Args:
        query: 搜尋關鍵字
        city: 城市
        headless: 是否無頭模式（False = 顯示瀏覽器，方便手動處理 CAPTCHA）
    """
    async with async_playwright() as p:
        print("🚀 啟動反檢測爬蟲...")
        print(f"📦 模式：{'無頭' if headless else '有頭（可手動處理 CAPTCHA）'}")
        
        # 使用 chromium 而非 chrome，較不容易被檢測
        browser = await p.chromium.launch(
            headless=headless,
            args=[
                '--disable-blink-features=AutomationControlled',  # 關鍵！
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu',
                '--lang=zh-TW',
            ]
        )
        
        # 創建反檢測上下文
        context = await create_stealth_context(browser)
        page = await context.new_page()
        
        # 注入反檢測腳本
        await apply_stealth_scripts(page)
        
        # 搜尋餐廳
        restaurants = await search_foodpanda_stealth(page, query, city)
        
        # 爬取菜單（只爬第一間）
        if restaurants and len(restaurants) > 0:
            print(f"\n📋 爬取第一間餐廳的菜單...")
            await crawl_menu_stealth(page, restaurants[0])
        
        await browser.close()
        return restaurants


def to_menu_json(restaurant: FoodpandaRestaurant) -> List[dict]:
    """轉換為標準菜單格式"""
    menu = []
    for item in restaurant.menu_items:
        menu.append({
            "restaurant": restaurant.name,
            "dish": item.name,
            "price": f"NT${int(item.price)}" if item.price else None,
            "source": "foodpanda",
            "source_url": restaurant.url,
        })
    return menu


async def main():
    """測試用主程式"""
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python foodpanda_stealth.py '搜尋關鍵字' [--headless]")
        print("範例: python foodpanda_stealth.py '牛排'")
        print("      python foodpanda_stealth.py '牛排' --headless")
        return
    
    query = sys.argv[1]
    headless = '--headless' in sys.argv
    
    print(f"🔍 搜尋：{query}")
    print("=" * 60)
    
    restaurants = await crawl_foodpanda_stealth(query, headless=headless)
    
    if restaurants:
        print("\n" + "=" * 60)
        print(f"✅ 成功找到 {len(restaurants)} 間餐廳")
        print("=" * 60)
        
        for i, r in enumerate(restaurants, 1):
            print(f"\n{i}. {r.name}")
            print(f"   URL: {r.url}")
            print(f"   菜單: {len(r.menu_items)} 道菜")
            
            if r.menu_items:
                print(f"   前 5 道：")
                for j, item in enumerate(r.menu_items[:5], 1):
                    print(f"      {j}. {item.name} - NT${item.price}")
    else:
        print("\n❌ 未找到餐廳或被 CAPTCHA 阻擋")
        print("\n💡 建議：")
        print("1. 再次執行（不使用 --headless）手動完成 CAPTCHA")
        print("2. 使用 2Captcha 等服務自動求解")
        print("3. 考慮使用替代方案（菜單編輯器）")


if __name__ == "__main__":
    asyncio.run(main())
