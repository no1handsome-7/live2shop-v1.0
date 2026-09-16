"""
Build R2 SessionRec training examples from REAL Live2Shop attributed-behavior lake data.

Before running:
  make proto
  export PYTHONPATH=$PWD/gen/python:$PYTHONPATH

Download/copy MinIO files for:
  raw/live2shop.behavior.attributed.v1/...

Input parquet rows contain original protobuf payload bytes from lake-sink.
"""
from __future__ import annotations
import argparse,glob,math
from collections import defaultdict
from pathlib import Path
import numpy as np,pyarrow.parquet as pq
from live2shop.v1 import interest_pb2

MAX_L=64;KNEG=20

def norm(x):
    x=np.asarray(x,np.float32);n=np.linalg.norm(x);return x/max(n,1e-6)
def bucket(ms,b=32):
    if ms<=0:return 0
    return min(b-1,1+int(math.log1p(ms/1000)/math.log(1.7)))

ap=argparse.ArgumentParser()
ap.add_argument("--parquet-glob",required=True)
ap.add_argument("--item-embeddings",required=True)
ap.add_argument("--output-dir",required=True)
ap.add_argument("--seed",type=int,default=42)
a=ap.parse_args()

events=defaultdict(list)
for p in glob.glob(a.parquet_glob):
    tab=pq.read_table(p,columns=["payload"])
    for raw in tab["payload"].to_pylist():
        ev=interest_pb2.AttributedBehaviorEvent();ev.ParseFromString(raw)
        if not ev.contributions:continue
        v=np.zeros(128,np.float32);w=0;dur=0
        for c in ev.contributions:
            if c.content_embedding.dim!=128:continue
            q=float(c.attribution_weight);v+=q*np.asarray(c.content_embedding.values,np.float32);w+=q;dur+=c.overlap_ms
        if w<=0:continue
        events[int(ev.user_id)].append({"ts":ev.meta.event_time_ms,"action":int(ev.action),"dur":dur,"emb":norm(v/w)})

import pandas as pd
idf=pd.read_parquet(a.item_embeddings);pool=np.stack(idf["embedding"].map(lambda x:norm(x)).to_list())
rng=np.random.default_rng(a.seed);out=[];od=Path(a.output_dir);od.mkdir(parents=True,exist_ok=True)
for uid,seq in events.items():
    seq=sorted(seq,key=lambda x:x["ts"])
    for t in range(3,len(seq)-1):
        hist=seq[max(0,t-MAX_L+1):t+1];target=seq[t+1]["emb"]
        content=np.zeros((MAX_L,128),np.float32);act=np.zeros(MAX_L,np.int64);tb=np.zeros(MAX_L,np.int64);db=np.zeros(MAX_L,np.int64);mask=np.zeros(MAX_L,bool)
        off=MAX_L-len(hist);last=0
        for j,h in enumerate(hist,start=off):
            content[j]=h["emb"];act[j]=h["action"];tb[j]=bucket(h["ts"]-last if last else 0);db[j]=bucket(h["dur"]);mask[j]=True;last=h["ts"]
        neg=pool[rng.integers(0,len(pool),KNEG)]
        out.append((content,act,tb,db,mask,target,neg))
for part,start in enumerate(range(0,len(out),50000)):
    b=out[start:start+50000]
    np.savez_compressed(od/f"part-{part:04d}.npz",
      content=np.stack([x[0] for x in b]).astype(np.float16),action=np.stack([x[1] for x in b]),
      time_bucket=np.stack([x[2] for x in b]),duration_bucket=np.stack([x[3] for x in b]),
      mask=np.stack([x[4] for x in b]),positive=np.stack([x[5] for x in b]).astype(np.float16),
      negatives=np.stack([x[6] for x in b]).astype(np.float16))
print("real commerce bridge examples",len(out))
