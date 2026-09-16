"""
Prepare MUGE e-commerce image/text data for CommerceVLM P0.

Preferred source:
  https://tianchi.aliyun.com/muge
Convenient public mirror:
  https://modelscope.cn/datasets/wangxingjun778/muge_test

This script supports two modes:
1) --input-jsonl <local metadata> for a manually downloaded official copy.
2) --modelscope-id <dataset id> for a ModelScope-accessible mirror.

Expected source records should expose an image/file path plus query/caption/text.
Column names are auto-detected from common variants.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

from common import write_jsonl

IMAGE_KEYS=("image","image_path","img","img_path","file","path")
TEXT_KEYS=("query","text","caption","title","sentence")

def first(row, keys):
    for k in keys:
        if k in row and row[k] not in (None,""):
            return row[k]
    return None

def convert(rows, out, root=None, limit=0, image_out_dir=None):
    result=[]
    image_out = Path(image_out_dir) if image_out_dir else None
    if image_out: image_out.mkdir(parents=True, exist_ok=True)
    for idx,row in enumerate(rows):
        img=first(row,IMAGE_KEYS); text=first(row,TEXT_KEYS)
        if img is None or text is None: continue
        if hasattr(img, "save") and hasattr(img, "convert"):
            if image_out is None:
                raise ValueError("ModelScope/PIL image records require --image-out-dir")
            image_id = row.get("image_id", idx)
            p = image_out / f"{image_id}.jpg"
            if not p.exists():
                img.convert("RGB").save(p, quality=92)
        else:
            if isinstance(img,dict):
                img=img.get("path") or img.get("image_path")
            if not img: continue
            p=Path(str(img))
            if root and not p.is_absolute(): p=Path(root)/p
            if not p.exists(): continue
        text=str(text).strip()
        if not text: continue
        result.append({
            "media":str(p.resolve()),
            "media_type":"image",
            "prompt":"请根据商品图片生成准确、简洁的中文商品语义描述，不要编造图中无法确认的属性。",
            "target":text,
            "retrieval_text":text,
            "meta":{"source":"MUGE"}
        })
        if limit and len(result)>=limit: break
    write_jsonl(out,result)
    print("MUGE rows:",len(result),"->",out)

def main():
    ap=argparse.ArgumentParser()
    g=ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--input-jsonl")
    g.add_argument("--modelscope-id")
    ap.add_argument("--root")
    ap.add_argument("--output",required=True)
    ap.add_argument("--image-out-dir")
    ap.add_argument("--limit",type=int,default=0)
    args=ap.parse_args()

    if args.input_jsonl:
        rows=(json.loads(x) for x in Path(args.input_jsonl).read_text(encoding="utf-8").splitlines() if x.strip())
        convert(rows,args.output,args.root,args.limit,args.image_out_dir)
    else:
        from modelscope.msdatasets import MsDataset
        ds=MsDataset.load(args.modelscope_id,split="train")
        convert(ds,args.output,args.root,args.limit,args.image_out_dir)

if __name__=="__main__": main()
