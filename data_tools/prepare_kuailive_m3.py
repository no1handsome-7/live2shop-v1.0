"""
Create temporal SessionRec NPZ examples from KuaiLive-M3.

Official code:
  https://github.com/imgkkk574/KuaiLive-M3
Dataset:
  https://huggingface.co/datasets/imgkkk2004/KuaiLive-M3

Uses:
  live_interaction.csv
  live_like.csv (optional but recommended)
  live_emb_128_ts/live_emb_128_ts_part*.parquet

For a workstation run, sample 500-3000 users and scan 2-18 embedding shards.
This keeps the true timestamped segment embeddings while controlling memory.
"""
from __future__ import annotations
import argparse,glob,math,random
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from tqdm import tqdm

MAX_L=64
KNEG=20

def to_ms(x):
    if pd.isna(x):return 0
    if isinstance(x,pd.Timestamp):return int(x.timestamp()*1000)
    try:
        v=int(x)
        # seconds / ms / us/ns rough normalization
        if v<10_000_000_000:return v*1000
        if v>10_000_000_000_000_000:return v//1_000_000
        if v>10_000_000_000_000:return v//1000
        return v
    except:
        return int(pd.Timestamp(x).timestamp()*1000)

def norm(x):
    x=np.asarray(x,np.float32);n=np.linalg.norm(x)
    return x/max(n,1e-6)

def bucket(ms,b=32):
    if ms<=0:return 0
    return min(b-1,1+int(math.log1p(ms/1000)/math.log(1.7)))

def sample_users(csv_path,n,seed):
    # user_id_set.csv is tiny (~22K) if available; otherwise reservoir from live interactions.
    df=pd.read_csv(csv_path,usecols=["user_id"])
    users=df["user_id"].drop_duplicates().to_numpy()
    rng=np.random.default_rng(seed)
    if len(users)>n:users=rng.choice(users,size=n,replace=False)
    return set(map(int,users))

def read_interactions(path,users,max_per_user=200):
    parts=[]
    usecols=[
      "user_id","live_id","live_play_start_timestamp","live_play_end_timestamp",
      "play_duration","like_cnt","comment_cnt","follow_author_cnt","share_cnt"
    ]
    for chunk in pd.read_csv(path,usecols=lambda c:c in usecols,chunksize=1_000_000):
        hit=chunk[chunk["user_id"].isin(users)]
        if len(hit):parts.append(hit)
    if not parts:return pd.DataFrame()
    x=pd.concat(parts,ignore_index=True)
    x["_ts"]=x["live_play_start_timestamp"].map(to_ms)
    x=x.sort_values(["user_id","_ts"]).groupby("user_id",group_keys=False).tail(max_per_user)
    return x

def read_likes(path,users):
    if not path or not Path(path).exists():return pd.DataFrame()
    parts=[]
    for chunk in pd.read_csv(path,usecols=["user_id","live_id","like_timestamp"],chunksize=1_000_000):
        hit=chunk[chunk["user_id"].isin(users)]
        if len(hit):parts.append(hit)
    if not parts:return pd.DataFrame()
    x=pd.concat(parts,ignore_index=True);x["_ts"]=x["like_timestamp"].map(to_ms);return x

def load_segments(shard_glob,live_ids,max_shards=0):
    paths=sorted(glob.glob(shard_glob))
    if max_shards:paths=paths[:max_shards]
    if not paths:raise SystemExit("No live_emb_128_ts shards found.")
    wanted={str(x) for x in live_ids};by_live=defaultdict(list);pool=[]
    for p in tqdm(paths,desc="segment shards"):
        frame=pd.read_parquet(p,columns=["live_stream_id","feature_128","timestamp"])
        hit=frame[frame["live_stream_id"].astype(str).isin(wanted)]
        for live_id,feat,ts in zip(hit["live_stream_id"],hit["feature_128"],hit["timestamp"]):
            e=norm(feat)
            by_live[str(live_id)].append((to_ms(ts),e))
            if len(pool)<200_000:pool.append(e)
        del frame,hit
    for k in by_live:by_live[k].sort(key=lambda x:x[0])
    return by_live,np.asarray(pool,np.float32)

def nearest(seq,ts):
    if not seq:return None
    times=np.fromiter((x[0] for x in seq),dtype=np.int64)
    i=int(np.searchsorted(times,ts))
    cand=[]
    if i<len(seq):cand.append(seq[i])
    if i>0:cand.append(seq[i-1])
    return min(cand,key=lambda x:abs(x[0]-ts))[1] if cand else None

def build_examples(events,pool,out_dir,seed):
    rng=np.random.default_rng(seed);out=[];part=0;od=Path(out_dir);od.mkdir(parents=True,exist_ok=True)
    def dump():
        nonlocal out,part
        if not out:return
        N=len(out)
        content=np.stack([x["content"] for x in out]).astype(np.float16)
        action=np.stack([x["action"] for x in out])
        timeb=np.stack([x["time"] for x in out])
        dur=np.stack([x["dur"] for x in out])
        mask=np.stack([x["mask"] for x in out])
        positive=np.stack([x["positive"] for x in out]).astype(np.float16)
        idx=rng.integers(0,len(pool),size=(N,KNEG))
        negatives=pool[idx].astype(np.float16)
        np.savez_compressed(od/f"part-{part:04d}.npz",content=content,action=action,time_bucket=timeb,
          duration_bucket=dur,mask=mask,positive=positive,negatives=negatives)
        print("saved",part,N);out=[];part+=1

    for uid,seq in events.items():
        seq=sorted(seq,key=lambda x:x["ts"])
        history=[]
        prev_ts=None
        for ev in seq:
            if history:
                hist=history[-MAX_L:]
                c=np.zeros((MAX_L,128),np.float32);a=np.zeros(MAX_L,np.int64);tb=np.zeros(MAX_L,np.int64);db=np.zeros(MAX_L,np.int64);m=np.zeros(MAX_L,bool)
                off=MAX_L-len(hist)
                last=None
                for j,h in enumerate(hist,start=off):
                    c[j]=h["emb"];a[j]=h["action"];tb[j]=bucket(0 if last is None else h["ts"]-last);db[j]=bucket(h["dur"]);m[j]=True;last=h["ts"]
                out.append({"content":c,"action":a,"time":tb,"dur":db,"mask":m,"positive":ev["emb"]})
                if len(out)>=50_000:dump()
            history.append(ev)
    dump()

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-dir",required=True)
    ap.add_argument("--output-dir",required=True)
    ap.add_argument("--users",type=int,default=1500)
    ap.add_argument("--max-segment-shards",type=int,default=6)
    ap.add_argument("--seed",type=int,default=42)
    a=ap.parse_args()
    root=Path(a.data_dir)
    users=sample_users(root/"user_id_set.csv",a.users,a.seed)
    inter=read_interactions(root/"live_interaction.csv",users)
    likes=read_likes(root/"live_like.csv",users)
    live_ids=set(map(str,inter["live_id"].unique()))
    if len(likes):live_ids.update(map(str,likes["live_id"].unique()))
    print("users",len(users),"interactions",len(inter),"live_ids",len(live_ids))
    by_live,pool=load_segments(str(root/"live_emb_128_ts"/"live_emb_128_ts_part*.parquet"),live_ids,a.max_segment_shards)
    if len(pool)<1000:raise SystemExit("Too few matching segment embeddings; increase --max-segment-shards or users.")
    events=defaultdict(list)
    for _,r in inter.iterrows():
        live=str(r["live_id"]);start=int(r["_ts"]);dur=max(0,int(float(r.get("play_duration",0) or 0)))
        ts=start+min(dur//2,15_000)
        emb=nearest(by_live.get(live,[]),ts)
        if emb is None:continue
        events[int(r["user_id"])].append({"ts":ts,"emb":emb,"action":2,"dur":dur}) # heartbeat/watch
        if float(r.get("follow_author_cnt",0) or 0)>0:
            events[int(r["user_id"])].append({"ts":ts+1,"emb":emb,"action":4,"dur":0})
    for _,r in likes.iterrows():
        live=str(r["live_id"]);ts=int(r["_ts"]);emb=nearest(by_live.get(live,[]),ts)
        if emb is not None:events[int(r["user_id"])].append({"ts":ts,"emb":emb,"action":3,"dur":0})
    build_examples(events,pool,a.output_dir,a.seed)

if __name__=="__main__":main()
