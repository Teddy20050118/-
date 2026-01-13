import asyncio
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

try:
    from playwright.async_api import async_playwright, Browser
except ImportError as e:
    raise SystemExit("缺少 playwright，請先安裝：pip install playwright && playwright install chromium") from e


@dataclass
class MenuItem:
    name: str
    price: Optional[str] = None
    category: Optional[str] = None


@dataclass
class Restaurant:
    name: str
    rating: Optional[float] = None
    price_level: Optional[str] = None
    address: Optional[str] = None
    url: Optional[str] = None
    menu_items: List[MenuItem] = None


def _safe_text(el) -> str:
    return (el.inner_text() if el else '').strip()


def _normalize_price_level(text: str) -> Optional[str]:
    t = (text or '').strip()
    if not t:
        return None
    if all(ch == '$' for ch in t):
        return str(len(t))
    return t


async def _extract_restaurants(page, max_results: int) -> List[Restaurant]:
    """從搜尋結果頁面提取餐廳列表"""
    # 等待搜尋結果載入
    await page.wait_for_timeout(2000)
    
    # 嘗試多種選擇器找到餐廳卡片
    card_selectors = [
        '[role="article"]',
        '.Nv2PK',
        'div.hfpxzc',
        'a.hfpxzc',
        'div[jsaction*="mouseover"]'
    ]
    
    cards = []
    for selector in card_selectors:
        cards = await page.query_selector_all(selector)
        if cards and len(cards) > 0:
            break
    
    results: List[Restaurant] = []
    
    for card in cards:
        if len(results) >= max_results:
            break
        
        # 提取餐廳名稱
        name = None
        name_selectors = [
            '.fontHeadlineSmall',
            '.qBF1Pd',
            '.fontHeadlineLarge',
            'a.hfpxzc',
            'div.qBF1Pd',
            '[class*="title"]',
        ]
        
        for ns in name_selectors:
            name_el = await card.query_selector(ns)
            if name_el:
                name = (await name_el.inner_text()).strip()
                if name:
                    break
        
        # 提取評分
        rating = None
        rating_selectors = [
            '.MW4etd',
            '.KFi5wf',
            'span[role="img"]',
            '[aria-label*="星"]',
        ]
        
        for rs in rating_selectors:
            rating_el = await card.query_selector(rs)
            if rating_el:
                rating_text = (await rating_el.inner_text()).strip()
                try:
                    rating = float(rating_text.split()[0].replace(',', '.'))
                    break
                except (ValueError, IndexError):
                    continue
        
        # 提取價格等級
        price_level = None
        price_selectors = [
            '.UzM3td',
            'span[aria-label*="Price"]',
            'span[aria-label*="價格"]',
        ]
        
        for ps in price_selectors:
            price_el = await card.query_selector(ps)
            if price_el:
                price_level = _normalize_price_level(await price_el.inner_text())
                if price_level:
                    break
        
        # 提取地址
        address = None
        address_selectors = [
            '.W4Efsd:nth-of-type(2)',
            '.Io6YTe',
            'div[class*="address"]',
        ]
        
        for ads in address_selectors:
            address_el = await card.query_selector(ads)
            if address_el:
                address = (await address_el.inner_text()).strip()
                if address:
                    break
        
        # 提取連結
        url = None
        url_el = await card.query_selector('a[href*="maps"]')
        if url_el:
            url = await url_el.get_attribute('href')
            # 確保 URL 是完整的
            if url and not url.startswith('http'):
                url = f"https://www.google.com{url}"
        
        # 只添加有名稱的餐廳
        if name:
            results.append(Restaurant(
                name=name,
                rating=rating,
                price_level=price_level,
                address=address,
                url=url,
                menu_items=[]
            ))
    
    return results


async def _extract_menu_from_place(page, restaurant: Restaurant, max_items_per_shop: int = 40) -> None:
    """進入餐廳詳細頁面，尋找並爬取菜單資料"""
    if not restaurant.url:
        return
    try:
        # 前往餐廳詳細頁面
        await page.goto(restaurant.url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(3000)  # 增加等待時間
        
        # 嘗試滾動頁面以載入更多內容
        try:
            scrollable = await page.query_selector('[role="main"]')
            if scrollable:
                for _ in range(3):  # 滾動 3 次
                    await scrollable.evaluate('el => el.scrollBy(0, 300)')
                    await page.wait_for_timeout(500)
        except Exception:
            pass
        
        # 嘗試找到並點擊「菜單」標籤
        menu_tab_texts = ['菜單', 'Menu', '메뉴', 'メニュー', 'Menú', 'Cardápio']
        menu_tab_clicked = False
        
        for text in menu_tab_texts:
            try:
                # 嘗試通過按鈕文字找到菜單標籤
                buttons = await page.query_selector_all('button')
                for btn in buttons:
                    btn_text = (await btn.inner_text()).strip()
                    if text.lower() in btn_text.lower():
                        await btn.click()
                        await page.wait_for_timeout(2000)
                        menu_tab_clicked = True
                        break
                if menu_tab_clicked:
                    break
            except Exception:
                continue
        
        # 如果沒找到菜單標籤，嘗試尋找 Overview 下的菜單資訊
        items: List[MenuItem] = []
        
        # 策略 1: 嘗試抓取所有可能是菜單項目的元素
        # Google Maps 的菜單項目可能在不同的結構中
        
        # 先嘗試找到包含菜單的容器
        menu_containers = await page.query_selector_all('[role="region"], [role="tabpanel"], .m6QErb, section')
        
        for container in menu_containers:
            container_text = (await container.inner_text())[:100] if container else ''
            
            # 尋找可能的菜單項目
            potential_items = await container.query_selector_all('div[jsaction], div[role="listitem"], li')
            
            for elem in potential_items:
                if len(items) >= max_items_per_shop:
                    break
                
                try:
                    elem_text = await elem.inner_text()
                    if not elem_text or len(elem_text) > 200:  # 跳過空的或太長的
                        continue
                    
                    lines = [l.strip() for l in elem_text.split('\n') if l.strip()]
                    if not lines:
                        continue
                    
                    # 第一行通常是菜名
                    title = lines[0]
                    
                    # 跳過明顯不是菜名的內容
                    skip_keywords = ['評論', '評分', 'reviews', 'photos', '相片', '照片', '地址', 'address', 
                                   '營業時間', 'hours', '電話', 'phone', '網站', 'website']
                    if any(kw in title.lower() for kw in skip_keywords):
                        continue
                    
                    if len(title) > 50 or len(title) < 2:  # 菜名長度合理性檢查
                        continue
                    
                    # 尋找價格（可能在第二行或同一行）
                    price = None
                    price_patterns = ['$', '€', '£', '¥', '₹', '₩', 'NT', '元', 'TWD']
                    
                    for line in lines[1:3]:  # 檢查接下來的 2 行
                        if any(p in line for p in price_patterns):
                            price = line
                            break
                    
                    # 檢查是否已經添加過相同的項目
                    if not any(item.name == title for item in items):
                        items.append(MenuItem(name=title, price=price))
                        
                except Exception:
                    continue
            
            if items:  # 如果已經找到項目就停止
                break
        
        # 策略 2: 如果上面沒找到，嘗試更通用的方法 - 抓取所有包含價格符號的文字塊
        if not items:
            all_divs = await page.query_selector_all('div')
            for div in all_divs[:200]:  # 限制查找範圍避免太慢
                if len(items) >= max_items_per_shop:
                    break
                try:
                    text = await div.inner_text()
                    if not text or len(text) > 100:
                        continue
                    
                    # 檢查是否包含價格
                    has_price = any(p in text for p in ['$', 'NT', '元', 'TWD'])
                    if has_price and '\n' in text:
                        lines = [l.strip() for l in text.split('\n') if l.strip()]
                        if len(lines) >= 2:
                            title = lines[0]
                            price = next((l for l in lines[1:] if any(p in l for p in ['$', 'NT', '元'])), None)
                            if title and len(title) < 50 and not any(item.name == title for item in items):
                                items.append(MenuItem(name=title, price=price))
                except Exception:
                    continue
        
        restaurant.menu_items = items
        
    except Exception as e:
        # 記錄錯誤但不中斷
        restaurant.menu_items = restaurant.menu_items or []


async def crawl_google_maps(query: str, *, max_shops: int = 10, max_items_per_shop: int = 40, headless: bool = True) -> List[Restaurant]:
    """
    從 Google Maps 搜尋餐廳並嘗試爬取菜單資訊
    
    注意：Google Maps 並非所有餐廳都有結構化的菜單資料。
    如果餐廳沒有提供線上菜單，menu_items 將為空列表。
    建議使用者可以：
    1. 查看餐廳的照片來獲取菜單資訊
    2. 訪問餐廳官網或外送平台獲取菜單
    3. 手動輸入菜單資料
    """
    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
        url = f"https://www.google.com/maps/search/{query}"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)  # 增加等待時間確保內容載入
        restaurants = await _extract_restaurants(page, max_shops)
        
        # 嘗試爬取每間餐廳的菜單（但可能很多餐廳沒有線上菜單）
        for r in restaurants:
            await _extract_menu_from_place(page, r, max_items_per_shop=max_items_per_shop)
        
        await browser.close()
        return restaurants


def to_menu_json(restaurants: List[Restaurant]) -> List[dict]:
    menu = []
    for r in restaurants:
        if not r.menu_items:
            continue
        for item in r.menu_items:
            menu.append({
                "restaurant": r.name,
                "dish": item.name,
                "price": item.price,
                "address": r.address,
                "rating": r.rating,
                "price_level": r.price_level,
                "source_url": r.url,
            })
    return menu


def save_menu_json(menu: List[dict], path: Path) -> None:
    path.write_text(json.dumps(menu, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: List[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="從 Google Maps 搜索頁爬取餐廳與菜單 (實驗性)")
    parser.add_argument("query", help="搜尋關鍵字，如：'沙鹿 餐廳 菜單'")
    parser.add_argument("--max-shops", type=int, default=10, help="最多抓幾間店")
    parser.add_argument("--max-items", type=int, default=40, help="每間店最多抓幾個菜品")
    parser.add_argument("--out", type=Path, default=Path("menu_scraped.json"), help="輸出 JSON 路徑")
    parser.add_argument("--headful", action="store_true", help="顯示瀏覽器方便除錯")
    args = parser.parse_args(argv)

    async def runner():
        restaurants = await crawl_google_maps(
            args.query,
            max_shops=args.max_shops,
            max_items_per_shop=args.max_items,
            headless=not args.headful,
        )
        menu = to_menu_json(restaurants)
        save_menu_json(menu, args.out)
        print(f"完成，匯出 {len(menu)} 筆菜品到 {args.out}")

    asyncio.run(runner())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
