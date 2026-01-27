"""
使用現有的 Chrome 瀏覽器爬取 Google 菜單
需要先手動啟動 Chrome 並開啟遠端除錯模式

使用方法：
1. 先關閉所有 Chrome 視窗
2. 執行此腳本（會自動啟動 Chrome）
3. 手動搜尋並打開菜單頁面
4. 按 Enter 開始爬取
"""
import asyncio
import json
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from playwright.async_api import async_playwright

@dataclass
class MenuItem:
    name: str
    price: str = None

@dataclass
class Restaurant:
    name: str
    menu_items: list = None

async def crawl_from_current_page():
    """從當前打開的頁面爬取菜單"""
    
    async with async_playwright() as p:
        # 連接到已經運行的 Chrome（需要啟用遠端除錯）
        # Chrome 會在端口 9222 上監聽
        try:
            browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        except Exception as e:
            print(f" 無法連接到 Chrome，請確保 Chrome 已啟動並開啟遠端除錯")
            print(f"\n請執行以下命令啟動 Chrome：")
            print('"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222')
            return None
        
        contexts = browser.contexts
        if not contexts:
            print(f" 沒有找到 Chrome 視窗")
            return None
        
        context = contexts[0]
        pages = context.pages
        if not pages:
            print(f" 沒有找到打開的分頁")
            return None
        
        # 顯示所有分頁，讓使用者知道正在使用哪個
        print(f"\n找到 {len(pages)} 個分頁：")
        for i, p in enumerate(pages, 1):
            try:
                title = await p.title()
                url = p.url
                print(f" {i}. {title[:50]} - {url[:60]}")
            except:
                pass
        
        # 尋找包含 Google 搜尋或菜單的分頁
        page = None
        for p in pages:
            try:
                url = p.url
                if 'google.com/search' in url or '菜單' in await p.title():
                    page = p
                    print(f"\n使用分頁: {await p.title()}")
                    break
            except:
                continue
        
        if not page:
            print(f"\n 未找到 Google 搜尋分頁，使用第一個分頁")
            page = pages[0]
        
        print("\n" + "="*60)
        print(f" 已連接到 Chrome")
        print("="*60)
        
        # 取得當前 URL
        url = page.url
        print(f" 當前頁面: {url}")
        
        # 提取餐廳名稱
        restaurant_name = "未知餐廳"
        try:
            title = await page.title()
            restaurant_name = title.split('-')[0].strip() if '-' in title else title.strip()
            print(f" 餐廳名稱: {restaurant_name}")
        except:
            pass
        
        print("\n" + "="*60)
        print("請確保已經：")
        print(f"1. 搜尋了餐廳（例如：麥當勞大甲經國 菜單）")
        print(f"2. 點擊了「菜單」標籤")
        print(f"3. 菜單已經顯示在頁面上")
        print("="*60)
        input("\n按 Enter 開始爬取菜單...")
        
        # 爬取菜單
        print(f"\n📜 開始提取菜單項目...")
        
        menu_items = []
        seen_names = set()
        
        # 檢查頁面上的元素
        bwzfsc_count = await page.locator('.bWZFsc').count()
        ocfjnf_count = await page.locator('.OCfJnf').count()
        print(f" 找到 .bWZFsc 數量: {bwzfsc_count}")
        print(f" 找到 .OCfJnf 數量: {ocfjnf_count}")
        
        if bwzfsc_count == 0:
            print(f"\n 找不到菜單元素！")
            print(f" 請確認：")
            print(f" 1. 頁面上有顯示菜單")
            print(f" 2. 已點擊「菜單」標籤")
            print(f" 3. 使用的是 Google 搜尋結果頁面")
            
            # 保存除錯 HTML
            content = await page.content()
            Path('debug_existing_chrome.html').write_text(content, encoding='utf-8')
            print(f"\n💾 已保存當前頁面: debug_existing_chrome.html")
            
            await browser.close()
            return None
        
        # 抓取菜名和價格
        print(f"\n 開始抓取菜單...")
        bwzfsc_items = await page.locator('.bWZFsc').all()
        
        for idx, item in enumerate(bwzfsc_items, 1):
            try:
                name = await item.inner_text()
                name = name.strip()
                
                if not name or len(name) < 2 or name in seen_names:
                    continue
                
                # 找價格：父元素的下一個兄弟元素
                price = "價格未提供"
                try:
                    # 向上找到父元素 (.NtG2de)
                    parent = item.locator('xpath=..')
                    # 找父元素的下一個兄弟
                    next_sibling = parent.locator('xpath=following-sibling::*[1]')
                    
                    if await next_sibling.count() > 0:
                        class_name = await next_sibling.get_attribute('class')
                        if class_name and 'OCfJnf' in class_name:
                            # 從 aria-label 提取（優先）
                            aria_label = await next_sibling.get_attribute('aria-label')
                            if aria_label:
                                price = aria_label.strip().rstrip('.')
                            else:
                                # 備用：從 innerText 提取
                                price_text = await next_sibling.inner_text()
                                if price_text:
                                    price = price_text.strip()
                except:
                    # 備用：索引配對
                    try:
                        all_prices = await page.locator('.OCfJnf').all()
                        if idx <= len(all_prices):
                            price_elem = all_prices[idx - 1]
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
                print(f" {len(menu_items):2d}. {name[:50]:50s} - {price}")
                
            except Exception as e:
                continue
        
        print(f"\n 共找到 {len(menu_items)} 道菜單")
        
        # 建立餐廳物件
        restaurant = Restaurant(
            name=restaurant_name,
            menu_items=menu_items
        )
        
        await browser.close()
        return restaurant

async def main():
    print("\n" + "="*60)
    print(f" Google 菜單爬蟲 - 使用現有 Chrome")
    print("="*60)
    
    # 提示用戶啟動 Chrome
    print(f"\n請先確保 Chrome 已啟動並開啟遠端除錯：")
    print('1. 關閉所有 Chrome 視窗')
    print('2. 執行此命令：')
    print('   "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222')
    print('3. 在 Chrome 中搜尋餐廳菜單')
    print('4. 點擊「菜單」標籤')
    print("")
    input("準備好後按 Enter 繼續...")
    
    restaurant = await crawl_from_current_page()
    
    if restaurant and restaurant.menu_items:
        print(f"\n{'='*60}")
        print(f" 爬取結果")
        print(f"{'='*60}")
        print(f" 餐廳: {restaurant.name}")
        print(f" 菜單: {len(restaurant.menu_items)} 道菜")
        
        # 儲存 JSON
        output_file = f'menu_{restaurant.name.replace(" ", "_")}.json'
        Path(output_file).write_text(
            json.dumps(asdict(restaurant), ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
        print(f"\n 結果已儲存: {output_file}")
        print(f"\n 成功爬取 {restaurant.name} 的 {len(restaurant.menu_items)} 道菜單！")
    else:
        print(f"\n 爬取失敗")

if __name__ == '__main__':
    asyncio.run(main())
