"""
Build a 5k-10k local subset of ShareGPT4Video.

Official project:
  https://sharegpt4video.github.io/
Official HF dataset:
  https://huggingface.co/datasets/ShareGPT4Video/ShareGPT4Video

Do NOT clone/download the full media repository on a 1-month, 3x3090 project.
Download the metadata first, choose a short-video subset, then download only the
referenced media files from the official dataset/repository.
"""
from __future__ import annotations
import argparse,json,random
from pathlib import Path
from common import write_jsonl

CAPTION_KEYS=("caption","video_caption","description","dense_caption")
VIDEO_KEYS=("video","video_path","path","video_name","video_id")

def pick(row,keys):
    for k in keys:
        v=row.get(k)
        if v not in (None,""):return v
    return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--metadata",required=True)
    ap.add_argument("--video-root",required=True)
    ap.add_argument("--output",required=True)
    ap.add_argument("--limit",type=int,default=8000)
    ap.add_argument("--seed",type=int,default=42)
    a=ap.parse_args()
    raw=json.loads(Path(a.metadata).read_text(encoding="utf-8")) if a.metadata.endswith(".json") else [json.loads(x) for x in Path(a.metadata).read_text(encoding="utf-8").splitlines() if x.strip()]
    if isinstance(raw,dict):
        for k in ("data","videos","annotations"):
            if isinstance(raw.get(k),list):raw=raw[k];break
        else: raw=list(raw.values())
    rnd=random.Random(a.seed);rnd.shuffle(raw)
    root=Path(a.video_root);rows=[]
    for r in raw:
        cap=pick(r,CAPTION_KEYS);vid=pick(r,VIDEO_KEYS)
        if isinstance(cap,list):
            chunks=[]
            for x in cap:
                if isinstance(x,dict): chunks.append(str(x.get("content") or x.get("text") or ""))
                else: chunks.append(str(x))
            cap=" ".join(x for x in chunks if x)
        if not cap or not vid:continue
        candidates=[root/str(vid)]
        stem=Path(str(vid)).stem
        candidates += [root/f"{stem}{ext}" for ext in (".mp4",".webm",".mkv")]
        p=next((x for x in candidates if x.exists()),None)
        if p is None:continue
        rows.append({
            "media":str(p.resolve()),"media_type":"video",
            "prompt":"请按时间顺序描述视频中发生的关键事件，强调对象变化、动作变化及前后关系。",
            "target":str(cap),"retrieval_text":str(cap),
            "meta":{"source":"ShareGPT4Video"}
        })
        if len(rows)>=a.limit:break
    write_jsonl(a.output,rows)
    print("ShareGPT4Video usable local subset:",len(rows))
if __name__=="__main__":main()
