"""
Prepare Products-10K into CommerceVLM image instruction records.

Improvements:
- automatic CSV column detection
- safer defaults for official Products-10K layouts
- sanity checks before writing training data
"""
from __future__ import annotations
import argparse, csv
from pathlib import Path
from collections import Counter
from common import write_jsonl

EXT={".jpg",".jpeg",".png",".webp"}

def detect_column(fieldnames, candidates):
    lower={x.lower():x for x in fieldnames}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    for k,v in lower.items():
        for c in candidates:
            if c.lower() in k:
                return v
    return None

def from_tree(root):
    root=Path(root)
    for p in root.rglob("*"):
        if p.suffix.lower() in EXT:
            yield p,p.parent.name

def from_csv(root,csv_path,image_col=None,label_col=None):
    root=Path(root)
    with open(csv_path,encoding="utf-8-sig") as f:
        reader=csv.DictReader(f)
        cols=reader.fieldnames or []
        image_col=image_col or detect_column(
            cols, ["image","name","file","filename","path"]
        )
        label_col=label_col or detect_column(
            cols, ["class","label","category","sku","product_id"]
        )
        if not image_col or not label_col:
            raise ValueError(
                f"Cannot detect columns. Available columns={cols}"
            )
        print(f"[columns] image={image_col} label={label_col}")
        for r in reader:
            p=root/r[image_col]
            if p.exists():
                yield p,str(r[label_col])

def sanity_check(rows):
    if not rows:
        raise RuntimeError("No Products-10K samples generated")

    missing=[x for x in rows if not Path(x["media"]).exists()]
    if missing:
        raise RuntimeError(
            f"Missing media files: {len(missing)}"
        )

    labels=Counter(x["meta"]["sku_label"] for x in rows)
    print(
        "sanity:",
        "samples=",len(rows),
        "classes=",len(labels),
        "top_classes=",labels.most_common(5)
    )

    if len(labels)<10:
        raise RuntimeError(
            "Suspicious dataset: fewer than 10 classes detected"
        )

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",required=True)
    ap.add_argument("--annotation-csv")
    ap.add_argument("--image-col")
    ap.add_argument("--label-col")
    ap.add_argument("--output",required=True)
    args=ap.parse_args()

    source = (
        from_csv(args.root,args.annotation_csv,args.image_col,args.label_col)
        if args.annotation_csv
        else from_tree(args.root)
    )

    rows=[]
    for p,label in source:
        text=f"SKU类别 {label}"
        rows.append({
            "media":str(Path(p).resolve()),
            "media_type":"image",
            "prompt":"识别图片中的细粒度商品身份或SKU类别。",
            "target":text,
            "retrieval_text":text,
            "meta":{
                "source":"Products-10K",
                "sku_label":label
            }
        })

    sanity_check(rows)
    write_jsonl(args.output,rows)
    print("Products-10K rows:",len(rows))

if __name__=="__main__":
    main()
