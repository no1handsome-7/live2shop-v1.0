from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

@dataclass
class SessionRecConfig:
    input_dim: int = 128
    hidden_dim: int = 256
    output_dim: int = 128
    max_seq_len: int = 64
    num_actions: int = 16
    num_time_buckets: int = 32
    num_duration_buckets: int = 32
    layers: int = 2
    heads: int = 4
    dropout: float = 0.1

class SessionRecModel(nn.Module):
    def __init__(self, cfg: SessionRecConfig):
        super().__init__()
        self.cfg = cfg
        h = cfg.hidden_dim
        self.content_adapter = nn.Sequential(
            nn.Linear(cfg.input_dim, h),
            nn.LayerNorm(h),
        )
        self.action_emb = nn.Embedding(cfg.num_actions, h)
        self.time_emb = nn.Embedding(cfg.num_time_buckets, h)
        self.duration_emb = nn.Embedding(cfg.num_duration_buckets, h)
        self.position = nn.Embedding(cfg.max_seq_len, h)

        layer = nn.TransformerEncoderLayer(
            d_model=h, nhead=cfg.heads,
            dim_feedforward=h * 4,
            dropout=cfg.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=cfg.layers)
        self.user_head = nn.Sequential(
            nn.LayerNorm(h),
            nn.Linear(h, cfg.output_dim),
        )
        self.item_adapter = nn.Sequential(
            nn.Linear(cfg.input_dim, cfg.output_dim),
            nn.LayerNorm(cfg.output_dim),
        )
        # [u, i, u*i, dot, business_features(4)]
        self.rank_head = nn.Sequential(
            nn.Linear(cfg.output_dim * 3 + 1 + 4, 256),
            nn.GELU(),
            nn.Dropout(cfg.dropout),
            nn.Linear(256, 64),
            nn.GELU(),
            nn.Linear(64, 1),
        )

    def encode_user(
        self,
        content: torch.Tensor,      # [B,L,128]
        action: torch.Tensor,       # [B,L]
        time_bucket: torch.Tensor,  # [B,L]
        duration_bucket: torch.Tensor, # [B,L]
        mask: torch.Tensor,         # [B,L], True for valid
    ) -> torch.Tensor:
        b, l, _ = content.shape
        if l > self.cfg.max_seq_len:
            content = content[:, -self.cfg.max_seq_len:]
            action = action[:, -self.cfg.max_seq_len:]
            time_bucket = time_bucket[:, -self.cfg.max_seq_len:]
            duration_bucket = duration_bucket[:, -self.cfg.max_seq_len:]
            mask = mask[:, -self.cfg.max_seq_len:]
            l = self.cfg.max_seq_len

        pos = torch.arange(l, device=content.device)[None].expand(b, -1)
        x = (
            self.content_adapter(content)
            + self.action_emb(action)
            + self.time_emb(time_bucket)
            + self.duration_emb(duration_bucket)
            + self.position(pos)
        )
        # Causal: token t does not see future interactions.
        causal = torch.triu(
            torch.ones(l, l, device=x.device, dtype=torch.bool), diagonal=1
        )
        x = self.encoder(
            x,
            mask=causal,
            src_key_padding_mask=~mask,
        )

        lengths = mask.long().sum(dim=1).clamp_min(1)
        idx = (lengths - 1).view(-1, 1, 1).expand(-1, 1, x.size(-1))
        last = x.gather(1, idx).squeeze(1)
        return F.normalize(self.user_head(last), dim=-1)

    def adapt_items(self, item_embedding: torch.Tensor) -> torch.Tensor:
        return F.normalize(self.item_adapter(item_embedding), dim=-1)

    def retrieval_logits(
        self, user: torch.Tensor, positive: torch.Tensor, negatives: torch.Tensor, temperature: float = 0.07
    ) -> torch.Tensor:
        # positive [B,128], negatives [B,K,128]
        pos = self.adapt_items(positive)
        neg = self.adapt_items(negatives)
        cand = torch.cat([pos[:, None, :], neg], dim=1)
        return torch.einsum("bd,bkd->bk", user, cand) / temperature

    def rank(
        self,
        user: torch.Tensor,                 # [B,128]
        item_embedding: torch.Tensor,       # [B,N,128]
        business_features: torch.Tensor | None = None,  # [B,N,4]
    ) -> torch.Tensor:
        items = self.adapt_items(item_embedding)
        b, n, d = items.shape
        u = user[:, None, :].expand(-1, n, -1)
        dot = (u * items).sum(dim=-1, keepdim=True)
        if business_features is None:
            business_features = torch.zeros(b, n, 4, device=items.device, dtype=items.dtype)
        x = torch.cat([u, items, u * items, dot, business_features], dim=-1)
        return self.rank_head(x).squeeze(-1)

    def save_checkpoint(self, out_dir: str | Path):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "rec_config.json").write_text(json.dumps(asdict(self.cfg), indent=2))
        torch.save(self.state_dict(), out / "model.pt")

    @classmethod
    def from_checkpoint(cls, path: str | Path, map_location="cpu"):
        path = Path(path)
        cfg = SessionRecConfig(**json.loads((path / "rec_config.json").read_text()))
        m = cls(cfg)
        m.load_state_dict(torch.load(path / "model.pt", map_location=map_location))
        return m

ACTION_TO_ID = {
    "ENTER_ROOM": 1,
    "HEARTBEAT": 2,
    "LIKE": 3,
    "FOLLOW": 4,
    "ITEM_CLICK": 5,
    "EXIT_ROOM": 6,
    "IMPRESSION": 7,
    "CLICK": 8,
    "LONG_VIEW": 9,
    "FAVORITE": 10,
    "ADD_CART": 11,
    "PURCHASE": 12,
}

def log_bucket(ms: int, buckets: int = 32, base_ms: int = 1000) -> int:
    if ms <= 0:
        return 0
    import math
    return min(buckets - 1, 1 + int(math.log1p(ms / base_ms) / math.log(1.7)))
