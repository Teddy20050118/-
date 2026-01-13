# Google Maps 菜單爬蟲使用說明

## 安裝依賴

```powershell
pip install playwright
playwright install chromium
```

## 使用方法

### 基本用法

爬取台中沙鹿餐廳的菜單：

```powershell
python crawler.py "沙鹿 餐廳 菜單"
```

輸出會存到 `menu_scraped.json`。

### 進階選項

```powershell
# 抓最多 5 間店，每間店最多 20 道菜
python crawler.py "台中 義大利麵 餐廳" --max-shops 5 --max-items 20

# 指定輸出檔案
python crawler.py "台北 火鍋 餐廳" --out taibei_hotpot.json

# 顯示瀏覽器視窗（方便除錯）
python crawler.py "台中餐廳" --headful
```

## 輸出格式

匯出的 JSON 格式：

```json
[
  {
    "restaurant": "富哥羊肉手作小食",
    "dish": "紅油餃子",
    "price": "$110.00",
    "address": "台中市沙鹿區...",
    "rating": 4.8,
    "price_level": "2",
    "source_url": "https://www.google.com/maps/place/..."
  },
  ...
]
```

## 注意事項

1. **實驗性功能**：Google Maps 網頁結構可能隨時變更，爬蟲可能需要更新 CSS 選擇器。
2. **合理使用**：避免頻繁、大量抓取，以免 IP 被限制。
3. **僅供學習**：請遵守 Google 服務條款，抓取的資料僅供個人學習與測試。
4. **不完整**：並非所有餐廳都有完整菜單，價格可能缺失。

## 整合到現有系統

抓取完成後，可以將 JSON 匯入資料庫：

```powershell
# 假設你有 migrate_data.py 可以接受自訂 JSON
python migrate_data.py --source menu_scraped.json
```

或者在 `menu.json` 中手動合併，讓 AI 助手的 fallback 機制使用。
