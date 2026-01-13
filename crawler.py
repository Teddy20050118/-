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
    cards = await page.query_selector_all('[role="article"], .Nv2PK')
    results: List[Restaurant] = []
    for card in cards:
        if len(results) >= max_results:
            break
        name_el = await card.query_selector('a, .qBF1Pd, .hfpxzc')
        name = (await name_el.inner_text() if name_el else '').strip()
        rating_el = await card.query_selector('.MW4etd, .KFi5wf')
        rating_text = (await rating_el.inner_text() if rating_el else '').strip()
        try:
            rating = float(rating_text.split('\n')[0].replace(',', '.')) if rating_text else None
        except ValueError:
            rating = None
        price_el = await card.query_selector('.UzM3td, .rllt__details span')
        price_level = _normalize_price_level(await price_el.inner_text() if price_el else '')
        address_el = await card.query_selector('.rllt__details div, .Io6YTe')
        address = (await address_el.inner_text() if address_el else '').strip()
        url_el = await card.query_selector('a[href]')
        url = await url_el.get_attribute('href') if url_el else None
        results.append(Restaurant(name=name or "(未命名)", rating=rating, price_level=price_level, address=address, url=url, menu_items=[]))
    return results


async def _extract_menu_from_place(page, restaurant: Restaurant, max_items_per_shop: int = 40) -> None:
    if not restaurant.url:
        return
    try:
        await page.goto(restaurant.url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1500)
        # Menu sections in Google Maps often use class .DqvUQe for dish cards
        dish_cards = await page.query_selector_all('.DqvUQe, [data-item-id^="ChI"], [role="listitem"]')
        items: List[MenuItem] = []
        for card in dish_cards:
            if len(items) >= max_items_per_shop:
                break
            title_el = await card.query_selector('.qFFZje, .NrDZNb, .qBF1Pd, [role="heading"]')
            price_el = await card.query_selector('.rbj0Ud, .F7nice')
            title = (await title_el.inner_text() if title_el else '').strip()
            if not title:
                continue
            price = (await price_el.inner_text() if price_el else '').strip() or None
            items.append(MenuItem(name=title, price=price))
        restaurant.menu_items = items
    except Exception:
        # 忽略單店解析失敗，以免整批中斷
        restaurant.menu_items = restaurant.menu_items or []


async def crawl_google_maps(query: str, *, max_shops: int = 10, max_items_per_shop: int = 40, headless: bool = True) -> List[Restaurant]:
    async with async_playwright() as p:
        browser: Browser = await p.chromium.launch(headless=headless)
        page = await browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")
        url = f"https://www.google.com/maps/search/{query}"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)
        restaurants = await _extract_restaurants(page, max_shops)
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
