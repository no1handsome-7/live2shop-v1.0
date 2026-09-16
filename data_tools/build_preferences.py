"""
Create SKU/price/style/evidence hard negatives from audited CommerceEvent SFT data.
"""
from __future__ import annotations
import argparse,copy,json,random
from common import read_jsonl,write_jsonl

COLORS=["黑色","白色","蓝色","红色","绿色","棕色"]
STYLES=["古着","美式复古","工装","街头","运动","通勤"]

def corrupt(row,rng):
    good=copy.deepcopy(row["target_json"]);bad=copy.deepcopy(good)
    modes=[]
    candidates=row.get("context",{}).get("room_candidates") or []
    if len(candidates)>=2:modes.append("sku")
    if isinstance(good.get("price_cent"),int):modes.append("price")
    if good.get("color"):modes.append("color")
    modes+=["style","promotion","evidence"]
    mode=rng.choice(modes)
    if mode=="sku":
        cur=good.get("active_item_id");alts=[x["item_id"] for x in candidates if x.get("item_id")!=cur]
        bad["active_item_id"]=int(rng.choice(alts))
    elif mode=="price":
        p=int(good["price_cent"]);bad["price_cent"]=max(100,p+rng.choice([-5000,-2000,2000,5000]))
    elif mode=="color":
        bad["color"]=rng.choice([x for x in COLORS if x!=good.get("color")])
    elif mode=="style":
        st=list(good.get("style_tags") or []);st.append(rng.choice(STYLES));bad["style_tags"]=list(dict.fromkeys(st))
    elif mode=="promotion":
        bad["promotion_tags"]=list(good.get("promotion_tags") or [])+["无依据限时五折"]
    else:
        bad["evidence"]={"asr":["输入中未出现的证据"],"ocr":[],"visual":[]}
    return bad,mode

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--output",required=True);ap.add_argument("--negatives-per-sample",type=int,default=2);ap.add_argument("--seed",type=int,default=42);a=ap.parse_args()
    rng=random.Random(a.seed);out=[]
    for row in read_jsonl(a.input):
        if not isinstance(row.get("target_json"),dict):continue
        for _ in range(a.negatives_per_sample):
            bad,mode=corrupt(row,rng)
            out.append({"media":row["media"],"media_type":row.get("media_type","video"),"prompt":row["prompt"],
                        "chosen":json.dumps(row["target_json"],ensure_ascii=False,sort_keys=True),
                        "rejected":json.dumps(bad,ensure_ascii=False,sort_keys=True),
                        "meta":{"negative_type":mode}})
    write_jsonl(a.output,out);print("preference pairs",len(out))
if __name__=="__main__":main()
