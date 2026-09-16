"""
Validate teacher labels and produce:
- accepted.jsonl
- needs_review.jsonl
- review.csv (human-editable)

After manual review, put corrected JSON in `corrected_target_json` column and run:
  python data_tools/import_reviewed_labels.py ...
"""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path
from common import read_jsonl,write_jsonl

REQ={"product_visible","active_item_id","category","style_tags","attributes","evidence","confidence"}

def reasons(row):
    t=row.get("target_json")
    out=[]
    if not isinstance(t,dict):return ["parse_error"]
    miss=REQ-set(t)
    if miss:out.append("missing:"+",".join(sorted(miss)))
    c=t.get("confidence")
    if not isinstance(c,(int,float)) or not (0<=c<=1):out.append("bad_confidence")
    if c is not None and c<0.65:out.append("low_confidence")
    allowed={int(x["item_id"]) for x in row.get("context",{}).get("room_candidates",[]) if "item_id" in x}
    sku=t.get("active_item_id")
    if sku is not None and allowed and int(sku) not in allowed:out.append("sku_not_in_room_catalog")
    price=t.get("price_cent")
    if price is not None and (not isinstance(price,int) or price<0):out.append("bad_price")
    return out

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--out-dir",required=True);a=ap.parse_args()
    accepted=[];review=[];od=Path(a.out_dir);od.mkdir(parents=True,exist_ok=True)
    for row in read_jsonl(a.input):
        rs=reasons(row)
        if rs:review.append((row,rs))
        else:accepted.append(row)
    write_jsonl(od/"accepted.jsonl",accepted);write_jsonl(od/"needs_review.jsonl",[x[0] for x in review])
    with (od/"review.csv").open("w",newline="",encoding="utf-8-sig") as f:
        wr=csv.DictWriter(f,fieldnames=["media","reason","target_json","corrected_target_json"])
        wr.writeheader()
        for row,rs in review:
            wr.writerow({"media":row["media"],"reason":";".join(rs),
                         "target_json":json.dumps(row.get("target_json"),ensure_ascii=False),
                         "corrected_target_json":""})
    print("accepted",len(accepted),"review",len(review))
if __name__=="__main__":main()
