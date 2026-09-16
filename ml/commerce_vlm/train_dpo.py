from __future__ import annotations

import argparse
import copy

import torch
import torch.nn.functional as F
from accelerate import Accelerator
from torch.optim import AdamW
from torch.utils.data import DataLoader
from tqdm.auto import tqdm

from live2shop_commerce.data import PreferenceCollator, PreferenceDataset
from live2shop_commerce.model import CommerceVLM
from live2shop_commerce.train_utils import (
    atomic_checkpoint, load_yaml, seed_everything,
    sequence_logp, trainable_parameters,
)

def batch_logp(model, pixel, ids, mask, labels):
    out = model(
        pixel_values=pixel,
        input_ids=ids,
        attention_mask=mask,
        labels=None,
    )
    return sequence_logp(out["logits"], labels)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_yaml(args.config)
    seed_everything(int(cfg.get("seed", 42)))

    accelerator = Accelerator(
        gradient_accumulation_steps=int(cfg["train"]["grad_accum"]),
        mixed_precision="bf16",
    )

    ckpt = cfg["model"]["init_checkpoint"]
    policy = CommerceVLM.from_checkpoint(ckpt, trainable_adapter=True)
    policy.configure_stage("dpo")
    reference = CommerceVLM.from_checkpoint(ckpt, trainable_adapter=False)
    for p in reference.parameters():
        p.requires_grad = False
    reference.eval()

    ds = PreferenceDataset(cfg["data"]["train_jsonl"])
    loader = DataLoader(
        ds,
        batch_size=int(cfg["train"]["micro_batch"]),
        shuffle=True,
        num_workers=int(cfg["data"].get("num_workers", 2)),
        collate_fn=PreferenceCollator(policy),
    )
    optim = AdamW(
        trainable_parameters(policy),
        lr=float(cfg["train"]["lr"]),
        weight_decay=float(cfg["train"].get("weight_decay", 0.01)),
    )

    policy, reference, optim, loader = accelerator.prepare(policy, reference, optim, loader)
    beta = float(cfg["train"].get("beta", 0.1))
    max_steps = int(cfg["train"]["max_steps"])
    step = 0
    pbar = tqdm(total=max_steps, disable=not accelerator.is_local_main_process)

    policy.train()
    while step < max_steps:
        for b in loader:
            with accelerator.accumulate(policy):
                pi_c = batch_logp(policy, b["pixel_values"], b["chosen_input_ids"], b["chosen_attention_mask"], b["chosen_labels"])
                pi_r = batch_logp(policy, b["pixel_values"], b["rejected_input_ids"], b["rejected_attention_mask"], b["rejected_labels"])
                with torch.no_grad():
                    ref_c = batch_logp(reference, b["pixel_values"], b["chosen_input_ids"], b["chosen_attention_mask"], b["chosen_labels"])
                    ref_r = batch_logp(reference, b["pixel_values"], b["rejected_input_ids"], b["rejected_attention_mask"], b["rejected_labels"])

                advantage = (pi_c - pi_r) - (ref_c - ref_r)
                loss = -F.logsigmoid(beta * advantage).mean()

                accelerator.backward(loss)
                optim.step()
                optim.zero_grad(set_to_none=True)

            if accelerator.sync_gradients:
                step += 1
                pbar.update(1)
                pbar.set_postfix(loss=f"{loss.item():.4f}", margin=f"{advantage.mean().item():.3f}")
                if step >= max_steps:
                    break

    accelerator.wait_for_everyone()
    if accelerator.is_main_process:
        atomic_checkpoint(accelerator.unwrap_model(policy), f"{cfg['train']['output_dir']}/final")
    pbar.close()

if __name__ == "__main__":
    main()
