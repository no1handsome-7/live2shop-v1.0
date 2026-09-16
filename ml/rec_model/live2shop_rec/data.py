from __future__ import annotations

import glob
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset

class NPZSessionDataset(Dataset):
    """
    Each .npz shard contains:
      content:       [N,L,128] float16/float32
      action:        [N,L] int64
      time_bucket:   [N,L] int64
      duration_bucket:[N,L] int64
      mask:          [N,L] bool
      positive:      [N,128]
      negatives:     [N,K,128]
      click_label:   [N] float32 (optional)
      candidate:     [N,128] (optional; defaults positive)
      business:      [N,4] optional
    """
    def __init__(self, path_or_glob: str):
        paths = sorted(glob.glob(path_or_glob))
        if not paths and Path(path_or_glob).is_file():
            paths = [path_or_glob]
        if not paths:
            raise FileNotFoundError(path_or_glob)

        self.shards = []
        self.index = []
        for p in paths:
            arr = np.load(p, mmap_mode="r")
            n = len(arr["content"])
            shard_idx = len(self.shards)
            self.shards.append(arr)
            self.index.extend((shard_idx, i) for i in range(n))

    def __len__(self):
        return len(self.index)

    def __getitem__(self, idx):
        s, i = self.index[idx]
        a = self.shards[s]
        out = {
            "content": torch.from_numpy(np.array(a["content"][i], dtype=np.float32)),
            "action": torch.from_numpy(np.array(a["action"][i], dtype=np.int64)),
            "time_bucket": torch.from_numpy(np.array(a["time_bucket"][i], dtype=np.int64)),
            "duration_bucket": torch.from_numpy(np.array(a["duration_bucket"][i], dtype=np.int64)),
            "mask": torch.from_numpy(np.array(a["mask"][i], dtype=np.bool_)),
            "positive": torch.from_numpy(np.array(a["positive"][i], dtype=np.float32)),
            "negatives": torch.from_numpy(np.array(a["negatives"][i], dtype=np.float32)),
        }
        if "retrieval_mask" in a.files:
            out["retrieval_mask"] = torch.tensor(float(a["retrieval_mask"][i]), dtype=torch.float32)
        if "click_label" in a.files:
            out["click_label"] = torch.tensor(float(a["click_label"][i]), dtype=torch.float32)
        if "candidate" in a.files:
            out["candidate"] = torch.from_numpy(np.array(a["candidate"][i], dtype=np.float32))
        if "business" in a.files:
            out["business"] = torch.from_numpy(np.array(a["business"][i], dtype=np.float32))
        return out
