import pyodbc

# 連線字串 (請確認與 migrate_data.py 相同)
CONN_STR = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost;"
    "DATABASE=master;"
    "Trusted_Connection=yes;"
)

def get_connection():
    return pyodbc.connect(CONN_STR)

def query_menu(budget=None, must_tags=None, exclude_tags=None):
    """
    將使用者的需求轉換為 SQL 查詢 (應用 Ch08 WHERE 與 Ch09 JOIN)
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 基礎查詢：連結 產品、類別、標籤 (應用 Ch09 內合併)
    sql = """
        SELECT DISTINCT P.ProductID, P.ProductName, P.Price, C.CategoryName
        FROM Products P
        JOIN Categories C ON P.CategoryID = C.CategoryID
        LEFT JOIN ProductTags PT ON P.ProductID = PT.ProductID
        LEFT JOIN Tags T ON PT.TagID = T.TagID
        WHERE 1=1
    """
    params = []

    # 1. 預算篩選 (應用 Ch08 比較運算子)
    if budget and budget > 0:
        sql += " AND (P.Price <= ? OR P.Price = 0)" # 0代表時價
        params.append(budget)

    # 2. 必備標籤 (例如: "素") - 應用 Ch08 邏輯運算
    if must_tags:
        for tag in must_tags:
            # 這裡使用子查詢技巧 (Ch09 巢狀結構查詢) 確保產品有該標籤
            sql += """
                AND P.ProductID IN (
                    SELECT PT2.ProductID 
                    FROM ProductTags PT2 
                    JOIN Tags T2 ON PT2.TagID = T2.TagID 
                    WHERE T2.TagName = ?
                )
            """
            params.append(tag)

    # 3. 排除標籤 (例如: "辣") - 應用 Ch06 差集概念 (NOT IN)
    if exclude_tags:
        for tag in exclude_tags:
            sql += """
                AND P.ProductID NOT IN (
                    SELECT PT3.ProductID 
                    FROM ProductTags PT3 
                    JOIN Tags T3 ON PT3.TagID = T3.TagID 
                    WHERE T3.TagName = ?
                )
            """
            params.append(tag)
            
    final_sql = f"""
        WITH Candidate AS (
{sql}
        )
        SELECT TOP 5 ProductName, Price, CategoryName
        FROM Candidate
        ORDER BY NEWID()
    """

    cursor.execute(final_sql, params)
    columns = [column[0] for column in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    
    conn.close()
    return results