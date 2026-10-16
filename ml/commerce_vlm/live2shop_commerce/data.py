from __future__ import annotations

import json
import random
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset

from .model import visual_prefix

class JsonlCommerceDataset(Dataset):
    """
    JSONL schema:
      {
        "media": "/path/image.jpg|video.mp4",
        "media_type": "image|video",
        "prompt": "...",
        "target": "...",
        "retrieval_text": "...",
        "negative_text": "...",             # optional
        "meta": {...}
      }
    """
    def __init__(self, path: str):
        self.path = path
        with open(path, "r", encoding="utf-8") as f:
            self.rows = [json.loads(line) for line in f if line.strip()]

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        return self.rows[i]

class PreferenceDataset(Dataset):
    """
    JSONL:
      {"media": ..., "media_type": ..., "prompt": ..., "chosen": ..., "rejected": ...}
    """
    def __init__(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            self.rows = [json.loads(line) for line in f if line.strip()]
    def __len__(self): return len(self.rows)
    def __getitem__(self, i): return self.rows[i]

def load_frames(media: str, media_type: str, max_frames: int) -> list[Image.Image]:
    if media_type == "image":
        return [Image.open(media).convert("RGB")]

    cap = cv2.VideoCapture(media)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {media}")
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if n <= 0:
        # Sequential fallback for containers without frame count metadata.
        frames = []
        while True:
            ok, frame = cap.read()
            if not ok: break
            frames.append(frame)
        cap.release()
        if not frames:
            raise RuntimeError(f"no frames in {media}")
        idxs = np.linspace(0, len(frames)-1, max_frames).astype(int)
        return [Image.fromarray(cv2.cvtColor(frames[i], cv2.COLOR_BGR2RGB)) for i in idxs]

    idxs = np.linspace(0, max(0, n - 1), max_frames).astype(int)
    out = []
    for idx in idxs:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if not ok:
            continue
        out.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
    cap.release()
    if not out:
        raise RuntimeError(f"no decodable frames in {media}")
    while len(out) < max_frames:
        out.append(out[-1].copy())
    return out[:max_frames]

class CommerceCollator:
    def __init__(self, model, max_length: int | None = None):
        self.model = model
        self.tokenizer = model.tokenizer
        self.processor = model.vision_processor
        self.max_length = max_length or model.cfg.max_length

    def _media(self, rows):
        per_sample = [
            load_frames(r["media"], r.get("media_type", "image"), self.model.cfg.max_frames)
            for r in rows
        ]
        max_f = max(len(x) for x in per_sample)
        # Homogeneous image batches use one frame, making P0 ~8x cheaper than
        # unnecessarily repeating the same image through the vision encoder.
        padded = []
        for frames in per_sample:
            frames = list(frames)
            while len(frames) < max_f:
                frames.append(frames[-1].copy())
            padded.extend(frames[:max_f])
        proc = self.processor(images=padded, return_tensors="pt")
        pv = proc["pixel_values"]
        b = len(rows)
        return pv.reshape(b, max_f, *pv.shape[1:])

    def _text(self, rows, target_key="target"):
        prompts, full = [], []
        prefix = visual_prefix(self.model.cfg.num_visual_queries)
        for row in rows:
            prompt = f"{prefix}\n### Input:\n{row['prompt']}\n### Response:\n"
            target = row[target_key]
            prompts.append(prompt)
            full.append(prompt + target + self.tokenizer.eos_token)

        p = self.tokenizer(
            prompts, padding=True, truncation=True,
            max_length=self.max_length, return_tensors="pt"
        )
        x = self.tokenizer(
            full,
            padding=True,
            truncation=True,
            max_length=self.max_length,
            return_tensors="pt"
        )

        # Hard fail is dangerous during large training jobs. Instead expose
        # truncation information for dataset audit/logging.
        x["was_truncated"] = torch.tensor(
            [
                int(len(ids) >= self.max_length)
                for ids in self.tokenizer(
                    full,
                    padding=False,
                    truncation=False
                ).input_ids
            ],
            dtype=torch.bool,
        )
        labels = x.input_ids.clone()
        # Mask padding and prompt tokens.
        labels[x.attention_mask == 0] = -100
        for i in range(len(rows)):
            plen = int(p.attention_mask[i].sum())
            labels[i, :plen] = -100
        return x.input_ids, x.attention_mask, labels

    def __call__(self, rows):
        input_ids, mask, labels = self._text(rows)
        retrieval_texts = [r.get("retrieval_text") or r.get("target", "") for r in rows]
        rt = self.tokenizer(
            retrieval_texts, padding=True, truncation=True,
            max_length=128, return_tensors="pt"
        )
        return {
            "pixel_values": self._media(rows),
            "input_ids": input_ids,
            "attention_mask": mask,
            "labels": labels,
            "retrieval_text_input_ids": rt.input_ids,
            "retrieval_text_attention_mask": rt.attention_mask,
        }

class PreferenceCollator(CommerceCollator):
    def __call__(self, rows):
        chosen_ids, chosen_mask, chosen_labels = self._text(rows, "chosen")
        rejected_ids, rejected_mask, rejected_labels = self._text(rows, "rejected")
        return {
            "pixel_values": self._media(rows),
            "chosen_input_ids": chosen_ids,
            "chosen_attention_mask": chosen_mask,
            "chosen_labels": chosen_labels,
            "rejected_input_ids": rejected_ids,
            "rejected_attention_mask": rejected_mask,
            "rejected_labels": rejected_labels,
        }
