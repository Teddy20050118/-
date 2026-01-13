"""測試爬蟲功能"""
import asyncio
import sys
from crawler import crawl_google_maps, to_menu_json

async def test():
    print("開始測試爬蟲...")
    print("搜尋關鍵字: 台中 火鍋")
    
    restaurants = await crawl_google_maps(
        "台中 火鍋",
        max_shops=2,
        max_items_per_shop=20,
        headless=True
    )
    
    print(f"\n找到 {len(restaurants)} 間餐廳：\n")
    
    for i, r in enumerate(restaurants, 1):
        print(f"{i}. {r.name}")
        print(f"   評分: {r.rating or '無'}")
        print(f"   地址: {r.address or '無'}")
        print(f"   URL: {r.url or '無'}")
        print(f"   菜單項目數: {len(r.menu_items)}")
        
        if r.menu_items:
            print(f"   前 5 道菜:")
            for j, item in enumerate(r.menu_items[:5], 1):
                print(f"      {j}. {item.name} - {item.price or '無價格'}")
        print()
    
    menu_json = to_menu_json(restaurants)
    print(f"總共轉換 {len(menu_json)} 筆菜品資料")
    
    if menu_json:
        print("\n前 3 筆範例:")
        for item in menu_json[:3]:
            print(f"  - {item['dish']} @ {item['restaurant']} - {item['price']}")

if __name__ == "__main__":
    try:
        asyncio.run(test())
    except KeyboardInterrupt:
        print("\n使用者中斷")
        sys.exit(0)
    except Exception as e:
        print(f"\n錯誤: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
