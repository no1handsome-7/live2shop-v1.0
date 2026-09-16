"""
Turn owned/licensed e-commerce or live videos into 5-second weak-supervision windows.

Input JSONL:
  {"video": "/path/live.mp4", "room_id": "demo001", "catalog": "/path/catalog.json"}

Output JSONL:
  {
    "media": ".../room/window_000123.mp4",
    "media_type":"video",
    "room_id":"...",
    "start_ms":..., "end_ms":...,
    "asr":"...", "ocr":[...],
    "room_candidates":[...]
  }

Use only videos you are permitted to process. For E-VAds, use only its SFT/RL
training split media, never the benchmark test split for training.
"""
from __future__ import annotations
import argparse,json,subprocess,tempfile
from pathlib import Path
from collections import defaultdict
import numpy as np
import cv2
from PIL import Image
from tqdm import tqdm

from common import read_jsonl, write_jsonl

def duration_ms(path):
    x=subprocess.check_output([
        "ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",str(path)
    ],text=True).strip()
    return int(float(x)*1000)

def split_one(video,outdir,window_ms):
    outdir.mkdir(parents=True,exist_ok=True)
    dur=duration_ms(video); result=[]
    for start in range(0,dur,window_ms):
        end=min(dur,start+window_ms)
        if end-start<1500:continue
        out=outdir/f"window_{start:010d}_{end:010d}.mp4"
        if not out.exists():
            subprocess.run([
                "ffmpeg","-hide_banner","-loglevel","error",
                "-ss",f"{start/1000:.3f}","-i",str(video),
                "-t",f"{(end-start)/1000:.3f}",
                "-c:v","libx264","-preset","veryfast","-crf","23",
                "-c:a","aac","-y",str(out)
            ],check=True)
        result.append((start,end,out))
    return result

def sample_frames(path,n=4):
    cap=cv2.VideoCapture(str(path)); count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    idxs=np.linspace(0,max(count-1,0),n).astype(int);frames=[]
    for idx in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES,int(idx));ok,f=cap.read()
        if ok:frames.append(cv2.cvtColor(f,cv2.COLOR_BGR2RGB))
    cap.release();return frames

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",required=True)
    ap.add_argument("--output-root",required=True)
    ap.add_argument("--output-jsonl",required=True)
    ap.add_argument("--window-ms",type=int,default=5000)
    ap.add_argument("--whisper-model",default="small")
    ap.add_argument("--skip-ocr",action="store_true")
    ap.add_argument("--skip-asr",action="store_true")
    a=ap.parse_args()

    asr_model=None
    if not a.skip_asr:
        from faster_whisper import WhisperModel
        import torch
        dev="cuda" if torch.cuda.is_available() else "cpu"
        asr_model=WhisperModel(a.whisper_model,device=dev,compute_type="float16" if dev=="cuda" else "int8")

    ocr_model=None
    if not a.skip_ocr:
        from rapidocr import RapidOCR
        ocr_model=RapidOCR(params={"Rec.lang_type":"ch","Global.text_score":0.55})

    rows=[]
    outroot=Path(a.output_root)
    for src in read_jsonl(a.manifest):
        video=Path(src["video"])
        room=str(src.get("room_id",video.stem))
        candidates=[]
        if src.get("catalog"):
            candidates=json.loads(Path(src["catalog"]).read_text(encoding="utf-8"))
        windows=split_one(video,outroot/room,a.window_ms)
        for start,end,path in tqdm(windows,desc=room):
            asr=""
            if asr_model:
                segs,_=asr_model.transcribe(str(path),language="zh",vad_filter=True)
                asr=" ".join(x.text.strip() for x in segs).strip()
            ocr=[]
            if ocr_model:
                for frame in sample_frames(path):
                    result=ocr_model(frame)
                    if result is None:continue
                    txts=getattr(result,"txts",None) or []
                    scores=getattr(result,"scores",None) or [1.0]*len(txts)
                    for txt,score in zip(txts,scores):
                        if float(score)>=0.55 and str(txt).strip():ocr.append(str(txt).strip())
                ocr=list(dict.fromkeys(ocr))[:40]
            rows.append({
                "media":str(path.resolve()),"media_type":"video",
                "room_id":room,"start_ms":start,"end_ms":end,
                "asr":asr,"ocr":ocr,"room_candidates":candidates,
                "source":src.get("source","owned_or_licensed")
            })
    write_jsonl(a.output_jsonl,rows)
    print("windows:",len(rows))

if __name__=="__main__":main()
