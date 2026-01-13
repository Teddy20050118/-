# 點餐助理（手機可用 · LibreChat 風格 · 多餐廳爬蟲）

這個專案是 FastAPI + 純前端（`web/`）的點餐聊天推薦介面，具備：

- 🤖 智能菜單推薦（預算、人數、不辣、飲料等偏好感知）
- 💬 LibreChat 風格 UI，支援多對話管理、搜尋、匯出/匯入
- 🕷️ Google Maps 菜單爬蟲（`crawler.py`），可批量抓取多間餐廳菜單
- 🌐 Render 雲端部署，手機隨時隨地可用
- 🔄 資料庫連線失敗時自動 fallback 到本地 `menu.json`

---

## 快速開始（雲端已部署）

**直接手機打開：https://ordering-assistant.onrender.com/**

輸入「預算 3000 元」「要有飲料」「5 個人」等需求，AI 會推薦菜色組合。

---

## 本地開發與使用

### 安裝依賴

```powershell
pip install -r requirements.txt
playwright install chromium  # 若要使用爬蟲功能
```

### 啟動後端

```powershell
python -m uvicorn src.back:app --host 127.0.0.1 --port 8000 --reload
```

打開 http://127.0.0.1:8000/ 使用前端界面。

---

## 菜單爬蟲使用（新功能）

用 `crawler.py` 從 Google Maps 批量抓取餐廳菜單：

```powershell
# 抓取台中沙鹿餐廳菜單
python crawler.py "沙鹿 餐廳 菜單" --max-shops 10 --out menu_scraped.json
```

詳細用法請見 [CRAWLER_README.md](./CRAWLER_README.md)。

---

## 方案 B：手機用「公開網址」打開（不在同一個 Wi‑Fi 也能用）

最省事的方式是用 **Cloudflare Tunnel**（免費、穩定）或 **ngrok** 把你電腦的服務暫時公開。

### 0) 安裝 Python 依賴

在專案根目錄：

```powershell
py -m pip install -r requirements.txt
```

### 1) 啟動後端（只開在本機）

```powershell
py -m uvicorn src.back:app --host 127.0.0.1 --port 8000
```

確認本機可開：
- http://127.0.0.1:8000/
- http://127.0.0.1:8000/health

### 2A) 用 Cloudflare Tunnel 公開（推薦）

1. 安裝 cloudflared：
   - 下載：https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
2. 直接把本機服務公開：

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

cloudflared 會印出一個 `https://xxxxx.trycloudflare.com` 的網址。

### 2B) 用 ngrok 公開（備案）

1. 安裝 ngrok：https://ngrok.com/download
2. 登入/設定 token（依 ngrok 官網指示）
3. 公開：

```powershell
ngrok http 8000
```

ngrok 會顯示 `https://xxxxx.ngrok-free.app` 之類的網址。

### 3) 手機使用

用手機（4G/5G 或任何網路）打開上面得到的 `https://...` 公開網址即可。

## 注意事項

- Tunnel 公開的是「你本機的服務」。你電腦關機、或 uvicorn/ tunnel 關掉，手機就無法連線。
- 如果要長期正式上線，建議把 FastAPI 部署到雲端（Render/Fly.io/Railway/Azure 等）。

## 推薦：用 Render 正式雲端部署（網址固定、24/7）

這個 repo 已經提供 `render.yaml`（Render Blueprint）。

### 1) 把專案推到 GitHub

Render 需要從 GitHub 拉程式碼。若你還沒有 git repo：

```powershell
git init
git add .
git commit -m "init"
```

接著把它推到你的 GitHub（在 GitHub 建好空 repo 後照它的指令做）。

### 2) Render 建立 Web Service

1. 登入 https://render.com/
2. New → **Blueprint** → 連你的 GitHub repo → 選這個專案
3. Render 會讀取 `render.yaml` 並自動建立 Web Service

`render.yaml` 已設定：
- Build command：`pip install -r requirements.txt`
- Start command：`uvicorn src.back:app --host 0.0.0.0 --port $PORT`
- Health check：`/health`

### 3) 部署完成後手機怎麼用

Render 會給你一個固定網址（例如 `https://ordering-assistant.onrender.com`）。
手機直接打開這個網址即可使用。

### 4) 注意（免費方案常見）

- Free plan 可能會 idle，久沒人用第一次打開會比較慢（冷啟動）。
- 若你要更穩，升級付費方案即可。

