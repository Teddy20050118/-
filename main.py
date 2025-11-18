#import
from __future__ import annotations
import os, json, re, shutil, subprocess, random, time
from typing import Dict, List, Optional, TypedDict, Literal, Tuple


DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
OLLAMA_BIN = os.getenv("OLLAMA_BIN", "ollama")

def _cli_available() -> bool:
    return shutil.which(OLLAMA_BIN) is not None

# 啟動 daemon
_DAEMON_SPAWNED = False
def ensure_daemon() -> None:
    global _DAEMON_SPAWNED
    if _DAEMON_SPAWNED:
        return
    if not _cli_available():
        raise RuntimeError(f"找不到 ollama 可執行檔，請設定 PATH 或 OLLAMA_BIN（目前：{OLLAMA_BIN}）。")
    try:
        kwargs = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = 0
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                creationflags |= subprocess.CREATE_NO_WINDOW
            DETACHED_PROCESS = 0x00000008
            creationflags |= DETACHED_PROCESS
            kwargs["startupinfo"] = startupinfo
            kwargs["creationflags"] = creationflags
        subprocess.Popen([OLLAMA_BIN, "serve"], **kwargs)  # 已在跑會快速返回
        time.sleep(0.3)  
    except Exception:
        pass
    _DAEMON_SPAWNED = True

def _cli_run(args: List[str], input_text: Optional[str] = None, timeout: float = 120.0) -> str:
    try:
        ensure_daemon()  # 啟動後台在 不開視窗
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

# 導入 Ollama 封裝
try:
    from utils.ollama_fuc import (
        recommend as ollama_recommend,
        chat as ollama_chat,
        ensure_daemon as ollama_ensure_daemon,
    )
except Exception:
    ollama_recommend = None  # type: ignore
    ollama_chat = None       # type: ignore
    ollama_ensure_daemon = None  # type: ignore


#  型別定義 
class Option(TypedDict, total=False):
    name: str
    extraPrice: Optional[float]


class MenuItem(TypedDict, total=False):
    name: str
    price: Optional[float]
    options: List[Option]
    tags: List[str]


class Category(TypedDict):
    name: str
    items: List[MenuItem]


class Menu(TypedDict):
    categories: List[Category]


class Preferences(TypedDict, total=False):
    spiceLevel: str
    excludes: List[str]
    budget: Optional[float]
    cuisine: Optional[str]
    notes: str
    needDrink: bool
    people: int
    weights: Dict[str, float]


class ConversationTurn(TypedDict, total=False):
    role: Literal["user", "assistant", "system"]
    content: str
    meta: Dict[str, object]


# JSON -> Menu 讀取

def write_menu_json(menu: Menu, out_path: str) -> None:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(menu, f, ensure_ascii=False, indent=2)


# 正規化規則 
_BEVERAGE_KEYWORDS = [
    "酒",     # 烈酒區、紅酒/清酒 等
    "啤酒",
    "清酒",
    "紅酒",
    "果汁",
    "茶",
    "飲料",
]
_BEVERAGE_EXACT = {"季節限定"}  #此分類皆為飲品


def _is_beverage_category(name: str) -> bool:
    if name in _BEVERAGE_EXACT:
        return True
    return any(k in name for k in _BEVERAGE_KEYWORDS)


def normalize_menu(menu: Menu) -> Dict[str, int]:
    """依需求正規化：
    1) price == 0 代表時價 → 為該品項加入『時價』標籤（若尚未存在）
    2) 酒與飲料類別（依分類名判定）移除所有『鹹度N』標籤

    回傳變更統計：{"market_price_tagged": x, "removed_salt_tags": y}
    """
    changed_market = 0
    removed_salt = 0

    for cat in menu.get('categories', []):
        is_bev = _is_beverage_category(cat.get('name', ''))
        for item in cat.get('items', []):
            tags = item.get('tags', [])
            #   時價標籤
            if item.get('price') == 0:
                if 'tags' not in item:
                    item['tags'] = []
                    tags = item['tags']
                if '時價' not in tags:
                    tags.append('時價')
                    changed_market += 1

            #   飲品移除鹹度
            if is_bev and tags:
                before = len(tags)
                item['tags'] = [t for t in tags if not (t.startswith('鹹度') and t[2:].isdigit())]
                removed_salt += before - len(item['tags'])

    return {"market_price_tagged": changed_market, "removed_salt_tags": removed_salt}


# 偏好抽取
_SPICE_WORDS = ["不辣", "微辣", "小辣", "中辣", "大辣", "很辣"]


def extract_prefs_from_text(text: str) -> Preferences:
    prefs: Preferences = {}
    t = text.strip()

    # 辣度
    for w in _SPICE_WORDS:
        if w in t:
            prefs["spiceLevel"] = "小辣" if w == "微辣" else w
            break

    # 忌口
    excludes: List[str] = []
    for cue in ("不要", "不吃", "忌口"):
        idx = t.find(cue)
        if idx != -1:
            seg = t[idx + len(cue):]
            for stop in ["。", " ", "，", ",", ";", "！", "?", "\n"]:
                cut = seg.find(stop)
                if cut != -1:
                    seg = seg[:cut]
                    break
            for p in re.split(r"[、,\s]+", seg):
                p = p.strip()
                if p:
                    excludes.append(p)
    if excludes:
        prefs["excludes"] = list(dict.fromkeys(excludes))

    # 預算
    m = re.search(r"(預算|不超過|小於|低於|<=)\s*(\d{2,6})", t)
    if not m:
        m = re.search(r"(\d{2,6})\s*(元|塊|NT|NTD)", t, flags=re.IGNORECASE)
    if m:
        try:
            prefs["budget"] = float(m.group(2) if m.lastindex and m.lastindex >= 2 else m.group(1))
        except Exception:
            pass

    # 菜系
    for c in ("中式", "日式", "泰式", "美式", "韓式", "義式"):
        if c in t:
            prefs["cuisine"] = c
            break
    
    need_drink_neg = re.search(r"(不要|不含|無)\s*飲料", t)
    if need_drink_neg:
        prefs["needDrink"] = False
    elif ("飲料" in t) or ("喝" in t):
        prefs["needDrink"] = True

    m2 = re.search(r"(\d{1,2})\s*人", t)
    if m2:
        try:
            prefs["people"] = int(m2.group(1))
        except Exception:
            pass

    # 飲料/人數
    need_drink = ("飲料" in t) or ("喝" in t)
    if need_drink:
        prefs["needDrink"] = True
    m2 = re.search(r"(\d{1,2})\s*人", t)
    if m2:
        try:
            prefs["people"] = int(m2.group(1))
        except Exception:
            pass

    # 動態權重線索
    cue_main = any(k in t for k in ["主菜", "吃飽", "份量", "大份", "有菜有肉"])
    cue_variety = any(k in t for k in ["多樣", "不要都一樣", "各點一些", "分著吃", "分享", "拼盤", "試試看"])
    cue_light = any(k in t for k in ["清爽", "清淡", "健康", "少油"])

    has_budget = prefs.get("budget") is not None
    constraint_count = sum([
        1 if has_budget else 0,
        1 if need_drink else 0,
        1 if "spiceLevel" in prefs else 0,
        1 if excludes else 0,
        1 if "cuisine" in prefs else 0,
        1 if cue_main else 0,
        1 if cue_variety else 0,
        1 if cue_light else 0,
    ])
    only_budget = has_budget and constraint_count == 1

    weights = {
        "price": 1.0 if only_budget else (0.8 if has_budget else 0.3),
        "main": 0.8 if cue_main else 0.5,
        "variety": 0.8 if cue_variety else 0.4,
        "drink": (0.6 if need_drink else -0.3),
        "spice": 0.7 if prefs.get("spiceLevel") == "不辣" or cue_light else 0.2,
        "category": 0.5,  # 類別基本權重
        "cuisine": 0.6 if "cuisine" in prefs else 0.0,
    }
    prefs["weights"] = weights
    return prefs


def merge_prefs_inplace(base: Preferences, delta: Preferences) -> None:
    if "budget" in delta and delta["budget"] is not None:
        base["budget"] = delta["budget"]
    if "people" in delta:
        base["people"] = delta["people"]
    if "spiceLevel" in delta:
        base["spiceLevel"] = delta["spiceLevel"]
    if "cuisine" in delta:
        base["cuisine"] = delta["cuisine"]
    if "needDrink" in delta:
        base["needDrink"] = delta["needDrink"]  # True 或 False 都接受
    if "excludes" in delta:
        base["excludes"] = list(dict.fromkeys([*base.get("excludes", []), *delta["excludes"]]))  # 去重合併
    if "weights" in delta:
        base["weights"] = delta["weights"]  # 每輪依新輸入動態重算
    if "notes" in delta:
        base["notes"] = delta["notes"]

def format_recommend_text(rec: Dict[str, object]) -> str:
    items = rec.get("items") if isinstance(rec, dict) else None
    if not isinstance(items, list) or not items:
        return "目前沒有明確推薦項目，可以再提供更多偏好（例如預算、忌口、辣度）。"
    lines: List[str] = ["我幫你挑了這些："]
    for i, it in enumerate(items, start=1):
        if not isinstance(it, dict):
            continue
        name = it.get("name")
        cat = it.get("category")
        price = it.get("price")
        reason = it.get("reason")
        price_str = ("時價" if (price in (None, 0)) else f"{price}")
        if cat:
            lines.append(f"{i}) [{cat}] {name} - {price_str}；理由：{reason}")
        else:
            lines.append(f"{i}) {name} - {price_str}；理由：{reason}")
    return "\n".join(lines)


#  既有骨架占位
def menu_to_json():
    # 從自由文字解析 補上文字->JSON 的parser
    return


conversation_history: List[ConversationTurn] = []  # 對話歷史



conversation_history: List[ConversationTurn] = []  # 對話歷史

def generate_conversation(
    history: List[ConversationTurn],
    user_input: str,
    menu: Menu,
    prefs: Preferences,
    model: Optional[str] = None,
) -> Tuple[str, List[ConversationTurn]]:
    history.append({"role": "user", "content": user_input, "meta": {}})

    # 抽取→就地合併（保留上一輪條件）
    dynamic = extract_prefs_from_text(user_input)
    dynamic.setdefault("notes", user_input)
    merge_prefs_inplace(prefs, dynamic)

    # 直接推薦（用累積後的 prefs）
    try:
        if ollama_recommend is None:
            raise RuntimeError("推薦功能未載入")
        rec = ollama_recommend(menu, prefs, top_k=5, model=model)
        reply = format_recommend_text(rec)
    except Exception as e:
        reply = f"推薦發生錯誤：{e}"

    history.append({"role": "assistant", "content": reply, "meta": {}})
    return reply, history




def _validate_menu(menu: Menu) -> None:
    if not isinstance(menu, dict) or 'categories' not in menu or not isinstance(menu['categories'], list):
        raise ValueError('menu.json 結構不正確：缺少 categories 或型別錯誤')
    for cat in menu['categories']:
        if not isinstance(cat, dict) or 'name' not in cat or 'items' not in cat:
            raise ValueError('menu.json 結構不正確：Category 需包含 name 與 items')
        if not isinstance(cat['items'], list):
            raise ValueError('menu.json 結構不正確：items 應為陣列')


def main():

    

    if callable(globals().get("ollama_ensure_daemon", None)):
        try:
            ollama_ensure_daemon()  # type: ignore
        except Exception:
            pass

    """讀取並驗證 utils/menu.json，套用正規化規則，並提供自然語言對話。"""
    base_dir = os.path.dirname(__file__)
    json_path = os.path.join(base_dir, 'utils', 'menu.json')

    if not os.path.exists(json_path):
        print(f"找不到 menu.json：{json_path}\n請直接建立或編輯此檔案以管理菜單資料。")
        return

    with open(json_path, 'r', encoding='utf-8') as f:
        menu: Menu = json.load(f)

    _validate_menu(menu)

    stats = normalize_menu(menu)
    if stats["market_price_tagged"] > 0 or stats["removed_salt_tags"] > 0:
        write_menu_json(menu, json_path)

    total_items = sum(len(c['items']) for c in menu['categories'])
    print(f"讀取 JSON: {json_path}")
    print(f"分類數: {len(menu['categories'])}，品項數: {total_items}")
    if stats["market_price_tagged"] > 0 or stats["removed_salt_tags"] > 0:
        print(f"已正規化：新增『時價』標籤 {stats['market_price_tagged']} 筆，移除飲品『鹹度N』標籤 {stats['removed_salt_tags']} 筆。")

    # 自然語言 REPL 
    prefs: Preferences = {}  # 作為 session 記憶，會被持續更新
    print("歡迎使用點餐推薦服務！")
    print("請問有什麼需求？（例如：預算 300、不辣、不要花生，要有飲料）")
    print("輸入 exit 離開。")
    print("輸入 reset/清除記憶 重置偏好。")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye")
            break
        if not text:
            continue
        if text.lower() in ("exit", "quit", "q"):
            print("Bye")
            break
        if text.lower() in ("reset",) or text in ("清除記憶", "清空", "重置", "重來"):
            conversation_history.clear()
            prefs.clear()
            print("已重置偏好與對話。")
            continue

        reply, _ = generate_conversation(conversation_history, text, menu, prefs)
        print(f"\n>> {reply}")


if __name__ == "__main__":
    main()

