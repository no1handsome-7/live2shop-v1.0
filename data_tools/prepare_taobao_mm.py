"""
Create RecModel NPZ shards from TAOBAO-MM pre-joined parquet.

Official:
  https://taobao-mm.github.io/
Download:
  huggingface-cli download --repo-type=dataset TaoBao-MM/Taobao-MM --local-dir data/raw/taobao-mm

For the 1-month project you do NOT need all 161 train shards initially.
Start with 4-16 shards; scale to 32+ if time/disk permit.

The official joined train shards contain user history and target-item multimodal
features. This script recognizes the official field names:
  150_2_180_c : history SCL embeddings, [L,128]
  205_c       : target SCL embedding, [128]
  label_0     : [1,0] non-click, [0,1] click
"""
from __future__ import annotations
import argparse,glob,math,random
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from tqdm import tqdm

MAX_L=64
KNEG=20

def norm(x):
    x=np.asarray(x,dtype=np.float32)
    n=np.linalg.norm(x,axis=-1,keepdims=True)
    return x/np.maximum(n,1e-6)

def click_label(v):
    if isinstance(v,np.ndarray):v=v.tolist()
    if isinstance(v,(list,tuple)) and len(v)>=2:return float(v[1] > v[0])
    return float(v)

def bucket(ms,b=32):
    if ms<=0:return 0
    return min(b-1,1+int(math.log1p(ms/1000)/math.log(1.7)))

def pad_history(seq):
    arr=np.asarray(seq,dtype=np.float32)
    if arr.ndim!=2 or arr.shape[1]!=128:return None
    arr=norm(arr[-MAX_L:])
    out=np.zeros((MAX_L,128),np.float32);mask=np.zeros(MAX_L,bool)
    out[-len(arr):]=arr;mask[-len(arr):]=True
    return out,mask

def find_col(cols,preferred,contains=None):
    for x in preferred:
        if x in cols:return x
    if contains:
        for x in cols:
            if contains in x:return x
    return None

def save_npz(path,rows,rng,pool):
    if not rows:return
    N=len(rows)
    content=np.stack([r["content"] for r in rows]).astype(np.float16)
    mask=np.stack([r["mask"] for r in rows])
    action=np.zeros((N,MAX_L),np.int64);action[mask]=2 # HEARTBEAT/history interaction
    timeb=np.zeros((N,MAX_L),np.int64)
    durb=np.zeros((N,MAX_L),np.int64)
    positive=np.stack([r["target"] for r in rows]).astype(np.float16)
    click=np.array([r["click"] for r in rows],np.float32)
    retrieval_mask=click.copy()
    negatives=np.empty((N,KNEG,128),np.float16)
    for i in range(N):
        idx=rng.integers(0,len(pool),size=KNEG)
        negatives[i]=pool[idx].astype(np.float16)
    business=np.zeros((N,4),np.float32)
    np.savez_compressed(path,content=content,action=action,time_bucket=timeb,
                        duration_bucket=durb,mask=mask,positive=positive,
                        negatives=negatives,retrieval_mask=retrieval_mask,
                        click_label=click,candidate=positive,business=business)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--train-glob",required=True,help="e.g. data/raw/taobao-mm/train/train-shard-0000{00..15}.parquet")
    ap.add_argument("--output-dir",required=True)
    ap.add_argument("--max-rows",type=int,default=2_000_000)
    ap.add_argument("--rows-per-output",type=int,default=100_000)
    ap.add_argument("--seed",type=int,default=42)
    a=ap.parse_args()
    paths=sorted(glob.glob(a.train_glob))
    if not paths:raise SystemExit("No TAOBAO-MM shards matched.")
    od=Path(a.output_dir);od.mkdir(parents=True,exist_ok=True)
    rng=np.random.default_rng(a.seed)

    # Build a negative pool from target embeddings while streaming shards.
    pool=[]
    rows=[];written=0;part=0
    for path in paths:
        pf=pq.ParquetFile(path)
        cols=pf.schema.names
        hist_col=find_col(cols,["150_2_180_c"],"180_c")
        target_col=find_col(cols,["205_c"])
        if not hist_col or not target_col:
            raise SystemExit(
              f"{path}: expected joined embedding columns 150_2_180_c and 205_c; "
              "download the pre-joined train shards, not only raw tables."
            )
        read_cols=[hist_col,target_col,"label_0"]
        for batch in pf.iter_batches(batch_size=4096,columns=read_cols):
            d=batch.to_pydict()
            for h,t,y in zip(d[hist_col],d[target_col],d["label_0"]):
                hp=pad_history(h)
                if hp is None:continue
                target=norm(np.asarray(t,dtype=np.float32)).reshape(-1)
                if target.size!=128:continue
                if len(pool)<100_000:
                    pool.append(target)
                elif rng.random()<0.005:
                    pool[int(rng.integers(0,len(pool)))]=target
                rows.append({"content":hp[0],"mask":hp[1],"target":target,"click":click_label(y)})
                written+=1
                if len(rows)>=a.rows_per_output and len(pool)>=5000:
                    save_npz(od/f"part-{part:04d}.npz",rows,rng,np.stack(pool))
                    print("saved",part,"total",written)
                    rows=[];part+=1
                if written>=a.max_rows:break
            if written>=a.max_rows:break
        if written>=a.max_rows:break
    if rows:
        save_npz(od/f"part-{part:04d}.npz",rows,rng,np.stack(pool))
    print("TAOBAO-MM examples",written,"negative pool",len(pool))

if __name__=="__main__":main()
