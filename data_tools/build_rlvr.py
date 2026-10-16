import argparse,random
from common import read_jsonl,write_jsonl
ap=argparse.ArgumentParser();ap.add_argument("--input",required=True);ap.add_argument("--output",required=True);ap.add_argument("--limit",type=int,default=1000);ap.add_argument("--seed",type=int,default=42);a=ap.parse_args()
rows=[r for r in read_jsonl(a.input) if isinstance(r.get("target_json"),dict)]
random.Random(a.seed).shuffle(rows)
# Prefer human-reviewed rows; confidence is annotation metadata and is stripped
# from CommerceEvent v2 targets by design.
rows.sort(key=lambda r:not bool(r.get("meta",{}).get("human_reviewed")))
write_jsonl(a.output,rows[:a.limit]);print("RLVR prompts",min(a.limit,len(rows)))
