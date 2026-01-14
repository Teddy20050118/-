"""
Foodpanda 外送平台爬蟲
從 foodpanda.com.tw 爬取餐廳菜單資料
"""
import asyncio
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

try:
    from playwright.async_api import async_playwright, Browser, Page
except ImportError as e:
    raise SystemExit("缺少 playwright，請先安裝：pip install playwright && playwright install chromium") from e


@dataclass
class FoodpandaMenuItem:
    name: str
    price: float
    description: Optional[str] = None
    category: Optional[str] = None
    image_url: Optional[str] = None


@dataclass
class FoodpandaRestaurant:
    name: str
    vendor_code: str  # Foodpanda 餐廳代碼
    url: str
    rating: Optional[float] = None
    delivery_time: Optional[str] = None
    delivery_fee: Optional[str] = None
    min_order: Optional[str] = None
    cuisines: List[str] = None
    menu_items: List[FoodpandaMenuItem] = None


async def search_foodpanda(page: Page, query: str, city: str = "taichung") -> List[FoodpandaRestaurant]:
    """
    在 Foodpanda 搜尋餐廳
    
    Args:
        query: 搜尋關鍵字（例如：「史堤克牛排」）
        city: 城市代碼（taipei, taichung, kaohsiung 等）
    
    Returns:
        餐廳列表（含 vendor_code 供後續爬取菜單）
    """
    # Foodpanda 搜尋頁面
    search_url = f"https://www.foodpanda.com.tw/city/{city}"
    
    try:
        await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        
        # 尋找搜尋框並輸入關鍵字
        search_input_selectors = [
            'input[placeholder*="搜尋"]',
            'input[placeholder*="Search"]',
            'input[type="search"]',
            'input[data-testid="search-input"]',
        ]
        
        search_input = None
        for selector in search_input_selectors:
            search_input = await page.query_selector(selector)
            if search_input:
                break
        
        if not search_input:
            print("未找到搜尋框")
            return []
        
        # 輸入搜尋關鍵字
        await search_input.fill(query)
        await search_input.press('Enter')
        await page.wait_for_timeout(3000)
        
        # 提取搜尋結果
        restaurants = []
        
        # Foodpanda 餐廳卡片選擇器
        card_selectors = [
            'a[data-testid^="vendor-"]',
            '[data-testid="vendor-card"]',
            'a[href*="/restaurant/"]',
        ]
        
        cards = []
        for selector in card_selectors:
            cards = await page.query_selector_all(selector)
            if cards:
                break
        
        for card in cards[:10]:  # 最多取 10 間
            try:
                # 提取餐廳 URL 和代碼
                href = await card.get_attribute('href')
                if not href or '/restaurant/' not in href:
                    continue
                
                # 提取 vendor code（例如：/restaurant/s1ab/some-restaurant）
                match = re.search(r'/restaurant/([^/]+)', href)
                vendor_code = match.group(1) if match else None
                
                if not vendor_code:
                    continue
                
                url = f"https://www.foodpanda.com.tw{href}" if href.startswith('/') else href
                
                # 提取餐廳名稱
                name_el = await card.query_selector('[data-testid="vendor-name"], h3, h2, .name')
                name = (await name_el.inner_text()).strip() if name_el else '(未命名)'
                
                # 提取評分
                rating = None
                rating_el = await card.query_selector('[data-testid="vendor-rating"], .rating')
                if rating_el:
                    rating_text = (await rating_el.inner_text()).strip()
                    try:
                        rating = float(re.search(r'(\d+\.?\d*)', rating_text).group(1))
                    except (AttributeError, ValueError):
                        pass
                
                # 提取外送時間
                delivery_time = None
                time_el = await card.query_selector('[data-testid="vendor-delivery-time"], .delivery-time')
                if time_el:
                    delivery_time = (await time_el.inner_text()).strip()
                
                restaurants.append(FoodpandaRestaurant(
                    name=name,
                    vendor_code=vendor_code,
                    url=url,
                    rating=rating,
                    delivery_time=delivery_time,
                    menu_items=[]
                ))
                
            except Exception as e:
                print(f"解析餐廳卡片失敗: {e}")
                continue
        
        return restaurants
        
    except Exception as e:
        print(f"搜尋失敗: {e}")
        return []


async def crawl_foodpanda_menu(page: Page, restaurant: FoodpandaRestaurant) -> None:
    """
    爬取 Foodpanda 餐廳的完整菜單
    
    Args:
        restaurant: 餐廳物件（必須包含 url 或 vendor_code）
    """
    try:
        # 前往餐廳頁面
        await page.goto(restaurant.url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)
        
        # 滾動頁面載入所有內容
        for _ in range(5):
            await page.evaluate('window.scrollBy(0, 800)')
            await page.wait_for_timeout(800)
        
        # 提取菜單項目
        items = []
        
        # Foodpanda 菜單項目選擇器
        item_selectors = [
            '[data-testid^="menu-product-"]',
            '[data-testid="dish-card"]',
            '.dish-card',
            'li[data-testid*="product"]',
        ]
        
        item_elements = []
        for selector in item_selectors:
            item_elements = await page.query_selector_all(selector)
            if item_elements:
                break
        
        for item_el in item_elements:
            try:
                # 提取菜名
                name_el = await item_el.query_selector('[data-testid="dish-name"], .dish-name, h3, h4')
                name = (await name_el.inner_text()).strip() if name_el else None
                
                if not name:
                    continue
                
                # 提取價格
                price = None
                price_el = await item_el.query_selector('[data-testid="dish-price"], .price, .dish-price')
                if price_el:
                    price_text = (await price_el.inner_text()).strip()
                    # 提取數字（例如：NT$150 -> 150）
                    match = re.search(r'(\d+\.?\d*)', price_text.replace(',', ''))
                    if match:
                        price = float(match.group(1))
                
                # 提取描述
                description = None
                desc_el = await item_el.query_selector('[data-testid="dish-description"], .description')
                if desc_el:
                    description = (await desc_el.inner_text()).strip()
                
                # 提取圖片
                image_url = None
                img_el = await item_el.query_selector('img')
                if img_el:
                    image_url = await img_el.get_attribute('src')
                
                items.append(FoodpandaMenuItem(
                    name=name,
                    price=price or 0,
                    description=description,
                    image_url=image_url
                ))
                
            except Exception as e:
                print(f"解析菜品失敗: {e}")
                continue
        
        restaurant.menu_items = items
        
    except Exception as e:
        print(f"爬取菜單失敗 {restaurant.name}: {e}")
        restaurant.menu_items = []


async def crawl_foodpanda(restaurant_name: str, city: str = "taichung") -> Optional[FoodpandaRestaurant]:
    """
    完整流程：搜尋餐廳 + 爬取菜單
    
    Args:
        restaurant_name: 餐廳名稱（例如：「史堤克牛排大甲店」）
        city: 城市代碼
    
    Returns:
        包含完整菜單的餐廳物件
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        
        # 1. 搜尋餐廳
        print(f"在 Foodpanda 搜尋：{restaurant_name}")
        restaurants = await search_foodpanda(page, restaurant_name, city)
        
        if not restaurants:
            print("未找到餐廳")
            await browser.close()
            return None
        
        # 2. 選擇第一間餐廳（或最相關的）
        target_restaurant = restaurants[0]
        print(f"找到餐廳：{target_restaurant.name} ({target_restaurant.url})")
        
        # 3. 爬取菜單
        print("正在爬取菜單...")
        await crawl_foodpanda_menu(page, target_restaurant)
        print(f"完成！共 {len(target_restaurant.menu_items)} 道菜")
        
        await browser.close()
        return target_restaurant


def to_menu_json(restaurant: FoodpandaRestaurant) -> List[dict]:
    """轉換為統一的菜單 JSON 格式"""
    menu = []
    for item in restaurant.menu_items:
        menu.append({
            "restaurant": restaurant.name,
            "dish": item.name,
            "price": f"NT${int(item.price)}" if item.price else None,
            "description": item.description,
            "source": "foodpanda",
            "source_url": restaurant.url,
            "rating": restaurant.rating,
        })
    return menu


async def main():
    """測試用主程式"""
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python foodpanda_crawler.py '餐廳名稱' [城市]")
        print("範例: python foodpanda_crawler.py '史堤克牛排' taichung")
        return
    
    restaurant_name = sys.argv[1]
    city = sys.argv[2] if len(sys.argv) > 2 else "taichung"
    
    restaurant = await crawl_foodpanda(restaurant_name, city)
    
    if restaurant:
        print(f"\n餐廳：{restaurant.name}")
        print(f"評分：{restaurant.rating}")
        print(f"外送時間：{restaurant.delivery_time}")
        print(f"\n菜單（前 10 道）：")
        for i, item in enumerate(restaurant.menu_items[:10], 1):
            print(f"{i}. {item.name} - NT${int(item.price)}")
            if item.description:
                print(f"   {item.description}")
        
        # 儲存到檔案
        menu_json = to_menu_json(restaurant)
        output_file = Path(f"menu_{restaurant.vendor_code}.json")
        output_file.write_text(json.dumps(menu_json, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f"\n已儲存到：{output_file}")


if __name__ == "__main__":
    asyncio.run(main())
