"""
Import real/demo item metadata and room candidate pool into PostgreSQL.

Input JSONL fields:
  item_id,title,category_id,category_path,brand,style_tags,attributes,
  price_cent,inventory,image_uri,popularity_score,room_ids
"""
import argparse,json,os
import psycopg

ap=argparse.ArgumentParser()
ap.add_argument("--jsonl",required=True)
ap.add_argument("--dsn",default=os.getenv("POSTGRES_DSN","postgresql://live2shop:live2shop@localhost:5432/live2shop"))
a=ap.parse_args()

with psycopg.connect(a.dsn) as conn:
  with conn.cursor() as cur:
    for line in open(a.jsonl,encoding="utf-8"):
      if not line.strip():continue
      r=json.loads(line)
      cur.execute("""
        INSERT INTO items(item_id,title,category_id,category_path,brand,style_tags,attributes,
          price_cent,inventory,image_uri,popularity_score,status)
        VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,'ACTIVE')
        ON CONFLICT(item_id) DO UPDATE SET
          title=EXCLUDED.title,category_id=EXCLUDED.category_id,category_path=EXCLUDED.category_path,
          brand=EXCLUDED.brand,style_tags=EXCLUDED.style_tags,attributes=EXCLUDED.attributes,
          price_cent=EXCLUDED.price_cent,inventory=EXCLUDED.inventory,image_uri=EXCLUDED.image_uri,
          popularity_score=EXCLUDED.popularity_score,status='ACTIVE'
      """,(r["item_id"],r["title"],r.get("category_id"),r.get("category_path") or [],
           r.get("brand"),r.get("style_tags") or [],json.dumps(r.get("attributes") or {},ensure_ascii=False),
           r["price_cent"],r.get("inventory",100),r.get("image_uri"),r.get("popularity_score",1.0)))
      for room in r.get("room_ids") or []:
        cur.execute("""
          INSERT INTO room_items(room_id,item_id,sort_order)
          VALUES(%s,%s,%s)
          ON CONFLICT(room_id,item_id) DO NOTHING
        """,(room,r["item_id"],0))
  conn.commit()
print("catalog imported")
