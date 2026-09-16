"""
Small held-out image/text retrieval evaluation for P0/P1 checkpoints.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np,torch
from tqdm.auto import tqdm
from live2shop_commerce import CommerceVLM
from live2shop_commerce.data import load_frames

ap=argparse.ArgumentParser()
ap.add_argument("--checkpoint",required=True);ap.add_argument("--jsonl",required=True);ap.add_argument("--limit",type=int,default=1000);a=ap.parse_args()
device=torch.device("cuda" if torch.cuda.is_available() else "cpu")
m=CommerceVLM.from_checkpoint(a.checkpoint).to(device).eval()
rows=[json.loads(x) for x in Path(a.jsonl).read_text(encoding="utf-8").splitlines() if x.strip()][:a.limit]
ve=[];te=[]
with torch.inference_mode():
  for r in tqdm(rows):
    frames=load_frames(r["media"],r.get("media_type","image"),m.cfg.max_frames)
    proc=m.vision_processor(images=frames,return_tensors="pt")
    pv=proc["pixel_values"].reshape(1,len(frames),*proc["pixel_values"].shape[1:]).to(device)
    _,v=m.encode_visual(pv);ve.append(v[0].float().cpu())
    tok=m.tokenizer([r.get("retrieval_text") or r["target"]],return_tensors="pt",truncation=True,max_length=128).to(device)
    t=m.encode_text_for_retrieval(tok.input_ids,tok.attention_mask);te.append(t[0].float().cpu())
V=torch.stack(ve);T=torch.stack(te);sim=V@T.T
for k in (1,5,10):
  top=sim.topk(min(k,len(rows)),dim=1).indices
  correct=torch.arange(len(rows))[:,None]
  print(f"image->text R@{k}",float((top==correct).any(dim=1).float().mean()))
  top2=sim.T.topk(min(k,len(rows)),dim=1).indices
  print(f"text->image R@{k}",float((top2==correct).any(dim=1).float().mean()))
