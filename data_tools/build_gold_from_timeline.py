"""
Create manually verified CommerceEvent gold labels from a CSV timeline.

CSV columns:
video,room_id,start_ms,end_ms,item_id,category,brand,style_tags,color,price_cent,promotion_tags

`style_tags` and `promotion_tags` use `|` separators.
This is the recommended way to build a clean 500-1000 window validation/test set
for your OWN demo streams.
"""
import argparse,csv,json
from pathlib import Path
from common import write_jsonl
ap=argparse.ArgumentParser();ap.add_argument("--csv",required=True);ap.add_argument("--window-root",required=True);ap.add_argument("--output",required=True);a=ap.parse_args()
rows=[]
with open(a.csv,encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        start,end=int(r["start_ms"]),int(r["end_ms"]);room=r["room_id"]
        # Matches build_commerce_windows output naming.
        media=Path(a.window_root)/room/f"window_{start:010d}_{end:010d}.mp4"
        target={"product_visible":bool(r.get("item_id")),"active_item_id":int(r["item_id"]) if r.get("item_id") else None,
                "category":r.get("category",""),"brand":r.get("brand",""),
                "style_tags":[x for x in r.get("style_tags","").split("|") if x],
                "color":r.get("color",""),"attributes":{},
                "price_cent":int(r["price_cent"]) if r.get("price_cent") else None,
                "promotion_tags":[x for x in r.get("promotion_tags","").split("|") if x],
                "evidence":{},"confidence":1.0}
        rows.append({"media":str(media.resolve()),"media_type":"video",
                     "prompt":"输出当前直播窗口的CommerceEvent JSON。","target":json.dumps(target,ensure_ascii=False,sort_keys=True),
                     "target_json":target,"retrieval_text":json.dumps(target,ensure_ascii=False,sort_keys=True),
                     "meta":{"source":"manual_gold","human_reviewed":True}})
write_jsonl(a.output,rows);print("gold",len(rows))
