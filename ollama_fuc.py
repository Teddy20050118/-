# ...existing code...
import os, json, re, shutil, subprocess, random, time
from typing import Any, Dict, List, Optional, Tuple

#從環境變數讀取設定
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
OLLAMA_BIN = os.getenv("OLLAMA_BIN", "ollama")

def _cli_available() -> bool:
    return shutil.which(OLLAMA_BIN) is not None #檢查路徑是否找到執行檔

# 靜默啟動 daemon
_DAEMON_SPAWNED = False
def ensure_daemon() -> None:
    global _DAEMON_SPAWNED
    if _DAEMON_SPAWNED: #避免重複啟動
        return
    if not _cli_available():
        raise RuntimeError(f"找不到 ollama 可執行檔，請設定 PATH 或 OLLAMA_BIN（目前：{OLLAMA_BIN}）。")
    kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    #Windows啟動子行程時隱藏視窗並背景執行而設計的參數設定
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            # |= (or)-> a = a or b
            creationflags |= subprocess.CREATE_NO_WINDOW
        #讓子行程與父行程的主控台分離，成為背景行程，不會跟隨父行程的主控台顯示與訊號（例如 Ctrl+C）而受影響。
        DETACHED_PROCESS = 0x00000008
        creationflags |= DETACHED_PROCESS
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = creationflags
    subprocess.Popen([OLLAMA_BIN, "serve"], **kwargs)
    time.sleep(0.3) #給服務0.3秒時間啟動
    _DAEMON_SPAWNED = True


#呼叫外部的 ollama 可執行檔並回傳結果
def _cli_run(args: List[str], input_text: Optional[str] = None, timeout: float = 120.0) -> str:
    try:
        ensure_daemon()
    except Exception:
        pass
    startupinfo = None
    creationflags = 0
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(
        [OLLAMA_BIN, *args],
        input=(input_text.encode("utf-8") if input_text is not None else None),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        startupinfo=startupinfo,
        creationflags=creationflags,
    )
    out = proc.stdout.decode("utf-8", errors="ignore").strip()
    err = proc.stderr.decode("utf-8", errors="ignore").strip()
    if proc.returncode != 0:
        raise RuntimeError(f"ollama 命令失敗: {' '.join([OLLAMA_BIN, *args])}\n{err}")
    return out or err


#把一串對話訊息 messages組裝成一段適合丟給 CLI/文字模型的提示字串
def _build_prompt_from_messages(messages: List[Dict[str, str]]) -> str:
    parts: List[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            parts.append(f"[系統] {content}")
        elif role == "assistant":
            parts.append(f"助理: {content}")
        else:
            parts.append(f"使用者: {content}")
    parts.append("助理:")
    return "\n".join(parts)
#把多輪對話messages轉成一段提示字串，再用CLI方式呼叫Ollama
def chat(messages: List[Dict[str, str]], model: Optional[str] = None, timeout: float = 120.0) -> str:
    mdl = model or DEFAULT_MODEL
    prompt = _build_prompt_from_messages(messages)
    return _cli_run(["run", mdl], input_text=prompt, timeout=timeout)

def _extract_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"(\{.*\}|\[.*\])", text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None

def recommend(menu: Dict[str, Any], prefs: Optional[Dict[str, Any]] = None, top_k: int = 5, model: Optional[str] = None) -> Dict[str, Any]:
    prefs = prefs or {}
    # 先嘗試模型
    try:
        system_prompt = "你是餐廳點餐助理，請依菜單與偏好推薦，並以服務生客氣的語氣回答所推薦的餐點。"
        payload = {"menu": menu, "prefs": prefs, "top_k": top_k}
        reply = chat(
            [{"role": "system", "content": system_prompt},
             {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            model=model,
        )
        obj = _extract_json(reply)
        if isinstance(obj, dict) and "items" in obj:
            return obj
        raise RuntimeError("模型回覆不是有效 JSON")
    except Exception:
        # 備用規則：動態權重 + 總預算約束 + 多樣性 
        weights: Dict[str, float] = dict(prefs.get("weights", {})) if isinstance(prefs.get("weights"), dict) else {}
        W_PRICE = float(weights.get("price", 0.5))
        W_MAIN = float(weights.get("main", 0.5))
        W_VAR = float(weights.get("variety", 0.3))
        W_DRINK = float(weights.get("drink", -0.3))
        W_SPICE = float(weights.get("spice", 0.2))
        W_CATE = float(weights.get("category", 0.4))
        W_CUIS = float(weights.get("cuisine", 0.0))

        budget: Optional[float] = None
        if isinstance(prefs.get("budget"), (int, float, str)):
            try:
                budget = float(prefs["budget"])  # type: ignore
            except Exception:
                budget = None
        want_spice = prefs.get("spiceLevel")
        excludes = [str(x) for x in prefs.get("excludes", [])] if isinstance(prefs.get("excludes"), list) else []
        need_drink = bool(prefs.get("needDrink"))
        cuisine = prefs.get("cuisine")
        people = 0
        try:
            if isinstance(prefs.get("people"), (int, float, str)):
                people = int(float(prefs["people"]))
        except Exception:
            people = 0

        seed_text = f"{prefs.get('notes','')}|{budget}|{need_drink}|{want_spice}|{excludes}|{cuisine}|{people}|{weights}"
        random.seed(seed_text)

        def is_bev(cat_name: str) -> bool:
            cat_name = str(cat_name or "")
            return any(kw in cat_name for kw in ["酒", "啤酒", "清酒", "紅酒", "果汁", "茶", "飲料"])

        CATEGORY_BASE = {
            "經典鍋物": 5,
            "明火好味": 5,
            "潮粵燒臘": 4,
            "生冷美饌": 3,
            "手作小菜": 2,
            "水果甜品": 1,
        }

        def base_cat_weight(name: str) -> float:
            if is_bev(name):
                return -2.0 if not need_drink else 1.0
            return float(CATEGORY_BASE.get(name, 2))

        def allowed(cat_name: str, it: Dict[str, Any]) -> bool:
            nm = str(it.get("name", ""))
            if excludes and any(x and x in nm for x in excludes):
                return False
            tags = it.get("tags") or []
            if excludes and any(x for x in excludes if any(x in str(t) for t in tags)):
                return False
            if want_spice == "不辣" and any(("辣" in str(t)) for t in (tags or [])):
                return False
            return True

        # 建候選，計算基礎分數
        candidates: List[Tuple[str, Dict[str, Any], float]] = []
        for cat in menu.get("categories", []):
            cat_name = str(cat.get("name", ""))
            cat_w = base_cat_weight(cat_name)
            for it in (cat.get("items") or []):
                if not isinstance(it, dict) or not allowed(cat_name, it):
                    continue
                pr = it.get("price")
                # 價格分數：越便宜越高，None/0 視為中間
                if pr in (None, 0):
                    price_score = 0.5
                else:
                    try:
                        price_score = max(0.0, 1.0 - min(float(pr), 3000.0) / 3000.0)
                    except Exception:
                        price_score = 0.5
                score = 0.0
                score += W_CATE * cat_w
                score += W_MAIN * (1.0 if cat_w >= 4 else 0.3)
                score += W_PRICE * price_score
                if is_bev(cat_name):
                    score += W_DRINK  # 正或負
                # 不辣加分
                if want_spice == "不辣":
                    tags = it.get("tags") or []
                    if not any(("辣" in str(t)) for t in (tags or [])):
                        score += W_SPICE
                # 菜系偏好
                if cuisine and (cuisine in str(it.get("name","")) or cuisine in (it.get("tags") or [])):
                    score += W_CUIS
                # 輕度打散
                score += random.uniform(0, 0.3)
                candidates.append((cat_name, it, score))

        if not candidates:
            return {"items": [], "notes": ""}

        # 逐步挑選：兼顧總預算與多樣性（類別重覆懲罰）
        target_k = top_k
        if people and isinstance(people, int) and people > 0:
            target_k = min(top_k, max(people, 2))

        picked: List[Tuple[str, Dict[str, Any]]] = []
        total_cost = 0.0
        cat_counts: Dict[str, int] = {}

        def can_add(cat_name: str, it: Dict[str, Any]) -> bool:
            pr = it.get("price")
            if budget is not None and pr not in (None, 0):
                try:
                    price = float(pr)
                except Exception:
                    return False
                if total_cost + price > budget:
                    return False
            return True

        def do_add(cat_name: str, it: Dict[str, Any]) -> None:
            nonlocal total_cost
            pr = it.get("price")
            if budget is not None and pr not in (None, 0):
                try:
                    total_cost += float(pr)
                except Exception:
                    pass
            picked.append((cat_name, it))
            cat_counts[cat_name] = cat_counts.get(cat_name, 0) + 1

        # 若需要飲料，先保證一杯可負擔的飲品
        if need_drink:
            bev_list = [(c, it, s) for (c, it, s) in candidates if is_bev(c)]
            bev_list.sort(key=lambda x: (1e9 if x[1].get("price") in (None, 0) else float(x[1].get("price")), -x[2]))
            for c, it, _ in bev_list:
                if can_add(c, it):
                    do_add(c, it)
                    break

        # 迭代挑選其餘項目：每輪根據當前已選給相同分類扣分
        while len(picked) < target_k:
            best = None
            best_score = -1e9
            for c, it, base in candidates:
                if any(it is p_it for _, p_it in picked):
                    continue
                adj = base - W_VAR * 1.5 * float(cat_counts.get(c, 0))
                if adj > best_score and can_add(c, it):
                    best_score = adj
                    best = (c, it)
            if best is None:
                break
            do_add(best[0], best[1])

        # 若因預算太低一個都選不到，保底挑最便宜
        if not picked and candidates:
            cheapest = min(candidates, key=lambda x: (1e9 if x[1].get("price") in (None, 0) else float(x[1].get("price"))))
            picked.append((cheapest[0], cheapest[1]))

        items: List[Dict[str, Any]] = []
        for cat_name, it in picked[:target_k]:
            pr = it.get("price")
            reason_bits: List[str] = []
            if budget is not None and pr not in (None, 0):
                try:
                    reason_bits.append("符合總預算" if float(pr) <= budget else "價格折衷")
                except Exception:
                    pass
            if need_drink and is_bev(cat_name):
                reason_bits.append("包含飲品")
            if want_spice == "不辣":
                reason_bits.append("口味清爽")
            if base_cat_weight(cat_name) >= 4:
                reason_bits.append("主菜優先")
            if not reason_bits:
                reason_bits.append("綜合評估推薦")
            items.append({
                "name": it.get("name"),
                "price": pr,
                "category": cat_name,
                "reason": "、".join(reason_bits),
            })

        return {"items": items, "notes": ""}
