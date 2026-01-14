# 🕷️ Foodpanda 反檢測爬蟲指南

## 📁 檔案說明

### 1. `foodpanda_stealth.py` - 反檢測版爬蟲 ⭐推薦

**特色：**
- ✅ 隱藏 Playwright 自動化痕跡
- ✅ 模擬真實使用者行為
- ✅ 隨機延遲和滾動
- ✅ 可手動處理 CAPTCHA

**使用方法：**
```bash
# 基本用法（顯示瀏覽器，可手動處理 CAPTCHA）
python foodpanda_stealth.py "牛排"

# 無頭模式（完全自動，但可能被 CAPTCHA 阻擋）
python foodpanda_stealth.py "牛排" --headless
```

### 2. `foodpanda_auto.py` - 全自動版（需要付費服務）

**特色：**
- ✅ 自動求解 CAPTCHA
- ✅ 完全無需人工介入
- ⚠️ 需要 2Captcha API key（付費）

**費用：**
- 約 $1 USD / 1000 次驗證
- 註冊：https://2captcha.com/

## 🚀 快速開始

### 安裝依賴

```bash
# 基本依賴
pip install playwright fake-useragent
playwright install chromium

# 如果要用自動求解（可選）
pip install 2captcha-python
```

### 測試爬蟲

```bash
# 方法 1：顯示瀏覽器（推薦，第一次使用）
python foodpanda_stealth.py "火鍋"

# 如果出現 CAPTCHA：
# 1. 瀏覽器會暫停 60 秒
# 2. 手動點擊完成驗證
# 3. 爬蟲會自動繼續

# 方法 2：無頭模式（已確認可用後）
python foodpanda_stealth.py "火鍋" --headless
```

## 🔧 反檢測技術說明

### 策略 1：隱藏自動化特徵

```javascript
// 注入到頁面的腳本
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined  // 隱藏 webdriver
});

window.chrome = { runtime: {} };  // 模擬真實 Chrome
```

### 策略 2：使用真實瀏覽器配置

```python
args=[
    '--disable-blink-features=AutomationControlled',  # 關鍵！
    '--disable-dev-shm-usage',
    '--lang=zh-TW',
]
```

### 策略 3：模擬真實行為

- ✅ 隨機延遲（500-2000ms）
- ✅ 滾動載入內容
- ✅ 先訪問首頁再搜尋
- ✅ 模擬滑鼠移動

### 策略 4：真實的 User-Agent

```python
user_agents = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...",
]
```

## 🎯 處理 CAPTCHA 的方法

### 方法 1：手動處理（免費）

```bash
# 不使用 --headless，瀏覽器會顯示
python foodpanda_stealth.py "牛排"

# 如果出現 CAPTCHA：
# 1. 爬蟲會暫停 60 秒
# 2. 你手動點擊完成驗證
# 3. 爬蟲繼續執行
```

**優勢：**
- ✅ 完全免費
- ✅ 100% 成功率

**劣勢：**
- ❌ 需要人工介入
- ❌ 無法完全自動化

### 方法 2：使用 2Captcha（付費）

```bash
# 設定 API key
export CAPTCHA_API_KEY="your_key_here"

# 執行自動爬蟲
python foodpanda_auto.py "牛排"
```

**優勢：**
- ✅ 完全自動化
- ✅ 無需人工介入

**劣勢：**
- ❌ 需要付費（約 $0.001/次）
- ❌ 求解時間：10-30 秒

### 方法 3：使用代理 IP 輪換

如果頻繁被封鎖，可以使用代理服務：

```python
# 在 create_stealth_context 中加入
context = await browser.new_context(
    proxy={
        "server": "http://proxy.example.com:8080",
        "username": "user",
        "password": "pass"
    }
)
```

推薦服務：
- Bright Data（前 Luminati）
- Smartproxy
- Oxylabs

## 📊 成功率估計

| 方法 | 成功率 | 速度 | 費用 |
|------|--------|------|------|
| Stealth + 手動 CAPTCHA | 95% | 慢 | 免費 |
| Stealth + 2Captcha | 85% | 中 | $0.001/次 |
| Stealth + 代理輪換 | 90% | 快 | $10-50/月 |

## 🐛 故障排除

### 問題 1：仍然被 CAPTCHA 阻擋

**解決方案：**
```bash
# 1. 使用有頭模式手動處理
python foodpanda_stealth.py "牛排"

# 2. 增加延遲時間（編輯 human_like_delay）
await human_like_delay(3000, 6000)  # 增加到 3-6 秒

# 3. 使用代理 IP
```

### 問題 2：找不到餐廳元素

**解決方案：**
```python
# 檢查保存的 HTML
with open('debug_foodpanda_stealth.html', 'r', encoding='utf-8') as f:
    html = f.read()
    # 尋找餐廳卡片的實際 class 名稱

# 更新選擇器
card_selectors = [
    'YOUR_NEW_SELECTOR',  # 從 HTML 找到的
    'a[href*="/restaurant/"]',
]
```

### 問題 3：菜單爬取失敗

**解決方案：**
```python
# 增加滾動次數
for i in range(10):  # 原本是 5
    await page.evaluate("window.scrollBy(0, 800)")
    await human_like_delay(1000, 2000)

# 或手動檢查菜單頁面結構
```

## 🔐 安全性與合法性

### 注意事項

1. **尊重 robots.txt**
   ```
   https://www.foodpanda.com.tw/robots.txt
   ```

2. **控制頻率**
   ```python
   # 不要過於頻繁
   await asyncio.sleep(5)  # 每次請求間隔 5 秒
   ```

3. **遵守服務條款**
   - 僅用於個人研究
   - 不要商業使用
   - 不要大量爬取

4. **使用緩存**
   ```python
   # 避免重複爬取
   if restaurant_name in cache:
       return cache[restaurant_name]
   ```

## 💡 如果還是不行...

### 替代方案 A：菜單編輯器

我可以幫你建立一個前端介面，手動新增菜單：
- 不需要爬蟲
- 100% 成功率
- 合法且穩定

### 替代方案 B：OCR + AI

使用 GPT-4 Vision 從菜單照片提取：
```python
# 上傳菜單照片
response = openai.ChatCompletion.create(
    model="gpt-4-vision-preview",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "提取菜單"},
            {"type": "image_url", "image_url": image_url}
        ]
    }]
)
```

### 替代方案 C：眾包資料

讓使用者貢獻菜單資料，建立社群維護的資料庫。

## 📚 進階技巧

### 技巧 1：使用 undetected-chromedriver

如果 Playwright 還是被檢測，可以試試：
```bash
pip install undetected-chromedriver selenium
```

### 技巧 2：瀏覽器指紋偽造

使用更進階的反檢測：
```bash
pip install playwright-stealth
```

### 技巧 3：機器學習識別 CAPTCHA

訓練模型自動識別（高難度）：
- 收集 CAPTCHA 圖片
- 訓練 CNN 模型
- 整合到爬蟲

## 🎓 學習資源

### 需要自己解決的部分

如果爬蟲還是不行，你可能需要：

1. **更新選擇器**
   - 打開 `debug_foodpanda_stealth.html`
   - 找到實際的 HTML 結構
   - 更新 `card_selectors` 和 `item_selectors`

2. **調整延遲時間**
   - 修改 `human_like_delay(min, max)`
   - 增加滾動次數

3. **使用不同的反檢測工具**
   - 研究 `puppeteer-extra-plugin-stealth`
   - 使用 `selenium-stealth`

4. **購買 CAPTCHA 求解服務**
   - 2Captcha
   - Anti-Captcha
   - CapMonster

### 有用的連結

- Playwright 官方文檔：https://playwright.dev/python/
- 2Captcha API：https://2captcha.com/2captcha-api
- 反爬蟲研究：https://antoinevastel.com/
- Stealth 插件：https://github.com/berstend/puppeteer-extra/tree/master/packages/puppeteer-extra-plugin-stealth

## ✅ 總結

**現在你有：**

1. ✅ 反檢測爬蟲（`foodpanda_stealth.py`）
2. ✅ 自動 CAPTCHA 求解（`foodpanda_auto.py`）
3. ✅ 詳細的故障排除指南
4. ✅ 替代方案說明

**測試步驟：**

```bash
# Step 1: 測試反檢測爬蟲
python foodpanda_stealth.py "牛排"

# Step 2: 如果被 CAPTCHA 阻擋，手動完成
# （瀏覽器會顯示，等 60 秒手動點擊）

# Step 3: 如果成功，改用無頭模式
python foodpanda_stealth.py "牛排" --headless

# Step 4: 如果還是不行，考慮使用 2Captcha
export CAPTCHA_API_KEY="your_key"
python foodpanda_auto.py "牛排"
```

祝你成功！🎉
