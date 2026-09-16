"""
Bootstrap R2 commerce-space adaptation before enough online feedback exists.

This is synthetic ONLY for adapting external 128D embedding spaces to
`commerce-space-v1`. Do not report business lift from this dataset.

Inputs:
  item_embeddings.parquet: item_id, embedding[128]
  catalog JSONL with item_id + style_tags/category_path

The generator samples a latent preference tag set, produces noisy
watch/like/click sessions, and asks the Session Transformer to predict the
next positively engaged item.
"""
from __future__ import annotations
import argparse,json,math,random
from collections import defaultdict
from pathlib import Path
import numpy as np,pandas as pd

MAX_L=64;KNEG=20

def bucket(ms,b=32):
    if ms<=0:return 0
    return min(b-1,1+int(math.log1p(ms/1000)/math.log(1.7)))

ap=argparse.ArgumentParser()
ap.add_argument("--embeddings",required=True)
ap.add_argument("--catalog-jsonl",required=True)
ap.add_argument("--output-dir",required=True)
ap.add_argument("--sessions",type=int,default=30000)
ap.add_argument("--seed",type=int,default=42)
a=ap.parse_args()
rng=np.random.default_rng(a.seed);pr=random.Random(a.seed)

edf=pd.read_parquet(a.embeddings);emb={int(r.item_id):np.asarray(r.embedding,np.float32) for r in edf.itertuples()}
meta={}
bytag=defaultdict(list)
for line in open(a.catalog_jsonl,encoding="utf-8"):
    if not line.strip():continue
    r=json.loads(line);iid=int(r["item_id"])
    if iid not in emb:continue
    tags=set(r.get("style_tags") or [])|set(r.get("category_path") or [])
    meta[iid]=tags
    for t in tags:bytag[t].append(iid)
ids=np.array(list(meta),dtype=np.int64);tags=list(bytag)
if len(ids)<100:raise SystemExit("Use at least 100 catalog items for bootstrap.")

examples=[]
for _ in range(a.sessions):
    pref=set(pr.sample(tags,k=min(len(tags),pr.randint(1,3))))
    seq=[]
    for step in range(pr.randint(8,40)):
        candidates=[]
        for t in pref:candidates+=bytag[t]
        if candidates and pr.random()<0.8:iid=pr.choice(candidates)
        else:iid=int(rng.choice(ids))
        overlap=len(pref&meta[iid])
        if overlap:
            action=pr.choices([2,3,5],[0.55,0.25,0.20])[0] # watch, like, click
            dur=pr.randint(10_000,180_000)
        else:
            action=2;dur=pr.randint(2_000,15_000)
        seq.append((iid,action,dur,step*15_000+pr.randint(0,5000)))
    for t in range(4,len(seq)-1):
        target=seq[t+1]
        if target[1] not in (3,5) and pr.random()<0.7:continue
        hist=seq[max(0,t-MAX_L+1):t+1]
        content=np.zeros((MAX_L,128),np.float32);act=np.zeros(MAX_L,np.int64);tb=np.zeros(MAX_L,np.int64);db=np.zeros(MAX_L,np.int64);mask=np.zeros(MAX_L,bool)
        off=MAX_L-len(hist);last=0
        for j,(iid,ac,dur,ts) in enumerate(hist,start=off):
            content[j]=emb[iid];act[j]=ac;tb[j]=bucket(ts-last if last else 0);db[j]=bucket(dur);mask[j]=True;last=ts
        neg_ids=rng.choice(ids,size=KNEG,replace=len(ids)<KNEG)
        examples.append((content,act,tb,db,mask,emb[target[0]],np.stack([emb[int(i)] for i in neg_ids])))
        if len(examples)>=200_000:break
    if len(examples)>=200_000:break

od=Path(a.output_dir);od.mkdir(parents=True,exist_ok=True)
for part,start in enumerate(range(0,len(examples),50000)):
    batch=examples[start:start+50000]
    np.savez_compressed(
      od/f"part-{part:04d}.npz",
      content=np.stack([x[0] for x in batch]).astype(np.float16),
      action=np.stack([x[1] for x in batch]),time_bucket=np.stack([x[2] for x in batch]),
      duration_bucket=np.stack([x[3] for x in batch]),mask=np.stack([x[4] for x in batch]),
      positive=np.stack([x[5] for x in batch]).astype(np.float16),
      negatives=np.stack([x[6] for x in batch]).astype(np.float16)
    )
print("bootstrap examples",len(examples))
