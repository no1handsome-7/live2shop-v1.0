import argparse,csv,json
from common import read_jsonl,write_jsonl
ap=argparse.ArgumentParser();ap.add_argument("--review-csv",required=True);ap.add_argument("--source-jsonl",required=True);ap.add_argument("--output",required=True);a=ap.parse_args()
source={r["media"]:r for r in read_jsonl(a.source_jsonl)};out=[]
with open(a.review_csv,encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        if not r["corrected_target_json"].strip():continue
        base=source[r["media"]];obj=json.loads(r["corrected_target_json"])
        if not isinstance(obj,dict) or obj.get("schema_version")!="commerce_event.v2":
            raise ValueError(f"corrected target must be commerce_event.v2: {r['media']}")
        forbidden={"active_item_id","confidence","page_price_cent","source_product_id"}&set(obj)
        if forbidden:raise ValueError(f"forbidden target fields {sorted(forbidden)}: {r['media']}")
        base["target_json"]=obj;base["target"]=json.dumps(obj,ensure_ascii=False,sort_keys=True);base["retrieval_text"]=base["target"]
        base.setdefault("meta",{})["human_reviewed"]=True
        base["meta"]["audit_status"]="human_reviewed";base["meta"]["audit_reasons"]=[];out.append(base)
write_jsonl(a.output,out);print("reviewed",len(out))
