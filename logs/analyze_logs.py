import json
import os
from collections import Counter
from typing import Any, Dict

import matplotlib.pyplot as plt
from matplotlib import font_manager, rcParams

BASE_DIR = os.path.dirname(__file__)
LOG_PATH = os.path.join(BASE_DIR, "logs", "chat_log.jsonl")

def _set_chinese_font() -> None:
    candidate_fonts = ["Microsoft JhengHei", "Microsoft YaHei", "SimHei"]
    found_font = None

    for name in candidate_fonts:
        try:
            font = font_manager.FontProperties(family=name)
            rcParams["font.family"] = font.get_name()
            found_font = font.get_name()
            break
        except Exception:
            continue

    if not found_font:
        print("警告：找不到預設的中文字型，可能會有亂碼或口字框。")
    else:
        print(f"已設定中文字型：{found_font}")

    rcParams["axes.unicode_minus"] = False


_set_chinese_font()
BASE_DIR = os.path.dirname(__file__)
LOG_PATH = os.path.join(BASE_DIR, "logs", "chat_log.jsonl")


def load_logs() -> list[Dict[str, Any]]:
    data = []
    if not os.path.exists(LOG_PATH):
        print(f"找不到 log 檔：{LOG_PATH}")
        return data

    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                data.append(obj)
            except Exception as e:
                print(f"略過壞掉的一行：{e} -> {line[:80]}...")
    return data


def _plot_bar(
    counter: Counter,
    title: str,
    filename: str | None = None,
    ordered_keys: list[str] | None = None,
) -> None:
    """將 Counter 結果畫成簡單長條圖，預設直接顯示；如有 filename 也會順便存成 PNG。"""
    if not counter:
        print(f"{title}：目前沒有資料，略過繪圖")
        return

    if ordered_keys is None:
        labels = list(counter.keys())
    else:
        labels = [k for k in ordered_keys if k in counter]
    values = [counter[k] for k in labels]

    plt.figure(figsize=(7, 4))
    bars = plt.bar(labels, values, color="#4f81bd")
    plt.title(title)
    plt.ylabel("筆數")

    # 在每個長條上方標數字
    for bar, v in zip(bars, values):
        plt.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            str(v),
            ha="center",
            va="bottom",
        )

    plt.tight_layout()

    # 如果有給檔名，就存一份
    if filename:
        out_dir = os.path.join(BASE_DIR, "logs")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, filename)
        plt.savefig(out_path, dpi=120)
        print(f"已產生圖檔：{out_path}")

    # 直接顯示圖
    plt.show()
    plt.close()


def main() -> None:
    logs = load_logs()
    if not logs:
        print("目前沒有任何對話紀錄。先和點餐助理聊幾句再來分析吧。")
        return

    total_msgs = len(logs)
    sessions = {log.get("sessionId") for log in logs}
    print(f"總訊息數：{total_msgs}")
    print(f"不同 session 數量：{len(sessions)}")

    # 統計偏好
    budget_bins: Counter[str] = Counter()
    need_drink_counter: Counter[str] = Counter()
    spice_counter: Counter[str] = Counter()

    for log in logs:
        prefs = log.get("prefs") or {}

        # 預算區間
        budget = prefs.get("budget")
        if isinstance(budget, (int, float)):
            b = float(budget)
            if b < 500:
                budget_bins["<500"] += 1
            elif b <= 1000:
                budget_bins["500-1000"] += 1
            else:
                budget_bins[">1000"] += 1

        # 飲料需求
        need_drink = prefs.get("needDrink")
        if need_drink is True:
            need_drink_counter["要飲料"] += 1
        elif need_drink is False:
            need_drink_counter["不要飲料"] += 1
        else:
            need_drink_counter["未提到"] += 1

        # 辣度
        spice = prefs.get("spiceLevel")
        if isinstance(spice, str) and spice:
            spice_counter[spice] += 1

    print("\n[預算區間統計]")
    if budget_bins:
        for k in ["<500", "500-1000", ">1000"]:
            if budget_bins[k]:
                print(f"  {k}：{budget_bins[k]} 筆")
    else:
        print("  尚無預算相關紀錄")

    print("\n[是否要求飲料]")
    for k, v in need_drink_counter.items():
        print(f"  {k}：{v} 筆")

    print("\n[辣度偏好]")
    if spice_counter:
        for k, v in spice_counter.items():
            print(f"  {k}：{v} 筆")
    else:
        print("  尚無辣度相關紀錄")

        # 產生圖表（會直接跳出視窗；同時也存檔）
    _plot_bar(budget_bins, "預算區間統計", None, ["<500", "500-1000", ">1000"])
    _plot_bar(need_drink_counter, "是否要求飲料", None, ["要飲料", "不要飲料", "未提到"])
    _plot_bar(spice_counter, "辣度偏好", None)


if __name__ == "__main__":
    main()
