from __future__ import annotations

import argparse
from pathlib import Path

import torch
import torch.nn.functional as F
import yaml
from accelerate import Accelerator
from torch.optim import AdamW
from torch.utils.data import DataLoader
from tqdm.auto import tqdm

from live2shop_rec.data import NPZSessionDataset
from live2shop_rec.model import SessionRecConfig, SessionRecModel

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())

    accelerator = Accelerator(
        gradient_accumulation_steps=int(cfg["train"].get("grad_accum", 1)),
        mixed_precision="bf16",
    )
    init = cfg["model"].get("init_checkpoint")
    if init:
        model = SessionRecModel.from_checkpoint(init)
        if cfg["model"].get("reinit_content_adapter", False):
            # Keep sequence dynamics while replacing the external embedding-space adapter.
            model.content_adapter.reset_parameters() if hasattr(model.content_adapter, "reset_parameters") else None
            for m in model.content_adapter.modules():
                if hasattr(m, "reset_parameters"): m.reset_parameters()
            for m in model.item_adapter.modules():
                if hasattr(m, "reset_parameters"): m.reset_parameters()
    else:
        model = SessionRecModel(SessionRecConfig(**cfg["model"]["architecture"]))

    ds = NPZSessionDataset(cfg["data"]["train_glob"])
    loader = DataLoader(ds, batch_size=int(cfg["train"]["micro_batch"]), shuffle=True, num_workers=4)
    opt = AdamW(model.parameters(), lr=float(cfg["train"]["lr"]), weight_decay=float(cfg["train"].get("weight_decay", 1e-4)))
    model, opt, loader = accelerator.prepare(model, opt, loader)

    max_steps = int(cfg["train"]["max_steps"])
    retrieval_weight = float(cfg["train"].get("retrieval_weight", 1.0))
    rank_weight = float(cfg["train"].get("rank_weight", 1.0))
    step = 0
    pbar = tqdm(total=max_steps, disable=not accelerator.is_local_main_process)

    model.train()
    while step < max_steps:
        for b in loader:
            with accelerator.accumulate(model):
                u = model.encode_user(
                    b["content"], b["action"], b["time_bucket"],
                    b["duration_bucket"], b["mask"],
                )
                logits = model.retrieval_logits(u, b["positive"], b["negatives"])
                labels = torch.zeros(logits.size(0), dtype=torch.long, device=logits.device)
                retrieval_each = F.cross_entropy(logits, labels, reduction="none")
                if "retrieval_mask" in b:
                    rm = b["retrieval_mask"]
                    retrieval_loss = (retrieval_each * rm).sum() / rm.sum().clamp_min(1.0)
                else:
                    retrieval_loss = retrieval_each.mean()
                loss = retrieval_weight * retrieval_loss

                rank_loss = torch.zeros((), device=loss.device)
                if "click_label" in b:
                    cand = b.get("candidate", b["positive"])[:, None, :]
                    business = b.get("business")
                    if business is not None: business = business[:, None, :]
                    rank_logit = model.rank(u, cand, business).squeeze(1)
                    rank_loss = F.binary_cross_entropy_with_logits(rank_logit, b["click_label"])
                    loss = loss + rank_weight * rank_loss

                accelerator.backward(loss)
                if accelerator.sync_gradients:
                    accelerator.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                opt.zero_grad(set_to_none=True)

            if accelerator.sync_gradients:
                step += 1
                pbar.update(1)
                pbar.set_postfix(
                    loss=f"{loss.item():.4f}",
                    retrieval=f"{retrieval_loss.item():.4f}",
                    rank=f"{rank_loss.item():.4f}",
                )
                if step >= max_steps:
                    break

    accelerator.wait_for_everyone()
    if accelerator.is_main_process:
        out = Path(cfg["train"]["output_dir"]) / "final"
        accelerator.unwrap_model(model).save_checkpoint(out)
    pbar.close()

if __name__ == "__main__":
    main()
