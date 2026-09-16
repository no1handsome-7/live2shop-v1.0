"""
Index item embeddings produced by scripts/encode_item_catalog.py.

Input parquet columns:
  item_id: int64
  embedding: list<float>[128]
"""
import argparse
import os
import pandas as pd
from pymilvus import Collection, connections, utility

ap = argparse.ArgumentParser()
ap.add_argument("--parquet", required=True)
ap.add_argument("--host", default=os.getenv("MILVUS_HOST","localhost"))
ap.add_argument("--port", default=os.getenv("MILVUS_PORT","19530"))
ap.add_argument("--collection", default=os.getenv("MILVUS_COLLECTION","live2shop_items"))
args = ap.parse_args()

connections.connect(host=args.host, port=args.port)
if not utility.has_collection(args.collection):
    raise SystemExit("collection missing; start rec-model-server once first")
c = Collection(args.collection)
df = pd.read_parquet(args.parquet)
batch = 5000
for start in range(0, len(df), batch):
    part = df.iloc[start:start+batch]
    c.upsert([
        part["item_id"].astype("int64").tolist(),
        ["commerce-space-v1"] * len(part),
        part["embedding"].tolist(),
    ])
    print("indexed", min(start+batch,len(df)), "/", len(df))
c.flush()
