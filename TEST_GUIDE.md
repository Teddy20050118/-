# 快速測試指南

## 🧪 測試新功能：混合式爬蟲

### 步驟 1：本地測試

1. **確認 Playwright 已安裝**
   ```powershell
   pip install playwright
   playwright install chromium
   ```

2. **測試 Foodpanda 爬蟲（獨立）**
   ```powershell
   cd "c:\Users\User\Desktop\點餐"
   python foodpanda_crawler.py "史堤克牛排"
   ```
   
   預期結果：
   ```
   在 Foodpanda 搜尋：史堤克牛排
   找到餐廳：史堤克牛排大甲店 (https://www.foodpanda.com.tw/restaurant/...)
   正在爬取菜單...
   完成！共 32 道菜
   
   餐廳：史堤克牛排大甲店
   評分：4.7
   
   菜單（前 10 道）：
   1. 經典牛排 - NT$280
   2. 雞腿排 - NT$200
   ...
   ```

3. **啟動本地伺服器**
   ```powershell
   cd "c:\Users\User\Desktop\點餐\src"
   python -m uvicorn back:app --reload --host 0.0.0.0 --port 8000
   ```

4. **開啟前端測試**
   ```
   瀏覽器開啟：http://localhost:8000
   ```

### 步驟 2：前端完整流程測試

1. **點擊爬蟲按鈕**
   - 找到右上角的 🕷️ 按鈕
   - 點擊

2. **輸入搜尋關鍵字**
   ```
   大甲 牛排
   ```
   
3. **等待餐廳清單**（約 5-10 秒）
   ```
   ✅ 成功爬取 5 間餐廳

   找到的餐廳：
   1. 史堤克牛排大甲店 ⭐4.7
   2. 愛將平價牛排－大甲店 ⭐3.9
   3. 大甲來客牛排 ⭐4.2
   4. 濃濃西餐 ⭐3.8
   5. 來怡客牛排 ⭐3.8

   💡 想查看某間餐廳的完整菜單？
   請輸入餐廳編號（1-5），我會從 Foodpanda 爬取完整菜單！
   ```

4. **選擇餐廳**
   - 在輸入框輸入：`1`
   - 按 Enter

5. **等待菜單爬取**（約 10-20 秒）
   ```
   ✅ 成功爬取 史堤克牛排大甲店 的菜單，共 32 道菜

   餐廳：史堤克牛排大甲店
   評分：⭐4.7
   外送時間：25-35 分鐘

   菜單（前 20 道）：
   1. 經典牛排 - NT$280
   2. 雞腿排 - NT$200
   3. 豬排 - NT$180
   ...

   💡 這些菜單資料已暫存，你可以開始點餐了！
   ```

6. **開始點餐**
   ```
   我要經典牛排 1 份
   ```

### 步驟 3：Render 部署測試

1. **等待自動部署**
   - GitHub push 後約 3-5 分鐘
   - 檢查 Render 儀表板

2. **檢查部署日誌**
   ```
   ✅ playwright install chromium --with-deps
   ✅ Build succeeded
   ✅ Service is live
   ```

3. **測試線上版本**
   ```
   https://ordering-assistant.onrender.com/
   ```

4. **如果失敗**
   - 檢查 Render 日誌
   - 可能原因：
     - Playwright 未正確安裝
     - 記憶體不足（免費版限制）
     - Chromium 依賴缺失

### 步驟 4：API 測試（進階）

```powershell
# 測試 Google Maps 搜尋
Invoke-RestMethod -Uri "http://localhost:8000/api/crawl" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"query":"大甲 牛排","maxShops":5}' | ConvertTo-Json

# 測試 Foodpanda 爬取
Invoke-RestMethod -Uri "http://localhost:8000/api/crawl-foodpanda" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"restaurantName":"史堤克牛排","city":"taichung"}' | ConvertTo-Json
```

## 🐛 常見問題排除

### 問題 1：找不到餐廳

**症狀**：
```
❌ 在 Foodpanda 找不到餐廳「xxx」
```

**解決方法**：
1. 檢查餐廳名稱是否正確
2. 嘗試簡化名稱（例如：「史堤克牛排大甲店」→「史堤克牛排」）
3. 確認餐廳在 Foodpanda 上有營業

### 問題 2：爬取超時

**症狀**：
```
❌ 連線錯誤：TimeoutError
```

**解決方法**：
1. 檢查網路連線
2. 增加 timeout 設定
3. 確認 Playwright 正常運作

### 問題 3：Render 部署失敗

**症狀**：
```
Service failed to start
```

**解決方法**：
1. 檢查 render.yaml 中的 buildCommand
2. 確認有安裝 `--with-deps`
3. 考慮升級到付費方案（免費版記憶體可能不足）

### 問題 4：CSS 選擇器失效

**症狀**：
```
✅ 成功爬取，但菜單為 0 道菜
```

**解決方法**：
1. Foodpanda 可能更新了網站結構
2. 需要更新 `foodpanda_crawler.py` 中的選擇器
3. 使用瀏覽器開發者工具檢查實際 HTML 結構

## 📊 預期行為

### 成功案例
- Google Maps 搜尋：5-10 秒，找到 5 間餐廳
- Foodpanda 爬取：10-30 秒，獲得 20-50 道菜
- 總時間：15-40 秒

### 失敗但正常的情況
- Google Maps 找到餐廳，但 Foodpanda 找不到（餐廳未上架外送）
- Foodpanda 有餐廳但菜單很少（可能剛上架或休息中）

### 需要修正的情況
- 一直超時（網路或程式碼問題）
- 所有餐廳都是 0 道菜（CSS 選擇器失效）
- 伺服器崩潰（記憶體不足）

## ✅ 測試檢查清單

- [ ] Playwright 已安裝
- [ ] 本地伺服器可啟動
- [ ] 前端爬蟲按鈕可見
- [ ] Google Maps 搜尋成功
- [ ] 可以輸入數字選擇餐廳
- [ ] Foodpanda 爬取成功
- [ ] 菜單資料正確顯示
- [ ] 可以開始點餐對話
- [ ] Render 部署成功
- [ ] 線上版本可用

## 🎯 效能指標

### 理想情況
- Google Maps：5-10 秒
- Foodpanda：10-20 秒
- 總時間：< 30 秒

### 可接受
- Google Maps：10-15 秒
- Foodpanda：20-40 秒
- 總時間：< 60 秒

### 需要優化
- 任何步驟 > 60 秒
- 失敗率 > 30%

## 📝 測試記錄範本

```
測試時間：2026-01-14 XX:XX
測試環境：□ 本地  □ Render
測試關鍵字：「大甲 牛排」

結果：
1. Google Maps 搜尋
   - 耗時：_____ 秒
   - 找到餐廳：_____ 間
   - 狀態：□ 成功  □ 失敗

2. 使用者選擇
   - 選擇編號：_____
   - 狀態：□ 正確觸發  □ 未觸發

3. Foodpanda 爬取
   - 耗時：_____ 秒
   - 菜品數量：_____ 道
   - 狀態：□ 成功  □ 失敗

4. 點餐對話
   - 狀態：□ 可用  □ 不可用

總評：□ 通過  □ 失敗
問題描述：_________________
```

---

**下一步**：完成測試後，可以考慮加入快取機制、多城市支援、或整合其他外送平台！
