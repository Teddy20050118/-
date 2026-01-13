"""除錯版爬蟲 - 顯示網頁結構"""
import asyncio
from playwright.async_api import async_playwright

async def debug_google_maps():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # 顯示瀏覽器
        page = await browser.new_page()
        
        # 搜尋餐廳
        query = "台中 火鍋"
        url = f"https://www.google.com/maps/search/{query}"
        print(f"前往: {url}")
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        
        # 找第一間餐廳
        print("\n尋找餐廳連結...")
        links = await page.query_selector_all('a[href*="/maps/place/"]')
        if links:
            first_link = links[0]
            href = await first_link.get_attribute('href')
            name = (await first_link.inner_text()).strip()
            print(f"找到: {name}")
            print(f"URL: {href}")
            
            # 點擊進入餐廳頁面
            print("\n點擊進入餐廳...")
            await first_link.click()
            await page.wait_for_timeout(4000)
            
            # 檢查頁面上的所有按鈕
            print("\n頁面上的按鈕:")
            buttons = await page.query_selector_all('button')
            for i, btn in enumerate(buttons[:20], 1):  # 只看前 20 個
                text = (await btn.inner_text()).strip()
                if text:
                    print(f"  {i}. {text[:50]}")
            
            # 嘗試找到菜單標籤
            print("\n尋找菜單標籤...")
            menu_found = False
            for btn in buttons:
                text = (await btn.inner_text()).strip().lower()
                if '菜單' in text or 'menu' in text:
                    print(f"找到菜單按鈕: {text}")
                    await btn.click()
                    await page.wait_for_timeout(3000)
                    menu_found = True
                    break
            
            if not menu_found:
                print("未找到菜單按鈕")
            
            # 保存頁面 HTML 供檢查
            html = await page.content()
            with open('debug_page.html', 'w', encoding='utf-8') as f:
                f.write(html)
            print("\n頁面 HTML 已保存到 debug_page.html")
            
            # 尋找可能的菜單項目
            print("\n尋找可能的菜單項目 (包含價格的文字)...")
            all_text_elements = await page.query_selector_all('div, span')
            menu_items_found = 0
            
            for elem in all_text_elements[:500]:  # 檢查前 500 個元素
                try:
                    text = await elem.inner_text()
                    if text and ('NT' in text or '$' in text or '元' in text):
                        if len(text) < 100 and '\n' not in text:  # 簡短的文字
                            print(f"  - {text}")
                            menu_items_found += 1
                            if menu_items_found >= 10:  # 只顯示前 10 個
                                break
                except Exception:
                    continue
            
            if menu_items_found == 0:
                print("未找到包含價格的文字")
            
            print("\n按 Enter 關閉瀏覽器...")
            input()
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(debug_google_maps())
