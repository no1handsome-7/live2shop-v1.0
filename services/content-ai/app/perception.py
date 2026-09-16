from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

def sample_frames(path: str, n: int = 8) -> list[Image.Image]:
    cap = cv2.VideoCapture(path)
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    idxs = np.linspace(0, max(count - 1, 0), n).astype(int)
    frames = []
    for idx in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, f = cap.read()
        if ok:
            frames.append(Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB)))
    cap.release()
    if not frames:
        raise RuntimeError(f"no frames decoded from {path}")
    while len(frames) < n:
        frames.append(frames[-1].copy())
    return frames

class ASR:
    def __init__(self, model_name: str = "small"):
        from faster_whisper import WhisperModel
        device = "cuda" if os.getenv("CUDA_VISIBLE_DEVICES", "") not in ("", "-1") else "cpu"
        compute = "float16" if device == "cuda" else "int8"
        self.model = WhisperModel(model_name, device=device, compute_type=compute)

    def transcribe(self, path: str) -> str:
        segments, _ = self.model.transcribe(path, language="zh", vad_filter=True)
        return " ".join(seg.text.strip() for seg in segments).strip()

class OCR:
    def __init__(self):
        from rapidocr import RapidOCR
        self.ocr = RapidOCR(params={"Rec.lang_type": "ch", "Global.text_score": 0.55})

    def run(self, frames: list[Image.Image]) -> list[str]:
        texts = []
        for im in frames[::2]:
            result = self.ocr(np.asarray(im))
            if result is None:
                continue
            txts = getattr(result, "txts", None) or []
            scores = getattr(result, "scores", None) or [1.0] * len(txts)
            for txt, score in zip(txts, scores):
                if float(score) >= 0.55 and str(txt).strip():
                    texts.append(str(txt).strip())
        return list(dict.fromkeys(texts))[:40]

def parse_json_loose(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            return json.loads(text[s:e+1])
        raise

def build_prompt(asr: str, ocr: list[str], candidates: list[dict], previous: dict | None) -> str:
    slim = []
    for c in candidates:
        slim.append({
            "item_id": c["item_id"],
            "title": c["title"],
            "category_path": c.get("category_path") or [],
            "brand": c.get("brand"),
            "style_tags": c.get("style_tags") or [],
            "price_cent": c.get("price_cent"),
        })
    schema = {
        "product_visible": True,
        "active_item_id": 0,
        "category": "",
        "brand": "",
        "style_tags": [],
        "color": "",
        "attributes": {},
        "price_cent": 0,
        "promotion_tags": [],
        "evidence": {"asr": [], "ocr": [], "visual": []},
        "confidence": 0.0,
    }
    return (
        "你是直播电商内容状态解析器。根据视频帧、ASR、OCR以及直播间候选商品池，"
        "输出当前窗口的完整商品状态。无法可靠确认 SKU 时 active_item_id=null；"
        "不得编造价格、品牌或促销。只输出 JSON。\n"
        f"ASR: {asr}\nOCR: {ocr}\n"
        f"候选商品: {json.dumps(slim, ensure_ascii=False)}\n"
        f"上一状态: {json.dumps(previous or {}, ensure_ascii=False)}\n"
        f"JSON schema示例: {json.dumps(schema, ensure_ascii=False)}"
    )
