"""
快速手動爬蟲 - 一鍵式操作
自動啟動 Chrome → 搜尋餐廳 → 等待用戶點擊菜單 → 爬取

使用方法：
    python quick_manual_crawl.py "餐廳名稱"
    
範例：
    python quick_manual_crawl.py "肯德基大甲"
    python quick_manual_crawl.py "麥當勞大甲經國"
"""
import asyncio
import json
import sys
import subprocess
import time
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

async def quick_crawl(restaurant_name: str):
    """快速手動爬蟲"""
    
    print("\n" + "="*60)
    print(f" 快速手動爬蟲")
    print("="*60)
    print(f" 目標餐廳: {restaurant_name}")
    print("="*60)
    
    # 1. 啟動 Chrome（遠端除錯模式）
    print(f"\n【步驟 1/4】啟動 Chrome...")
    
    # 先關閉現有的 Chrome
    try:
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], 
                      capture_output=True, timeout=5)
        time.sleep(2)
    except:
        pass
    
    # 啟動新的 Chrome
    chrome_cmd = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "--remote-debugging-port=9222",
        "--user-data-dir=C:\\temp\\chrome_debug",
        f"https://www.google.com/search?q={restaurant_name} 菜單"
    ]
    
    chrome_process = subprocess.Popen(chrome_cmd)
    time.sleep(5)  # 等待 Chrome 啟動
    
    print(f" Chrome 已啟動")
    print(f" 已自動搜尋：{restaurant_name} 菜單")
    
    # 2. 連接到 Chrome
    print(f"\n【步驟 2/4】連接到 Chrome...")
    
    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp('http://localhost:9222')
        except Exception as e:
            print(f" 無法連接到 Chrome: {e}")
            return None
        
        contexts = browser.contexts
        if not contexts or not contexts[0].pages:
            print(f" 沒有找到 Chrome 頁面")
            return None
        
        page = None
        for context in contexts:
            for p in context.pages:
                try:
                    if 'google.com/search' in p.url:
                        page = p
                        break
                except:
                    continue
            if page:
                break
        
        if not page:
            page = contexts[0].pages[0]
        
        print(f" 已連接到 Chrome")
        print(f"📄 當前頁面: {page.url[:80]}")
        
        # 3. 等待用戶操作
        print("\n" + "="*60)
        print(f"【步驟 3/4】👆 請在 Chrome 中操作：")
        print("="*60)
        print(f"1. 找到餐廳的資訊卡")
        print(f"2. 點擊「菜單」標籤")
        print(f"3. 等待菜單完整顯示")
        print(f"4. 確認可以看到菜名和價格")
        print("="*60)
        input("\n 完成後，按 Enter 開始爬取...")
        
        # 4. 爬取菜單
        print(f"\n【步驟 4/4】📥 開始爬取菜單...")
        
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
            print(f" 1. 已點擊「菜單」標籤")
            print(f" 2. 菜單已完整顯示在頁面上")
            print(f" 3. 使用的是 Google 搜尋結果頁面")
            
            # 保存除錯 HTML
            content = await page.content()
            debug_file = f'debug_quick_{restaurant_name.replace(" ", "_")}.html'
            Path(debug_file).write_text(content, encoding='utf-8')
            print(f"\n💾 已保存當前頁面: {debug_file}")
            
            await browser.close()
            return None
        
        # 抓取菜名和價格
        print(f"\n📥 開始抓取...")
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
                    parent = item.locator('xpath=..')
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
    if len(sys.argv) < 2:
        print(f"用法: python quick_manual_crawl.py <餐廳名稱>")
        print(f"範例: python quick_manual_crawl.py \"肯德基大甲\"")
        print(f"範例: python quick_manual_crawl.py \"麥當勞大甲經國\"")
        sys.exit(1)
    
    restaurant_name = sys.argv[1]
    
    restaurant = await quick_crawl(restaurant_name)
    
    if restaurant and restaurant.menu_items:
        print(f"\n{'='*60}")
        print(f" 爬取結果")
        print(f"{'='*60}")
        print(f"🏪 餐廳: {restaurant.name}")
        print(f"🍽️ 菜單: {len(restaurant.menu_items)} 道菜")
        
        # 儲存 JSON
        output_file = f'menu_{restaurant.name.replace(" ", "_")}.json'
        Path(output_file).write_text(
            json.dumps(asdict(restaurant), ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
        print(f"\n💾 結果已儲存: {output_file}")
        print(f"\n 成功爬取 {restaurant.name} 的 {len(restaurant.menu_items)} 道菜單！")
        
        # 提示如何在系統中使用
        print("\n" + "="*60)
        print(f" 如何在點餐系統中使用這個菜單：")
        print("="*60)
        print(f"1. 重啟後端服務（會自動載入新菜單）")
        print(f"2. 或在前端點選餐廳切換")
        print("="*60)
    else:
        print(f"\n 爬取失敗")
        sys.exit(1)

if __name__ == '__main__':
    asyncio.run(main())
