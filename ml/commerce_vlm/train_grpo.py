"""
Small-scale RLVR / group-relative policy optimization for Live2Shop.

This implementation intentionally targets 3x24GB cards and one-month project scope:
- completions are sampled on-policy;
- rewards are deterministic verifiers;
- rewards are normalized within each prompt group;
- a clipped sequence-level importance ratio is used;
- a frozen SFT/DPO reference supplies a KL-style regularizer.

It is not a drop-in reproduction of a vendor RL framework; it is a compact,
auditable implementation suitable for this project and ablations.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from torch.optim import AdamW
from tqdm.auto import tqdm

from live2shop_commerce.data import load_frames
from live2shop_commerce.model import CommerceVLM, visual_prefix
from live2shop_commerce.reward import verifier_reward
from live2shop_commerce.train_utils import atomic_checkpoint, load_yaml, sequence_logp, seed_everything, trainable_parameters

def prepare_pixels(model, media, media_type, device):
    frames = load_frames(media, media_type, model.cfg.max_frames)
    proc = model.vision_processor(images=frames, return_tensors="pt")
    pv = proc["pixel_values"].reshape(1, model.cfg.max_frames, *proc["pixel_values"].shape[1:])
    return pv.to(device)

def build_prompt(model, prompt):
    prefix = visual_prefix(model.cfg.num_visual_queries)
    return f"{prefix}\n### Input:\n{prompt}\n### Response:\n"

@torch.no_grad()
def sample_completion(model, pixels, prompt, temperature, max_new_tokens):
    device = pixels.device
    text = build_prompt(model, prompt)
    tok = model.tokenizer(text, return_tensors="pt", truncation=True, max_length=model.cfg.max_length).to(device)
    visual, _ = model.encode_visual(pixels)
    emb = model.inject_visual(tok.input_ids, visual)
    ids = model.llm.generate(
        inputs_embeds=emb,
        attention_mask=tok.attention_mask,
        max_new_tokens=max_new_tokens,
        do_sample=True,
        temperature=temperature,
        top_p=0.95,
        eos_token_id=model.tokenizer.eos_token_id,
        pad_token_id=model.tokenizer.pad_token_id,
    )
    return model.tokenizer.decode(ids[0], skip_special_tokens=True).strip()

def completion_logp(model, pixels, prompt, completion):
    device = pixels.device
    p = build_prompt(model, prompt)
    pt = model.tokenizer(p, return_tensors="pt", truncation=True, max_length=model.cfg.max_length).to(device)
    full = model.tokenizer(
        p + completion + model.tokenizer.eos_token,
        return_tensors="pt", truncation=True, max_length=model.cfg.max_length
    ).to(device)
    labels = full.input_ids.clone()
    labels[:, :pt.input_ids.size(1)] = -100
    visual, _ = model.encode_visual(pixels)
    emb = model.inject_visual(full.input_ids, visual)
    out = model.llm(inputs_embeds=emb, attention_mask=full.attention_mask, use_cache=False)
    return sequence_logp(out.logits, labels)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = load_yaml(args.config)
    seed_everything(int(cfg.get("seed", 42)))

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    ckpt = cfg["model"]["init_checkpoint"]
    policy = CommerceVLM.from_checkpoint(ckpt, trainable_adapter=True).to(device)
    policy.configure_stage("grpo")
    reference = CommerceVLM.from_checkpoint(ckpt, trainable_adapter=False).to(device)
    for p in reference.parameters(): p.requires_grad = False
    reference.eval()

    rows = [json.loads(x) for x in Path(cfg["data"]["train_jsonl"]).read_text().splitlines() if x.strip()]
    opt = AdamW(trainable_parameters(policy), lr=float(cfg["train"]["lr"]))

    group = int(cfg["train"].get("group_size", 4))
    clip_eps = float(cfg["train"].get("clip_eps", 0.2))
    kl_beta = float(cfg["train"].get("kl_beta", 0.02))
    temp = float(cfg["train"].get("temperature", 0.8))
    max_new = int(cfg["train"].get("max_new_tokens", 192))
    max_steps = int(cfg["train"]["max_steps"])

    policy.train()
    pbar = tqdm(total=max_steps)
    for step in range(max_steps):
        row = rows[step % len(rows)]
        pixels = prepare_pixels(policy, row["media"], row.get("media_type", "video"), device)
        target = row.get("target_json") or json.loads(row["target"])

        policy.eval()
        completions = [sample_completion(policy, pixels, row["prompt"], temp, max_new) for _ in range(group)]
        rewards = torch.tensor(
            [verifier_reward(c, target)[0] for c in completions],
            device=device, dtype=torch.float32,
        )
        advantages = (rewards - rewards.mean()) / rewards.std().clamp_min(1e-4)

        with torch.no_grad():
            old_logps = torch.cat([completion_logp(policy, pixels, row["prompt"], c) for c in completions])
            ref_logps = torch.cat([completion_logp(reference, pixels, row["prompt"], c) for c in completions])

        policy.train()
        new_logps = torch.cat([completion_logp(policy, pixels, row["prompt"], c) for c in completions])
        ratio = torch.exp(new_logps - old_logps)
        clipped = torch.clamp(ratio, 1.0 - clip_eps, 1.0 + clip_eps)
        pg = -torch.min(ratio * advantages, clipped * advantages).mean()

        # Sequence-level KL proxy. Keep it small and auditable.
        kl = (new_logps - ref_logps).pow(2).mean()
        loss = pg + kl_beta * kl

        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(trainable_parameters(policy), 1.0)
        opt.step()

        pbar.update(1)
        pbar.set_postfix(loss=f"{loss.item():.3f}", reward=f"{rewards.mean().item():.3f}")

    atomic_checkpoint(policy, f"{cfg['train']['output_dir']}/final")
    pbar.close()

if __name__ == "__main__":
    main()
