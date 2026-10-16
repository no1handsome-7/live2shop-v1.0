"""
Turn authorized e-commerce/live videos into 5-second weak-supervision windows.

Input JSONL:
  {"video": "/path/live.mp4", "room_id": "demo001", "catalog": "/path/catalog.json"}
or product-page manifest rows emitted by ``build_product_page_manifest.py``.

Output JSONL:
  {
    "media": ".../room/window_000123.mp4",
    "media_type":"video",
    "room_id":"...",
    "start_ms":..., "end_ms":...,
    "asr":"...", "ocr":[...],
    "room_candidates":[...],              # legacy live-room source only
    "offline_page_facts": {...},           # product-page teacher/audit only
    "product_group_key":"...", "split":"train"
  }

Use only videos you are permitted to process. For E-VAds, use only its SFT/RL
training split media, never the benchmark test split for training.
"""
from __future__ import annotations
import argparse,json,re,subprocess
from pathlib import Path
from collections import defaultdict
import numpy as np
import cv2
from PIL import Image
from tqdm import tqdm

from common import read_jsonl, write_jsonl

def safe_component(value):
    value=re.sub(r"[^A-Za-z0-9._-]+","_",str(value)).strip("._")
    if not value:raise ValueError("empty/unsafe output id")
    return value[:160]

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

    used_rooms=set()
    seen_videos={}
    for src in read_jsonl(a.manifest):
        video=Path(src["video"])
        if src.get("product_group_key"):
            suffix=str(src.get("video_sha256") or video.stem)[:16]
            room=safe_component(f"product_{src['product_group_key']}_{suffix}")
        else:
            room=safe_component(src.get("room_id",video.stem))

        # Prevent accidental collision between streams.
        if room in used_rooms:
            raise ValueError(
                f"Duplicate room_id detected: {room}"
            )
        used_rooms.add(room)

        video_key=str(video.resolve())
        split=src.get("split")
        if video_key in seen_videos and seen_videos[video_key] != split:
            raise ValueError(
                f"Leakage detected: same video appears in multiple splits: "
                f"{video_key}"
            )
        seen_videos[video_key]=split

        candidates=[]
        if src.get("catalog"):
            raw=Path(src["catalog"]).read_text(encoding="utf-8")
            try:
                candidates=json.loads(raw)
            except json.JSONDecodeError:
                # accept JSONL catalogs
                candidates=[json.loads(x) for x in raw.splitlines() if x.strip()]
            if not isinstance(candidates,list):
                raise ValueError("catalog must be a JSON array or JSONL")
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
                "source":src.get("source","owned_or_licensed"),
                "source_kind":src.get("source_kind","live_room"),
                "split":src.get("split"),
                "product_group_key":src.get("product_group_key"),
                "source_product_id":src.get("source_product_id"),
                "video_sha256":src.get("video_sha256"),
                # This field may be read by the teacher and audit tools only.
                # export_p2_student_samples.py removes it from training JSONL.
                "offline_page_facts":src.get("page_facts") or {},
                "provenance":src.get("provenance") or {},
            })
    write_jsonl(a.output_jsonl,rows)
    print("windows:",len(rows))

if __name__=="__main__":main()
