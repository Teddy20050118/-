"""
Foodpanda 爬蟲 - 完全自動版（使用 2Captcha）

⚠️ 需要 2Captcha API Key
註冊：https://2captcha.com/
費用：約 $1 per 1000 captchas

環境變數：
export CAPTCHA_API_KEY="your_api_key_here"
"""
import asyncio
import os
import json
from typing import Optional
from foodpanda_stealth import (
    crawl_foodpanda_stealth, 
    FoodpandaRestaurant,
    create_stealth_context,
    apply_stealth_scripts,
    human_like_delay
)
from playwright.async_api import async_playwright, Page

# 如果要使用 2Captcha，需要安裝：pip install 2captcha-python
try:
    from twocaptcha import TwoCaptcha
    CAPTCHA_AVAILABLE = True
except ImportError:
    print("⚠️  2captcha-python 未安裝")
    print("安裝：pip install 2captcha-python")
    CAPTCHA_AVAILABLE = False


async def solve_recaptcha(page: Page, site_key: str, api_key: str) -> Optional[str]:
    """
    使用 2Captcha 求解 reCAPTCHA
    
    Args:
        page: Playwright 頁面
        site_key: reCAPTCHA site key
        api_key: 2Captcha API key
    
    Returns:
        驗證 token 或 None
    """
    if not CAPTCHA_AVAILABLE:
        return None
    
    try:
        print("🤖 使用 2Captcha 求解...")
        
        solver = TwoCaptcha(api_key)
        current_url = page.url
        
        # 提交驗證請求
        result = solver.recaptcha(
            sitekey=site_key,
            url=current_url
        )
        
        token = result['code']
        print(f"✅ 獲得驗證 token: {token[:50]}...")
        
        # 注入 token 到頁面
        await page.evaluate(f"""
            document.getElementById('g-recaptcha-response').innerHTML = '{token}';
        """)
        
        # 提交表單
        await page.click('button[type="submit"]')
        await human_like_delay(2000, 4000)
        
        return token
        
    except Exception as e:
        print(f"❌ CAPTCHA 求解失敗：{e}")
        return None


async def crawl_with_auto_captcha(query: str, api_key: str = None) -> list:
    """
    完全自動化爬蟲（處理 CAPTCHA）
    
    Args:
        query: 搜尋關鍵字
        api_key: 2Captcha API key（可選，從環境變數讀取）
    """
    api_key = api_key or os.getenv('CAPTCHA_API_KEY')
    
    if not api_key:
        print("⚠️  未提供 CAPTCHA_API_KEY")
        print("將嘗試不解 CAPTCHA 繼續...")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled']
        )
        
        context = await create_stealth_context(browser)
        page = await context.new_page()
        await apply_stealth_scripts(page)
        
        # 訪問首頁
        await page.goto("https://www.foodpanda.com.tw")
        await human_like_delay(2000, 3000)
        
        # 檢查 CAPTCHA
        captcha = await page.query_selector('.g-recaptcha')
        if captcha and api_key:
            site_key = await captcha.get_attribute('data-sitekey')
            if site_key:
                await solve_recaptcha(page, site_key, api_key)
        
        # 繼續正常流程...
        # （使用 foodpanda_stealth.py 的邏輯）
        
        await browser.close()


# 使用範例
if __name__ == "__main__":
    # 方法 1：從環境變數讀取
    # export CAPTCHA_API_KEY="your_key"
    # python foodpanda_auto.py
    
    # 方法 2：直接傳入
    API_KEY = "YOUR_2CAPTCHA_API_KEY"  # 替換成你的 key
    
    asyncio.run(crawl_with_auto_captcha("牛排", api_key=API_KEY))
