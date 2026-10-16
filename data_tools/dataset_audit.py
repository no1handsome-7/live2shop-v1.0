#!/usr/bin/env python3
"""CommerceVLM dataset quality audit."""
import argparse,json,math
from pathlib import Path
from collections import Counter

ap=argparse.ArgumentParser()
ap.add_argument("--jsonl",required=True)
ap.add_argument("--max-length",type=int,default=768)
ap.add_argument("--output")
a=ap.parse_args()

rows=[json.loads(x) for x in Path(a.jsonl).read_text(encoding="utf8").splitlines() if x.strip()]

lengths=[]
blank=0
images=[]
catalog=0

for r in rows:
    text=(r.get("prompt","")+" "+r.get("target","")).strip()
    lengths.append(len(text.split()))
    if not bool(r.get("target_json",{}).get("product_visible")):
        blank+=1
    if r.get("media"):
        images.append(r["media"])
    c=r.get("meta",{}).get("catalog_candidates")
    if c:
        catalog+=1

dup=1-len(set(images))/max(len(images),1)

def pct(v):
    return round(v*100,2)

report={
 "samples":len(rows),
 "avg_tokens":round(sum(lengths)/max(len(lengths),1),2),
 "p95_tokens":sorted(lengths)[int(len(lengths)*0.95)] if lengths else 0,
 "blank_ratio":pct(blank/max(len(rows),1)),
 "duplicate_image_ratio":pct(dup),
 "catalog_coverage":pct(catalog/max(len(rows),1)),
 "over_max_length_ratio":pct(sum(x>=a.max_length for x in lengths)/max(len(rows),1))
}

print(json.dumps(report,indent=2,ensure_ascii=False))
if a.output:
    Path(a.output).write_text(json.dumps(report,indent=2),encoding="utf8")
