# Google Maps 爬蟲說明

## 功能概述

本專案的 Google Maps 爬蟲可以：
- ✅ 搜尋餐廳並獲取基本資訊（名稱、評分、地址）
- ⚠️ **嘗試**爬取菜單資料（但成功率較低）

## 重要限制

### 為什麼很多餐廳沒有菜單資料？

Google Maps 上的餐廳資訊有以下特性：

1. **大多數餐廳沒有結構化的菜單資料**
   - 許多餐廳只有照片、評論、營業時間等基本資訊
   - 菜單通常以照片形式存在，而非結構化文字

2. **菜單資料格式不統一**
   - 即使有菜單，HTML 結構可能因地區、語言、餐廳類型而異
   - Google 會動態調整頁面結構，導致爬蟲選擇器經常失效

3. **需要額外的互動**
   - 有些餐廳的菜單需要點擊「菜單」標籤才會顯示
   - 內容可能需要滾動才會載入（lazy loading）

## 替代方案

### 方案 1：從其他平台獲取菜單

建議從這些平台爬取或手動匯入菜單：
- **Uber Eats / Foodpanda**：這些外送平台有結構化的菜單資料
- **餐廳官網**：通常有完整的菜單資訊
- **iCHEF / inline**：餐廳 POS 系統的線上菜單

### 方案 2：OCR 識別菜單照片

使用 OCR（光學字元識別）從 Google Maps 的菜單照片中提取資訊：
```python
# 需要安裝：pip install pytesseract pillow
from PIL import Image
import pytesseract

# 從照片中提取文字
image = Image.open('menu_photo.jpg')
text = pytesseract.image_to_string(image, lang='chi_tra')  # 繁體中文
```

### 方案 3：手動建立菜單資料庫

1. 使用爬蟲獲取餐廳清單（名稱、評分、地址）
2. 手動或半自動地從各來源整理菜單
3. 建立標準化的菜單資料格式

## 使用建議

### 當前爬蟲適用場景

✅ **適合：**
- 快速搜尋並列出某區域的餐廳
- 獲取餐廳的基本資訊（名稱、評分、位置）
- 作為後續手動整理菜單的起點

❌ **不適合：**
- 期望完整自動化爬取所有菜單資料
- 用於生產環境的即時菜單更新

### 實際工作流程建議

```
1. 使用爬蟲搜尋 "台中 火鍋"
   ↓
2. 獲得 5 間火鍋店的清單（含評分、地址）
   ↓
3. 手動查看各餐廳的：
   - Google Maps 照片（找菜單照片）
   - 官網或 Facebook 粉絲頁
   - 外送平台頁面
   ↓
4. 將菜單資料整理成標準格式
   ↓
5. 匯入到系統的 menu.json 或資料庫
```

## 技術細節

### 爬蟲實作邏輯

```python
# 1. 搜尋餐廳
await page.goto(f"https://www.google.com/maps/search/{query}")

# 2. 提取餐廳列表（使用多種選擇器應對變化）
cards = await page.query_selector_all('[role="article"], .Nv2PK, ...')

# 3. 對每間餐廳：
for restaurant in restaurants:
    # 3.1 前往餐廳詳細頁面
    await page.goto(restaurant.url)
    
    # 3.2 嘗試點擊「菜單」標籤
    menu_tab = await page.query_selector('button:has-text("菜單")')
    if menu_tab:
        await menu_tab.click()
    
    # 3.3 爬取菜單項目（通常會失敗）
    items = await page.query_selector_all('.menu-item-selector')
```

### 為什麼爬取經常失敗？

1. **CSS 選擇器容易過時**
   - Google 經常更新前端程式碼
   - 不同地區、語言使用不同的 CSS class

2. **反爬蟲機制**
   - 頻繁請求可能被 Google 限制
   - Headless 瀏覽器可能被偵測

3. **動態載入**
   - 內容可能需要 JavaScript 渲染
   - 需要滾動或互動才會載入

## 改進方向

### 短期改進

1. **增加更多選擇器**：持續更新 CSS 選擇器以應對 Google 的變化
2. **加入重試機制**：失敗時自動重試或使用不同策略
3. **截圖存檔**：保存菜單截圖供後續 OCR 處理

### 長期方案

1. **整合外送平台 API**
   - Uber Eats、Foodpanda 有公開或半公開的 API
   - 可以獲得結構化的菜單資料

2. **眾包菜單資料**
   - 讓使用者貢獻和編輯菜單
   - 建立社群維護的餐廳資料庫

3. **AI 輔助整理**
   - 使用 GPT-4 Vision 分析菜單照片
   - 自動將圖片轉換為結構化資料

## 範例輸出

### 成功案例（少數）
```json
{
  "name": "鼎王麻辣鍋",
  "rating": 4.3,
  "menu_items": [
    {"name": "麻辣鍋", "price": "NT$400"},
    {"name": "酸菜白肉鍋", "price": "NT$380"}
  ]
}
```

### 常見情況（多數）
```json
{
  "name": "鼎王麻辣鍋",
  "rating": 4.3,
  "menu_items": []  // 無菜單資料
}
```

## 總結

Google Maps 爬蟲是個**輔助工具**而非完整解決方案：
- ✅ 用它來快速找到餐廳清單
- ❌ 不要期待能自動獲得完整菜單
- 💡 結合其他資料來源才能建立完整的菜單資料庫
