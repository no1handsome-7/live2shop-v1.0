"""
Encode your Live2Shop item catalog into `commerce-space-v1`.

Input JSONL:
  {
    "item_id": 300001,
    "title": "Levi's 70505 Vintage Denim Jacket",
    "brand": "Levi's",
    "style_tags": ["vintage","denim"],
    "image": "/absolute/path/item.jpg"
  }

Output Parquet:
  item_id: int64
  embedding: list<float>[128]

The embedding is the same 0.6 visual + 0.4 text fusion used by Content AI.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
import pandas as pd
import torch
import torch.nn.functional as F
from PIL import Image
from tqdm import tqdm

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"ml"/"commerce_vlm"))
from live2shop_commerce import CommerceVLM

ap=argparse.ArgumentParser()
ap.add_argument("--checkpoint",required=True)
ap.add_argument("--catalog-jsonl",required=True)
ap.add_argument("--output",required=True)
a=ap.parse_args()

device=torch.device("cuda" if torch.cuda.is_available() else "cpu")
model=CommerceVLM.from_checkpoint(a.checkpoint).to(device).eval()
rows=[]
with open(a.catalog_jsonl,encoding="utf-8") as f:
    source=[json.loads(x) for x in f if x.strip()]

with torch.inference_mode():
    for r in tqdm(source):
        image=Image.open(r["image"]).convert("RGB")
        proc=model.vision_processor(images=[image],return_tensors="pt")
        pv=proc["pixel_values"][:,None].to(device)
        _,ve=model.encode_visual(pv)
        text=" | ".join([
            str(r.get("title","")),str(r.get("brand","")),
            " ".join(map(str,r.get("style_tags") or [])),
            json.dumps(r.get("attributes") or {},ensure_ascii=False),
        ])
        tok=model.tokenizer([text],return_tensors="pt",truncation=True,max_length=192).to(device)
        te=model.encode_text_for_retrieval(tok.input_ids,tok.attention_mask)
        emb=F.normalize(0.6*ve+0.4*te,dim=-1)[0].float().cpu().tolist()
        rows.append({"item_id":int(r["item_id"]),"embedding":emb})
pd.DataFrame(rows).to_parquet(a.output,index=False)
print("encoded",len(rows),"items ->",a.output)
