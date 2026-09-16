import argparse,random
from common import read_jsonl,write_jsonl
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--output",required=True);ap.add_argument("--limit",type=int,default=1000);ap.add_argument("--seed",type=int,default=42);a=ap.parse_args()
rows=[r for r in read_jsonl(a.input) if isinstance(r.get("target_json"),dict)]
random.Random(a.seed).shuffle(rows)
# Prefer human reviewed or high confidence samples.
rows.sort(key=lambda r:(not bool(r.get("meta",{}).get("human_reviewed")), -float(r.get("target_json",{}).get("confidence",0))))
write_jsonl(a.output,rows[:a.limit]);print("RLVR prompts",min(a.limit,len(rows)))
