from __future__ import annotations

import json
import math
import os
import random
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
import yaml

def seed_everything(seed: int):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def gather_with_grad(x: torch.Tensor) -> torch.Tensor:
    """Differentiable all-gather for distributed contrastive learning."""
    if (
        torch.distributed.is_available()
        and torch.distributed.is_initialized()
    ):
        from torch.distributed.nn.functional import all_gather
        return torch.cat(list(all_gather(x)), dim=0)
    return x


def contrastive_loss(a: torch.Tensor, b: torch.Tensor, temperature: float = 0.07):
    if a.size(0) < 2:
        return a.new_zeros(())
    logits = a @ b.t() / temperature
    labels = torch.arange(a.size(0), device=a.device)
    return 0.5 * (
        F.cross_entropy(logits, labels) +
        F.cross_entropy(logits.t(), labels)
    )

def sequence_logp(logits: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
    # Decoder-only shift.
    shift_logits = logits[:, :-1].float()
    shift_labels = labels[:, 1:]
    mask = shift_labels != -100
    safe = shift_labels.masked_fill(~mask, 0)
    lp = F.log_softmax(shift_logits, dim=-1).gather(-1, safe.unsqueeze(-1)).squeeze(-1)
    lp = lp * mask
    return lp.sum(dim=-1) / mask.sum(dim=-1).clamp_min(1)

def trainable_parameters(model):
    return [p for p in model.parameters() if p.requires_grad]

def count_trainable(model):
    train = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    return train, total

def atomic_checkpoint(model, out_dir: str):
    out = Path(out_dir)
    tmp = out.with_name(out.name + ".tmp")
    if tmp.exists():
        import shutil; shutil.rmtree(tmp)
    model.save_checkpoint(tmp)
    if out.exists():
        import shutil; shutil.rmtree(out)
    tmp.rename(out)
