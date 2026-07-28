# 點餐助手

FastAPI 點餐助手，支援從 Google 擷取菜單、使用 VLM 辨識手機拍攝的菜單／菜色照片，以及透過 LINE Bot 新增餐廳。

## 啟動

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 編輯 .env 後：
python src/back.py
```

網頁預設位於 `http://127.0.0.1:7890`。照片辨識需要在 `.env` 設定支援 OpenAI-compatible Chat Completions 圖片輸入的 `API_BASE_URL`、`API_KEY`、`VISION_MODEL` 與 `VISION_VERIFY_MODEL`。密集大圖會經過全圖判讀、四張重疊切片 OCR 及最終校對，約呼叫 6 次 Vision API。

## 網頁照片辨識

點選右上角「拍照辨識」，可直接開啟手機後鏡頭或選取 JPEG、PNG、WebP、HEIC 圖片（上限 10 MB）。辨識完成只會建立 15 分鐘的暫存分析，顯示店名候選、品項數、價格覆蓋率及衝突；使用者確認後才會寫入 schema v2 菜單。

API 也可直接呼叫：

```bash
curl -X POST http://127.0.0.1:7890/api/menu/vision \
  -F "restaurant_name=範例餐廳" \
  -F "image=@menu.jpg"
```

分析回傳 `analysisId` 後，再確認存檔：

```bash
curl -X POST http://127.0.0.1:7890/api/menu/vision/ANALYSIS_ID/confirm \
  -H "Content-Type: application/json" \
  -d '{"restaurant_name":"範例餐廳","accept_conflicts":true}'
```

## LINE Bot 設定

1. 在 LINE Developers 建立 Provider、LINE Official Account 與 Messaging API channel。
2. 將 Channel secret 與長效 Channel access token 填入 `.env` 的 `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`。
3. 將此服務部署在公開 HTTPS 網址，並把 Webhook URL 設為 `https://你的網域/line/webhook`。
4. 在 Messaging API 頁面啟用 **Use webhook**，並關閉 Official Account Manager 的自動回覆以免重複答覆。
5. 加 Bot 為好友後直接傳照片。Bot 會先列出編號暫存菜單；統一使用 `改 3 菜名 豬腳飯` 修改菜名，或用 `改 3 價格 90` 修改價格。確認內容後傳 `確認` 正式建立，或傳 `取消` 放棄；每次人工修改會延長 15 分鐘確認期限。舊版自然語言修正句型仍保留相容性。

Webhook 會先使用 `x-line-signature` 與 Channel secret 驗證未修改的原始 request body。圖片工作會先快速回覆已收到，再於背景下載圖片、呼叫 VLM，最後用 push message 回報結果，避免 VLM 處理時間耗盡 reply token。

## 主要環境變數

| 變數 | 用途 |
|---|---|
| `API_BASE_URL` | OpenAI-compatible API 位址，可填到服務根路徑或 `/chat/completions` |
| `API_KEY` | 模型 API 金鑰 |
| `VISION_MODEL` | 高解析切片 OCR 模型，預設 `gemma-4-31b` |
| `VISION_VERIFY_MODEL` | 全圖判讀與 OCR 校對模型，預設 `mistral-small-4` |
| `RECOMMEND_MODEL` | 輸出候選 item ID 的推薦規劃模型 |
| `RESPONSE_MODEL` | 將已驗證推薦轉成自然文字的模型 |
| `FAST_FALLBACK_MODEL` | 主要回答模型失敗時的文字備援；不參與選品 |
| `LINE_CHANNEL_SECRET` | LINE webhook 簽章驗證 |
| `LINE_CHANNEL_ACCESS_TOKEN` | 下載 LINE 圖片與傳送訊息 |

`GET /health` 會顯示 `visionConfigured` 與 `lineConfigured`，方便部署後檢查是否完成設定，但不會洩露金鑰內容。
