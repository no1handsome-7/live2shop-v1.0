"""
Create manually verified CommerceEvent gold labels.

Safety additions:
- verify source video split consistency
- refuse same source video appearing in train/val/test
"""
import argparse,csv,json
from pathlib import Path
from common import write_jsonl
from product_pages.schema import build_student_prompt, empty_commerce_event

ap=argparse.ArgumentParser()
ap.add_argument("--csv",required=True)
ap.add_argument("--window-root",required=True)
ap.add_argument("--output",required=True)
a=ap.parse_args()

rows=[]
video_splits={}

with open(a.csv,encoding="utf-8-sig") as f:
    reader=csv.DictReader(f)

    for r in reader:
        video=r.get("video")
        split=r.get("split")

        if video and split:
            old=video_splits.get(video)
            if old and old != split:
                raise ValueError(
                    f"Leakage: video {video} exists in {old} and {split}"
                )
            video_splits[video]=split

        start,end=int(r["start_ms"]),int(r["end_ms"])
        room=r["room_id"]

        media=(
            Path(a.window_root)/room/
            f"window_{start:010d}_{end:010d}.mp4"
        )

        target={
            **empty_commerce_event(),
            "product_visible":bool(r.get("item_id")),
            "category":r.get("category",""),
            "brand":r.get("brand",""),
            "style_tags":[x for x in r.get("style_tags","").split("|") if x],
            "color":r.get("color",""),
            "attributes":{},
            "price_cent":int(r["price_cent"]) if r.get("price_cent") else None,
            "promotion_tags":[x for x in r.get("promotion_tags","").split("|") if x],
            "evidence":{"asr":[],"ocr":[],"visual":[]},
        }

        rows.append({
            "media":str(media.resolve()),
            "media_type":"video",
            "prompt":build_student_prompt("",[]),
            "target":json.dumps(
                target,
                ensure_ascii=False,
                sort_keys=True
            ),
            "target_json":target,
            "retrieval_text":json.dumps(
                target,
                ensure_ascii=False,
                sort_keys=True
            ),
            "meta":{
                "source":"manual_gold",
                "human_reviewed":True,
                "audit_status":"human_reviewed",
                "commerce_event_schema":"commerce_event.v2",
                "source_product_id":r.get("item_id") or None,
                "video_split":split
            }
        })

write_jsonl(a.output,rows)
print("gold",len(rows))
