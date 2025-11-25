import json
import os
import pyodbc 

# 1. 資料庫連線設定 (請修改為你的 SQL Server 設定)
# 如果是本機開發，通常 server 是 'localhost' 或電腦名稱
# Driver 可能需要依據你安裝的版本修改，如 'ODBC Driver 17 for SQL Server'
conn_str = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost;"
    "DATABASE=master;"  # 請確認你的資料庫名稱
    "Trusted_Connection=yes;" # 使用 Windows 驗證，若用帳密請改為 UID=sa;PWD=密碼;
)

def migrate():
    # 2. 讀取 menu.json
    base_dir = os.path.dirname(__file__)
    json_path = os.path.join(base_dir, 'db', 'menu.json') # 確認你的 json 路徑
    
    if not os.path.exists(json_path):
        # 備用路徑檢查，依據你提供的檔案結構
        json_path = os.path.join(base_dir, 'db', 'menu.json')

    with open(json_path, 'r', encoding='utf-8') as f:
        menu_data = json.load(f)

    try:
        conn = pyodbc.connect(conn_str)
        cursor = conn.cursor()#sqlserver 裡面的游標
        
        print("開始匯入資料...")

        # 3. 遍歷 JSON 結構
        for category in menu_data.get('categories', []):
            cat_name = category['name']
            
            # A. 寫入分類 (Categories)
            # 先檢查是否存在，不存在才新增，避免重複執行出錯
            cursor.execute("SELECT CategoryID FROM Categories WHERE CategoryName = ?", cat_name)
            row = cursor.fetchone()
            
            if row:
                cat_id = row[0]
            else:
                cursor.execute("INSERT INTO Categories (CategoryName) OUTPUT INSERTED.CategoryID VALUES (?)", cat_name)
                cat_id = cursor.fetchone()[0]
                print(f"新增分類: {cat_name} (ID: {cat_id})")

            # B. 寫入產品 (Products)
            for item in category.get('items', []):
                p_name = item['name']
                p_price = item.get('price', 0) # 若無價格則設為 0
                
                # 檢查產品是否已存在
                cursor.execute("SELECT ProductID FROM Products WHERE ProductName = ?", p_name)
                p_row = cursor.fetchone()
                
                if p_row:
                    p_id = p_row[0]
                else:
                    cursor.execute("""
                        INSERT INTO Products (ProductName, Price, CategoryID) 
                        OUTPUT INSERTED.ProductID 
                        VALUES (?, ?, ?)
                    """, (p_name, p_price, cat_id))
                    p_id = cursor.fetchone()[0]
                    print(f"  - 新增產品: {p_name}")

                # C. 處理標籤 (Tags & ProductTags)
                for tag_text in item.get('tags', []):
                    # 檢查標籤是否存在 Tags 表
                    cursor.execute("SELECT TagID FROM Tags WHERE TagName = ?", tag_text)
                    t_row = cursor.fetchone()
                    
                    if t_row:
                        tag_id = t_row[0]
                    else:
                        cursor.execute("INSERT INTO Tags (TagName) OUTPUT INSERTED.TagID VALUES (?)", tag_text)
                        tag_id = cursor.fetchone()[0]
                        # print(f"    * 新增標籤: {tag_text}")

                    # 建立產品與標籤的關聯 (ProductTags)
                    # 先檢查關聯是否存在
                    cursor.execute("SELECT 1 FROM ProductTags WHERE ProductID = ? AND TagID = ?", (p_id, tag_id))
                    if not cursor.fetchone():
                        cursor.execute("INSERT INTO ProductTags (ProductID, TagID) VALUES (?, ?)", (p_id, tag_id))

        conn.commit()
        print("\n資料匯入成功！")

    except Exception as e:
        print(f"發生錯誤: {e}")
        if 'conn' in locals():
            conn.rollback()
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    migrate()