#!/usr/bin/env python3
"""
setup_and_run.py

一鍵初始化資料庫、匯入菜單資料，並可選擇啟動 FastAPI 伺服器。

用法範例：
  python setup_and_run.py --all
  python setup_and_run.py --init-db --migrate --start-server --background

環境變數：DB_HOST, DB_USER, DB_PASS, DB_NAME
若未設定，會互動式提示輸入。
"""
import os
import sys
import json
import argparse
import subprocess
import mysql.connector
from mysql.connector import errorcode
from typing import Dict, Any, List

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))


def get_db_config() -> Dict[str, Any]:
    cfg = {
        "host": os.environ.get("DB_HOST", "localhost"),
        "user": os.environ.get("DB_USER", "root"),
        "password": os.environ.get("DB_PASS", ""),
        "database": os.environ.get("DB_NAME", "MyOrderingDB"),
    }
    # If password is empty and running interactively, prompt
    if not cfg["password"] and sys.stdin.isatty():
        try:
            import getpass
            cfg["password"] = getpass.getpass(prompt="MySQL password for %s@%s: " % (cfg["user"], cfg["host"]))
        except Exception:
            pass
    return cfg


def check_connection(cfg: Dict[str, Any]) -> bool:
    try:
        conn = mysql.connector.connect(**cfg)
        conn.close()
        print("[OK] 成功連線到 MySQL")
        return True
    except mysql.connector.Error as e:
        print(f"[ERR] 無法連線 MySQL: {e}")
        return False


def execute_sql_file(cfg: Dict[str, Any], sql_path: str) -> None:
    print(f"執行 SQL 檔: {sql_path}")
    if not os.path.exists(sql_path):
        raise FileNotFoundError(f"找不到 SQL 檔: {sql_path}")

    with open(sql_path, 'r', encoding='utf-8') as f:
        sql_text = f.read()

    try:
        conn = mysql.connector.connect(**{k: v for k, v in cfg.items() if k != 'database'} )
        cursor = conn.cursor()
        # menudb.sql 建 DB 並 USE DB; use multi=True
        for result in cursor.execute(sql_text, multi=True):
            # consume results
            pass
        cursor.close()
        conn.close()
        print("[OK] SQL 已執行 (含建立 database/schema)")
    except mysql.connector.Error as e:
        print(f"[ERR] 執行 SQL 檔失敗: {e}")
        raise


def migrate_menu(cfg: Dict[str, Any], menu_paths: List[str]) -> None:
    # 找到第一個存在的 menu.json
    menu_path = None
    for p in menu_paths:
        if os.path.exists(p):
            menu_path = p
            break
    if menu_path is None:
        raise FileNotFoundError(f"找不到 menu.json，已搜尋: {menu_paths}")

    print(f"使用 menu 檔案: {menu_path}")
    with open(menu_path, 'r', encoding='utf-8') as f:
        menu_data = json.load(f)

    try:
        conn = mysql.connector.connect(**cfg)
        cursor = conn.cursor()

        print("開始匯入資料到 MyOrderingDB...")
        for category in menu_data.get('categories', []):
            cat_name = category.get('name')
            if not cat_name:
                print("跳過沒有 name 的分類")
                continue

            cursor.execute("SELECT CategoryID FROM Categories WHERE CategoryName = %s", (cat_name,))
            row = cursor.fetchone()
            if row:
                cat_id = row[0]
            else:
                cursor.execute("INSERT INTO Categories (CategoryName) VALUES (%s)", (cat_name,))
                cat_id = cursor.lastrowid
                print(f"新增分類: {cat_name} (ID: {cat_id})")

            for item in category.get('items', []) or []:
                p_name = item.get('name')
                if not p_name:
                    # skip invalid items
                    continue
                p_price = item.get('price', 0) or 0

                cursor.execute("SELECT ProductID FROM Products WHERE ProductName = %s", (p_name,))
                p_row = cursor.fetchone()
                if p_row:
                    p_id = p_row[0]
                else:
                    cursor.execute(
                        "INSERT INTO Products (ProductName, Price, CategoryID) VALUES (%s, %s, %s)",
                        (p_name, p_price, cat_id),
                    )
                    p_id = cursor.lastrowid
                    print(f"  - 新增產品: {p_name}")

                tags = item.get('tags') or []
                for tag_text in tags:
                    if not tag_text:
                        continue
                    cursor.execute("SELECT TagID FROM Tags WHERE TagName = %s", (tag_text,))
                    t_row = cursor.fetchone()
                    if t_row:
                        tag_id = t_row[0]
                    else:
                        cursor.execute("INSERT INTO Tags (TagName) VALUES (%s)", (tag_text,))
                        tag_id = cursor.lastrowid

                    cursor.execute("SELECT 1 FROM ProductTags WHERE ProductID = %s AND TagID = %s", (p_id, tag_id))
                    if not cursor.fetchone():
                        cursor.execute("INSERT INTO ProductTags (ProductID, TagID) VALUES (%s, %s)", (p_id, tag_id))

        conn.commit()
        print("[OK] 資料匯入成功！")
    except Exception as e:
        print(f"[ERR] 匯入過程發生錯誤: {e}")
        if 'conn' in locals() and conn.is_connected():
            conn.rollback()
        raise
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()


def start_server(host: str = '127.0.0.1', port: int = 8000, background: bool = False) -> None:
    cmd = [sys.executable, '-m', 'uvicorn', 'back:app', '--host', host, '--port', str(port)]
    if background:
        print(f"在背景啟動伺服器: {' '.join(cmd)}")
        # On Windows, DETACHED_PROCESS flag to detach
        creationflags = 0
        if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
            creationflags |= subprocess.CREATE_NO_WINDOW
        subprocess.Popen(cmd, creationflags=creationflags)
    else:
        print(f"啟動伺服器: {' '.join(cmd)} (Ctrl+C 以停止)")
        subprocess.run(cmd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--init-db', action='store_true', help='建立資料庫與 schema (會執行 menudb.sql)')
    parser.add_argument('--migrate', action='store_true', help='從 menu.json 匯入資料到資料庫')
    parser.add_argument('--start-server', action='store_true', help='啟動 FastAPI 伺服器 (uvicorn back:app)')
    parser.add_argument('--background', action='store_true', help='若啟動伺服器則在背景執行')
    parser.add_argument('--all', action='store_true', help='等同於 --init-db --migrate --start-server')
    parser.add_argument('--sql-path', default=os.path.join(PROJECT_ROOT, 'db', 'menudb.sql'))
    parser.add_argument('--menu-paths', nargs='*', default=[
        os.path.join(PROJECT_ROOT, 'menu.json'),
        os.path.join(PROJECT_ROOT, 'db', 'menu.json'),
    ])
    args = parser.parse_args()

    cfg = get_db_config()
    if not check_connection(cfg):
        print('請確認 MySQL 正在執行並且 DB 設定正確（或使用環境變數 DB_HOST/DB_USER/DB_PASS/DB_NAME）')
        sys.exit(1)

    if args.all:
        args.init_db = args.migrate = args.start_server = True

    if args.init_db:
        execute_sql_file(cfg, args.sql_path)

    if args.migrate:
        migrate_menu(cfg, args.menu_paths)

    if args.start_server:
        start_server(background=args.background)


if __name__ == '__main__':
    main()
