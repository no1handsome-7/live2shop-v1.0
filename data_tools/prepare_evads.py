"""
Prepare ONLY E-VAds SFT/RL splits for training. Test is blocked by design.

Official:
  https://github.com/TaobaoTmall-AlgorithmProducts/E-VAds_Benchmark
HF media:
  https://huggingface.co/datasets/TaobaoTmall-AlgorithmProducts/E-VAds_Benchmark

Because upstream annotation field names can evolve, this converter accepts JSON
or JSONL and auto-detects common video/question/answer fields.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
from common import write_jsonl

VIDEO=("video","videos","video_path","video_name","video_id","filename")
Q=("question","query","instruction","prompt")
A=("answer","response","output","gt_answer","reward_model")

def first(r,ks):
    for k in ks:
        if k in r and r[k] not in (None,""):return r[k]

def load(path):
    if str(path).lower().endswith(".parquet"):
        import pandas as pd
        return pd.read_parquet(path).to_dict("records")
    txt=Path(path).read_text(encoding="utf-8")
    try:
        x=json.loads(txt)
        if isinstance(x,list):return x
        for k in ("data","annotations","samples"):
            if isinstance(x.get(k),list):return x[k]
        return list(x.values())
    except:
        return [json.loads(z) for z in txt.splitlines() if z.strip()]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--split",required=True,choices=["sft","rl"])
    ap.add_argument("--annotation",required=True)
    ap.add_argument("--video-root",required=True)
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    if "test" in a.annotation.lower():
        raise SystemExit("Refusing to prepare E-VAds TEST for training.")
    root=Path(a.video_root);rows=[]
    for r in load(a.annotation):
        vid,q,ans=first(r,VIDEO),first(r,Q),first(r,A)

        # RL prompt is stored as message list
        if hasattr(q, "tolist"):
            q = q.tolist()

        if isinstance(q, list) and len(q):
            if isinstance(q[0], dict):
                q = q[0].get("content", "")

        # reward_model stores answer
        if isinstance(ans, dict):
            ans = (
                ans.get("answer")
                or ans.get("response")
                or ans.get("ground_truth")
            )
            
        if not q or not ans:continue
        p=None
        if vid:
            cands=[root/str(vid),root/(Path(str(vid)).stem+".mp4")]
            p=next((x for x in cands if x.exists()),None)
        if p is None:continue
        rows.append({
            "media":str(p.resolve()),"media_type":"video",
            "prompt":str(q),"target":str(ans),"retrieval_text":str(ans),
            "meta":{"source":"E-VAds","official_split":a.split}
        })
    write_jsonl(a.output,rows)
    print("E-VAds",a.split,"rows:",len(rows))
if __name__=="__main__":main()
