"""
Prepare Products-10K after downloading it under the official challenge terms.

Official project:
  https://products-10k.github.io/

The script supports the common layout where each image's parent directory or
annotation CSV identifies a SKU/class label.
"""
from __future__ import annotations
import argparse,csv
from pathlib import Path
from common import write_jsonl

EXT={".jpg",".jpeg",".png",".webp"}

def from_tree(root):
    root=Path(root)
    for p in root.rglob("*"):
        if p.suffix.lower() not in EXT: continue
        label=p.parent.name
        yield p,label

def from_csv(root,csv_path,image_col,label_col):
    root=Path(root)
    with open(csv_path,encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            p=root/r[image_col]
            if p.exists(): yield p,str(r[label_col])

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True)
    ap.add_argument("--annotation-csv")
    ap.add_argument("--image-col",default="image")
    ap.add_argument("--label-col",default="class")
    ap.add_argument("--output",required=True)
    args=ap.parse_args()
    source=from_csv(args.root,args.annotation_csv,args.image_col,args.label_col) if args.annotation_csv else from_tree(args.root)
    rows=[]
    for p,label in source:
        text=f"SKU类别 {label}"
        rows.append({
            "media":str(p.resolve()),"media_type":"image",
            "prompt":"识别图片中的细粒度商品身份或SKU类别；对于高度相似商品，优先依据包装、容量、颜色和型号。",
            "target":text,"retrieval_text":text,
            "meta":{"source":"Products-10K","sku_label":label}
        })
    write_jsonl(args.output,rows)
    print("Products-10K rows:",len(rows))
if __name__=="__main__":main()
