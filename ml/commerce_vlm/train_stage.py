from __future__ import annotations

import argparse
from pathlib import Path

import mlflow
import torch
from accelerate import Accelerator
from torch.optim import AdamW
from torch.utils.data import DataLoader
from tqdm.auto import tqdm

from live2shop_commerce.data import CommerceCollator, JsonlCommerceDataset
from live2shop_commerce.model import CommerceVLM, CommerceVLMConfig
from live2shop_commerce.train_utils import (
    atomic_checkpoint, contrastive_loss, count_trainable,
    load_yaml, seed_everything, trainable_parameters,
)

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

    stage = cfg["train"]["stage"]
    checkpoint = cfg["model"].get("init_checkpoint")
    if checkpoint:
        model = CommerceVLM.from_checkpoint(
            checkpoint,
            trainable_adapter=(stage != "alignment"),
        )
    else:
        model = CommerceVLM(CommerceVLMConfig(**cfg["model"]["architecture"]))

    model.configure_stage(stage)
    if cfg["train"].get("gradient_checkpointing", True):
        model.llm.gradient_checkpointing_enable()

    ds = JsonlCommerceDataset(cfg["data"]["train_jsonl"])
    collator = CommerceCollator(model)
    loader = DataLoader(
        ds,
        batch_size=int(cfg["train"]["micro_batch"]),
        shuffle=True,
        num_workers=int(cfg["data"].get("num_workers", 4)),
        collate_fn=collator,
        pin_memory=True,
    )
    params = trainable_parameters(model)
    optim = AdamW(
        params,
        lr=float(cfg["train"]["lr"]),
        weight_decay=float(cfg["train"].get("weight_decay", 0.01)),
    )
    model, optim, loader = accelerator.prepare(model, optim, loader)

    train_n, total_n = count_trainable(accelerator.unwrap_model(model))
    if accelerator.is_main_process:
        print(f"stage={stage} trainable={train_n:,}/{total_n:,} ({100*train_n/total_n:.2f}%)")

    steps = int(cfg["train"]["max_steps"])
    log_every = int(cfg["train"].get("log_every", 10))
    save_every = int(cfg["train"].get("save_every", 500))
    contrastive_weight = float(cfg["train"].get("contrastive_weight", 0.0))

    mlflow_enabled = bool(cfg.get("mlflow", {}).get("enabled", False)) and accelerator.is_main_process
    if mlflow_enabled:
        mlflow.set_tracking_uri(cfg["mlflow"]["tracking_uri"])
        mlflow.set_experiment(cfg["mlflow"]["experiment"])
        mlflow.start_run(run_name=cfg["mlflow"].get("run_name", stage))
        mlflow.log_params({
            "stage": stage,
            "lr": cfg["train"]["lr"],
            "micro_batch": cfg["train"]["micro_batch"],
            "grad_accum": cfg["train"]["grad_accum"],
            "max_steps": steps,
        })

    model.train()
    pbar = tqdm(total=steps, disable=not accelerator.is_local_main_process)
    step = 0
    epoch = 0
    while step < steps:
        epoch += 1
        for batch in loader:
            with accelerator.accumulate(model):
                out = model(**batch)
                loss = out["loss"]
                if contrastive_weight > 0:
                    c = contrastive_loss(out["visual_retrieval"], out["text_retrieval"])
                    loss = loss + contrastive_weight * c
                accelerator.backward(loss)
                if accelerator.sync_gradients:
                    accelerator.clip_grad_norm_(params, float(cfg["train"].get("max_grad_norm", 1.0)))
                optim.step()
                optim.zero_grad(set_to_none=True)

            if accelerator.sync_gradients:
                step += 1
                pbar.update(1)
                if step % log_every == 0:
                    v = accelerator.reduce(loss.detach(), reduction="mean").item()
                    pbar.set_postfix(loss=f"{v:.4f}", epoch=epoch)
                    if mlflow_enabled:
                        mlflow.log_metric("train_loss", v, step=step)

                if step % save_every == 0 or step == steps:
                    accelerator.wait_for_everyone()
                    if accelerator.is_main_process:
                        unwrapped = accelerator.unwrap_model(model)
                        atomic_checkpoint(unwrapped, f"{cfg['train']['output_dir']}/step-{step}")
                if step >= steps:
                    break

    if accelerator.is_main_process:
        atomic_checkpoint(accelerator.unwrap_model(model), f"{cfg['train']['output_dir']}/final")
        if mlflow_enabled:
            mlflow.end_run()
    pbar.close()

if __name__ == "__main__":
    main()
