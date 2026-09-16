from __future__ import annotations

import argparse,json,time
from pathlib import Path
from collections import defaultdict

import torch
from tqdm.auto import tqdm

from live2shop_commerce import CommerceVLM
from live2shop_commerce.data import load_frames
from live2shop_commerce.reward import parse_json_loose

def eq(a,b):
    if a is None and b is None:return True
    return str(a).strip().lower()==str(b).strip().lower()

def f1_set(a,b):
    a=set(map(str,a or []));b=set(map(str,b or []))
    if not a and not b:return 1.0
    if not a or not b:return 0.0
    p=len(a&b)/len(a);r=len(a&b)/len(b)
    return 2*p*r/(p+r) if p+r else 0.0

ap=argparse.ArgumentParser()
ap.add_argument("--checkpoint",required=True)
ap.add_argument("--jsonl",required=True)
ap.add_argument("--limit",type=int,default=0)
a=ap.parse_args()

device=torch.device("cuda" if torch.cuda.is_available() else "cpu")
model=CommerceVLM.from_checkpoint(a.checkpoint).to(device).eval()
rows=[json.loads(x) for x in Path(a.jsonl).read_text(encoding="utf-8").splitlines() if x.strip()]
if a.limit:rows=rows[:a.limit]

stats=defaultdict(float);lat=[]
for row in tqdm(rows):
    frames=load_frames(row["media"],row.get("media_type","video"),model.cfg.max_frames)
    proc=model.vision_processor(images=frames,return_tensors="pt")
    pv=proc["pixel_values"].reshape(1,len(frames),*proc["pixel_values"].shape[1:]).to(device)
    t=time.perf_counter()
    out=model.generate_json(pv,row["prompt"],max_new_tokens=256,temperature=0)
    lat.append((time.perf_counter()-t)*1000)
    pred=parse_json_loose(out);gt=row.get("target_json") or json.loads(row["target"])
    stats["n"]+=1
    if pred is None:
        continue
    stats["json_valid"]+=1
    for k in ("active_item_id","category","brand","color","price_cent"):
        if gt.get(k) not in (None,"",[]):
            stats[k+"_den"]+=1
            stats[k+"_num"]+=float(eq(pred.get(k),gt.get(k)))
    stats["style_f1"]+=f1_set(pred.get("style_tags"),gt.get("style_tags"))

n=max(stats["n"],1)
print("N",int(n))
print("JSON valid",stats["json_valid"]/n)
for k in ("active_item_id","category","brand","color","price_cent"):
    den=stats[k+"_den"]
    if den:print(k,stats[k+"_num"]/den)
print("style_f1",stats["style_f1"]/n)
if lat:
    xs=sorted(lat)
    print("latency_ms_p50",xs[len(xs)//2])
    print("latency_ms_p95",xs[min(len(xs)-1,int(len(xs)*.95))])
