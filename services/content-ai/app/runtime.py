from __future__ import annotations

import json
import os
import json
from pathlib import Path

import torch
from PIL import Image

from live2shop_commerce import CommerceVLM
from live2shop_commerce.reward import parse_json_loose

class CommerceRuntime:
    def __init__(self, checkpoint: str):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = CommerceVLM.from_checkpoint(checkpoint).to(self.device).eval()

    @torch.inference_mode()
    def infer(self, frames: list[Image.Image], prompt: str) -> tuple[dict, list[float]]:
        proc = self.model.vision_processor(images=frames, return_tensors="pt")
        pv = proc["pixel_values"].reshape(
            1, self.model.cfg.max_frames, *proc["pixel_values"].shape[1:]
        ).to(self.device)
        text = self.model.generate_json(pv, prompt, max_new_tokens=256, temperature=0.0)
        obj = parse_json_loose(text)
        if obj is None:
            raise ValueError(f"model did not emit JSON: {text[:300]}")

        _, visual_emb = self.model.encode_visual(pv)
        semantic_text = json.dumps(obj, ensure_ascii=False, sort_keys=True)
        tok = self.model.tokenizer(
            [semantic_text], return_tensors="pt",
            truncation=True, max_length=192
        ).to(self.device)
        text_emb = self.model.encode_text_for_retrieval(
            tok.input_ids, tok.attention_mask
        )
        fused = torch.nn.functional.normalize(
            0.6 * visual_emb + 0.4 * text_emb, dim=-1
        )
        return obj, fused[0].float().cpu().tolist()
