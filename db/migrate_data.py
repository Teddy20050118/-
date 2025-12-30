import json
import os
import mysql.connector

# 1. MySQL 連線設定 
db_config = {
    "host": "localhost",
    "user": "root",         
    "password": "0000", 
    "database": "MyOrderingDB"
}

def migrate():
    base_dir = os.path.dirname(__file__)
    json_path = os.path.join(base_dir, 'db', 'menu.json') 
    
    if not os.path.exists(json_path):
         
        json_path = os.path.join(base_dir, 'menu.json')

    with open(json_path, 'r', encoding='utf-8') as f:
        menu_data = json.load(f)

    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        print("開始匯入資料到 MySQL...")

        # 3. 遍歷 JSON 結構
        for category in menu_data.get('categories', []):
            cat_name = category['name']
            
            # 寫入分類
            cursor.execute("SELECT CategoryID FROM Categories WHERE CategoryName = %s", (cat_name,))
            row = cursor.fetchone()
            
            if row:
                cat_id = row[0]
            else:
                cursor.execute("INSERT INTO Categories (CategoryName) VALUES (%s)", (cat_name,))
                cat_id = cursor.lastrowid # MySQL 取得剛插入 ID 的方法
                print(f"新增分類: {cat_name} (ID: {cat_id})")

            # B. 寫入產品
            for item in category.get('items', []):
                p_name = item['name']
                p_price = item.get('price', 0)
                
                cursor.execute("SELECT ProductID FROM Products WHERE ProductName = %s", (p_name,))
                p_row = cursor.fetchone()
                
                if p_row:
                    p_id = p_row[0]
                else:
                    cursor.execute("""
                        INSERT INTO Products (ProductName, Price, CategoryID) 
                        VALUES (%s, %s, %s)
                    """, (p_name, p_price, cat_id))
                    p_id = cursor.lastrowid
                    print(f"  - 新增產品: {p_name}")

                # C. 處理標籤
                for tag_text in item.get('tags', []):
                    cursor.execute("SELECT TagID FROM Tags WHERE TagName = %s", (tag_text,))
                    t_row = cursor.fetchone()
                    
                    if t_row:
                        tag_id = t_row[0]
                    else:
                        cursor.execute("INSERT INTO Tags (TagName) VALUES (%s)", (tag_text,))
                        tag_id = cursor.lastrowid

                    # 建立關聯
                    cursor.execute("SELECT 1 FROM ProductTags WHERE ProductID = %s AND TagID = %s", (p_id, tag_id))
                    if not cursor.fetchone():
                        cursor.execute("INSERT INTO ProductTags (ProductID, TagID) VALUES (%s, %s)", (p_id, tag_id))

        conn.commit()
        print("\n資料匯入成功！")

    except Exception as e:
        print(f"發生錯誤: {e}")
        if 'conn' in locals() and conn.is_connected():
            conn.rollback()
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    migrate()