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

        model_root = os.getenv(
            "RAPIDOCR_MODEL_ROOT",
            "/var/cache/rapidocr",
        )

        self.ocr = RapidOCR(
            params={
                "Rec.lang_type": "ch",
                "Global.text_score": 0.55,
                "Global.model_root_dir": model_root,
            }
        )

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

def build_prompt(asr: str, ocr: list[str]) -> str:
    return (
        "根据商品讲解视频及以下自动转写信息，输出当前窗口的 CommerceEvent JSON。"
        "只能使用视频画面、ASR 和 OCR 中可观察到的证据；未知字段使用 null、空列表或空对象，"
        "不得猜测平台商品 ID 或任何未观察属性。price_cent 仅表示当前视频中明确出现的人民币价格（分）。\n"
        f"ASR={asr or ''}\n"
        f"OCR={json.dumps(ocr or [], ensure_ascii=False)}"
    )
