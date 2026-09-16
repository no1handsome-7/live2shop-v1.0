from __future__ import annotations

import json
import os
import time
from collections import deque

import numpy as np
import torch

from live2shop_rec import SessionRecModel
from live2shop_rec.model import ACTION_TO_ID, log_bucket

class RecRuntime:
    def __init__(self, checkpoint: str):
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = SessionRecModel.from_checkpoint(checkpoint, map_location=self.device).to(self.device).eval()

    @torch.inference_mode()
    def encode(self, rows: list[dict]) -> list[float]:
        max_len = self.model.cfg.max_seq_len
        rows = rows[-max_len:]
        l = len(rows)
        if l == 0:
            return [0.0] * self.model.cfg.output_dim

        content = np.zeros((1, max_len, 128), np.float32)
        action = np.zeros((1, max_len), np.int64)
        time_bucket = np.zeros((1, max_len), np.int64)
        duration_bucket = np.zeros((1, max_len), np.int64)
        mask = np.zeros((1, max_len), np.bool_)

        offset = max_len - l
        prev_ts = None
        for j, row in enumerate(rows, start=offset):
            content[0, j] = np.asarray(row["embedding"], np.float32)
            action[0, j] = ACTION_TO_ID.get(row.get("action",""), 0)
            ts = int(row.get("event_time_ms", 0))
            gap = 0 if prev_ts is None else max(0, ts - prev_ts)
            prev_ts = ts
            time_bucket[0, j] = log_bucket(gap)
            duration_bucket[0, j] = log_bucket(int(row.get("duration_ms", 0)))
            mask[0, j] = True

        t = lambda x: torch.from_numpy(x).to(self.device)
        u = self.model.encode_user(t(content), t(action), t(time_bucket), t(duration_bucket), t(mask))
        return u[0].float().cpu().tolist()

    @torch.inference_mode()
    def rank(self, user_embedding: list[float], item_embeddings: list[list[float]], features: list[list[float]] | None = None):
        u = torch.tensor([user_embedding], dtype=torch.float32, device=self.device)
        items = torch.tensor([item_embeddings], dtype=torch.float32, device=self.device)
        bf = torch.tensor([features], dtype=torch.float32, device=self.device) if features is not None else None
        score = torch.sigmoid(self.model.rank(u, items, bf))[0]
        return score.float().cpu().tolist()
