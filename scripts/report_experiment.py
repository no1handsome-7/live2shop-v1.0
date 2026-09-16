import argparse,os
import psycopg

ap=argparse.ArgumentParser()
ap.add_argument("--experiment",default="live2shop-ranking-v1")
ap.add_argument("--dsn",default=os.getenv("POSTGRES_DSN","postgresql://live2shop:live2shop@localhost:5432/live2shop"))
a=ap.parse_args()

sql="""
WITH x AS (
 SELECT
   experiment_variant AS variant,
   COUNT(DISTINCT impression_id) FILTER (WHERE action='IMPRESSION') AS impressions,
   COUNT(DISTINCT impression_id) FILTER (WHERE action='CLICK') AS clicks,
   COUNT(DISTINCT impression_id) FILTER (WHERE action='ADD_CART') AS carts,
   COUNT(DISTINCT impression_id) FILTER (WHERE action='PURCHASE') AS purchases
 FROM recommendation_feedback
 WHERE experiment_id=%s
 GROUP BY experiment_variant
)
SELECT variant,impressions,clicks,carts,purchases,
       CASE WHEN impressions>0 THEN clicks::double precision/impressions ELSE 0 END AS ctr,
       CASE WHEN impressions>0 THEN carts::double precision/impressions ELSE 0 END AS cart_rate,
       CASE WHEN impressions>0 THEN purchases::double precision/impressions ELSE 0 END AS purchase_rate
FROM x ORDER BY variant
"""
with psycopg.connect(a.dsn) as conn:
  with conn.cursor() as cur:
    cur.execute(sql,(a.experiment,))
    print("variant\timpressions\tclicks\tcarts\tpurchases\tCTR\tcart_rate\tpurchase_rate")
    for r in cur.fetchall():
      print("\t".join(str(round(x,6) if isinstance(x,float) else x) for x in r))
