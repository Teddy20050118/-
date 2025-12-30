import mysql.connector

# MySQL 連線設定 
DB_CONFIG = {
    "host": "localhost",
    "user": "root",         
    "password": "0000",  
    "database": "MyOrderingDB"
}

def get_connection():
    return mysql.connector.connect(**DB_CONFIG)

def query_menu(budget=None, must_tags=None, exclude_tags=None):
    """
    將使用者的需求轉換為 MySQL 查詢
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True) # 設定回傳字典格式，對應 ollama_fuc.py 的邏輯

    # 基礎查詢
    sql = """
        SELECT DISTINCT P.ProductName, P.Price, C.CategoryName
        FROM Products P
        JOIN Categories C ON P.CategoryID = C.CategoryID
        LEFT JOIN ProductTags PT ON P.ProductID = PT.ProductID
        LEFT JOIN Tags T ON PT.TagID = T.TagID
        WHERE 1=1
    """
    params = []

    # 1. 預算篩選 
    if budget and budget > 0:
        sql += " AND (P.Price <= %s OR P.Price = 0)" 
        params.append(budget)

    # 2. 必備標籤
    if must_tags:
        for tag in must_tags:
            sql += """
                AND P.ProductID IN (
                    SELECT PT2.ProductID 
                    FROM ProductTags PT2 
                    JOIN Tags T2 ON PT2.TagID = T2.TagID 
                    WHERE T2.TagName = %s
                )
            """
            params.append(tag)

    # 3. 排除標籤
    if exclude_tags:
        for tag in exclude_tags:
            sql += """
                AND P.ProductID NOT IN (
                    SELECT PT3.ProductID 
                    FROM ProductTags PT3 
                    JOIN Tags T3 ON PT3.TagID = T3.TagID 
                    WHERE T3.TagName = %s
                )
            """
            params.append(tag)
            
    # 隨機推薦 5 筆 (MySQL 使用 ORDER BY RAND() LIMIT 5)
    sql += " ORDER BY RAND() LIMIT 5"

    try:
        cursor.execute(sql, params)
        results = cursor.fetchall() # 因為設定 dictionary=True，這裡直接就是字典列表
        return results
    finally:
        cursor.close()
        conn.close()