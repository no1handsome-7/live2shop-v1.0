from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import (
    AutoModelForCausalLM,
    AutoProcessor,
    AutoTokenizer,
    SiglipVisionModel,
)

VIS_TOKEN = "<vis>"

@dataclass
class CommerceVLMConfig:
    llm_model: str = "Qwen/Qwen3-1.7B-Base"
    vision_model: str = "google/siglip2-base-patch16-224"
    num_visual_queries: int = 16
    max_frames: int = 8
    retrieval_dim: int = 128
    max_length: int = 768
    temporal_heads: int = 8
    lora_rank: int = 32
    lora_alpha: int = 64
    lora_dropout: float = 0.05

class TemporalResampler(nn.Module):
    def __init__(self, vision_dim: int, num_queries: int, max_frames: int, heads: int):
        super().__init__()
        self.num_queries = num_queries
        self.max_frames = max_frames
        self.queries = nn.Parameter(torch.randn(num_queries, vision_dim) * 0.02)
        self.temporal = nn.Parameter(torch.randn(max_frames, vision_dim) * 0.01)
        self.norm_in = nn.LayerNorm(vision_dim)
        self.attn = nn.MultiheadAttention(vision_dim, heads, batch_first=True)
        self.norm_out = nn.LayerNorm(vision_dim)
        self.ff = nn.Sequential(
            nn.Linear(vision_dim, vision_dim * 4),
            nn.GELU(),
            nn.Linear(vision_dim * 4, vision_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: [B, F, P, D]
        b, f, p, d = x.shape
        if f > self.max_frames:
            raise ValueError(f"frames={f} exceeds max_frames={self.max_frames}")
        x = x + self.temporal[:f][None, :, None, :]
        kv = self.norm_in(x.reshape(b, f * p, d))
        q = self.queries[None].expand(b, -1, -1)
        out, _ = self.attn(q, kv, kv, need_weights=False)
        out = self.norm_out(out + q)
        out = self.norm_out(out + self.ff(out))
        return out

class CommerceVLM(nn.Module):
    def __init__(self, cfg: CommerceVLMConfig, dtype: torch.dtype = torch.bfloat16):
        super().__init__()
        self.cfg = cfg
        self.dtype = dtype

        self.tokenizer = AutoTokenizer.from_pretrained(cfg.llm_model, use_fast=True)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        if VIS_TOKEN not in self.tokenizer.get_vocab():
            self.tokenizer.add_special_tokens({"additional_special_tokens": [VIS_TOKEN]})

        self.vision_processor = AutoProcessor.from_pretrained(cfg.vision_model)
        self.vision = SiglipVisionModel.from_pretrained(cfg.vision_model, torch_dtype=dtype)
        self.llm = AutoModelForCausalLM.from_pretrained(
            cfg.llm_model,
            torch_dtype=dtype,
            # Prefer Flash Attention when installed, fallback to SDPA.
            attn_implementation="flash_attention_2",
        )
        self.llm.resize_token_embeddings(len(self.tokenizer))

        vision_dim = int(self.vision.config.hidden_size)
        llm_dim = int(self.llm.config.hidden_size)

        self.resampler = TemporalResampler(
            vision_dim=vision_dim,
            num_queries=cfg.num_visual_queries,
            max_frames=cfg.max_frames,
            heads=cfg.temporal_heads,
        )
        self.projector = nn.Sequential(
            nn.Linear(vision_dim, llm_dim),
            nn.GELU(),
            nn.LayerNorm(llm_dim),
            nn.Linear(llm_dim, llm_dim),
        )
        self.visual_retrieval_head = nn.Linear(llm_dim, cfg.retrieval_dim)
        self.text_retrieval_head = nn.Linear(llm_dim, cfg.retrieval_dim)

        self.vis_token_id = self.tokenizer.convert_tokens_to_ids(VIS_TOKEN)

    def freeze_backbones(self) -> None:
        for p in self.vision.parameters():
            p.requires_grad = False
        for p in self.llm.parameters():
            p.requires_grad = False
        for module in (
            self.resampler, self.projector,
            self.visual_retrieval_head, self.text_retrieval_head,
        ):
            for p in module.parameters():
                p.requires_grad = True

    def enable_llm_lora(self) -> None:
        from peft import LoraConfig, PeftModel, get_peft_model
        if isinstance(self.llm, PeftModel):
            # freeze_backbones() intentionally disables every LLM parameter.
            # When resuming P1/P2/P3/P4, explicitly reactivate the loaded LoRA
            # parameters; otherwise later stages would silently train only the
            # multimodal bridge.
            for name, p in self.llm.named_parameters():
                p.requires_grad = ("lora_" in name)
            return
        conf = LoraConfig(
            r=self.cfg.lora_rank,
            lora_alpha=self.cfg.lora_alpha,
            lora_dropout=self.cfg.lora_dropout,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=[
                "q_proj", "k_proj", "v_proj", "o_proj",
                "gate_proj", "up_proj", "down_proj",
            ],
        )
        self.llm = get_peft_model(self.llm, conf)

    def configure_stage(self, stage: str) -> None:
        self.freeze_backbones()
        stage = stage.lower()
        if stage in {"cpt", "sft", "dpo", "grpo"}:
            self.enable_llm_lora()
        if stage in {"cpt", "sft", "dpo", "grpo"}:
            # Projector/resampler remain trainable for domain adaptation.
            pass
        if stage == "alignment":
            # Only randomly initialized multimodal bridge + retrieval heads train.
            return
        if stage not in {"cpt", "sft", "dpo", "grpo"}:
            raise ValueError(f"unknown stage: {stage}")

    def encode_visual(self, pixel_values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        # pixel_values [B,F,3,H,W]
        b, f, c, h, w = pixel_values.shape
        flat = pixel_values.reshape(b * f, c, h, w).to(
            device=next(self.vision.parameters()).device,
            dtype=next(self.vision.parameters()).dtype,
        )
        with torch.set_grad_enabled(any(p.requires_grad for p in self.vision.parameters())):
            out = self.vision(pixel_values=flat).last_hidden_state
        p, d = out.shape[1], out.shape[2]
        out = out.reshape(b, f, p, d)
        queries = self.resampler(out.to(next(self.resampler.parameters()).dtype))
        visual_tokens = self.projector(queries)
        retrieval = F.normalize(self.visual_retrieval_head(visual_tokens.mean(dim=1)), dim=-1)
        return visual_tokens, retrieval

    def encode_text_for_retrieval(
        self, input_ids: torch.Tensor, attention_mask: torch.Tensor
    ) -> torch.Tensor:
        # Lightweight text tower that shares Qwen's learned token space. This is
        # deliberately cheap because P0 freezes the language backbone.
        emb = self.llm.get_input_embeddings()(input_ids)
        mask = attention_mask[..., None].to(emb.dtype)
        pooled = (emb * mask).sum(dim=1) / mask.sum(dim=1).clamp_min(1.0)
        return F.normalize(self.text_retrieval_head(pooled), dim=-1)

    def inject_visual(
        self, input_ids: torch.Tensor, visual_tokens: torch.Tensor
    ) -> torch.Tensor:
        embeds = self.llm.get_input_embeddings()(input_ids)
        for i in range(input_ids.size(0)):
            positions = (input_ids[i] == self.vis_token_id).nonzero(as_tuple=False).flatten()
            if positions.numel() != self.cfg.num_visual_queries:
                raise ValueError(
                    f"sample {i}: expected {self.cfg.num_visual_queries} {VIS_TOKEN} tokens, "
                    f"found {positions.numel()}"
                )
            embeds[i, positions] = visual_tokens[i].to(embeds.dtype)
        return embeds

    def forward(
        self,
        pixel_values: torch.Tensor,
        input_ids: torch.Tensor,
        attention_mask: torch.Tensor,
        labels: Optional[torch.Tensor] = None,
        retrieval_text_input_ids: Optional[torch.Tensor] = None,
        retrieval_text_attention_mask: Optional[torch.Tensor] = None,
    ) -> dict:
        visual_tokens, visual_retrieval = self.encode_visual(pixel_values)
        inputs_embeds = self.inject_visual(input_ids, visual_tokens)
        out = self.llm(
            inputs_embeds=inputs_embeds,
            attention_mask=attention_mask,
            labels=labels,
            use_cache=False,
        )
        result = {
            "loss": out.loss,
            "logits": out.logits,
            "visual_retrieval": visual_retrieval,
        }
        if retrieval_text_input_ids is not None:
            result["text_retrieval"] = self.encode_text_for_retrieval(
                retrieval_text_input_ids, retrieval_text_attention_mask
            )
        return result

    @torch.inference_mode()
    def generate_json(
        self,
        pixel_values: torch.Tensor,
        prompt: str,
        max_new_tokens: int = 256,
        temperature: float = 0.0,
    ) -> str:
        device = next(self.parameters()).device
        visual_tokens, _ = self.encode_visual(pixel_values.to(device))
        prefix = visual_prefix(self.cfg.num_visual_queries)
        text = f"{prefix}\n### Input:\n{prompt}\n### Response:\n"
        encoded = self.tokenizer(
            text, return_tensors="pt", truncation=True, max_length=self.cfg.max_length
        ).to(device)
        inputs_embeds = self.inject_visual(encoded.input_ids, visual_tokens)
        kwargs = dict(
            inputs_embeds=inputs_embeds,
            attention_mask=encoded.attention_mask,
            max_new_tokens=max_new_tokens,
            do_sample=temperature > 0,
            pad_token_id=self.tokenizer.pad_token_id,
            eos_token_id=self.tokenizer.eos_token_id,
        )
        if temperature > 0:
            kwargs["temperature"] = temperature
        ids = self.llm.generate(**kwargs)
        # With inputs_embeds, generated ids contain generated tokens only on
        # recent Transformers versions; tolerate either behavior.
        return self.tokenizer.decode(ids[0], skip_special_tokens=True).strip()

    def save_checkpoint(self, out_dir: str | Path) -> None:
        from peft import PeftModel
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "commerce_config.json").write_text(
            json.dumps(asdict(self.cfg), ensure_ascii=False, indent=2)
        )
        custom = {
            "resampler": self.resampler.state_dict(),
            "projector": self.projector.state_dict(),
            "visual_retrieval_head": self.visual_retrieval_head.state_dict(),
            "text_retrieval_head": self.text_retrieval_head.state_dict(),
        }
        torch.save(custom, out / "multimodal_bridge.pt")
        self.tokenizer.save_pretrained(out / "tokenizer")
        if isinstance(self.llm, PeftModel):
            self.llm.save_pretrained(out / "llm_adapter")

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: str | Path,
        dtype: torch.dtype = torch.bfloat16,
        trainable_adapter: bool = False,
    ) -> "CommerceVLM":
        from peft import PeftModel

        checkpoint = Path(checkpoint)
        cfg = CommerceVLMConfig(**json.loads((checkpoint / "commerce_config.json").read_text()))
        model = cls(cfg, dtype=dtype)
        custom = torch.load(checkpoint / "multimodal_bridge.pt", map_location="cpu")
        model.resampler.load_state_dict(custom["resampler"])
        model.projector.load_state_dict(custom["projector"])
        model.visual_retrieval_head.load_state_dict(custom["visual_retrieval_head"])
        model.text_retrieval_head.load_state_dict(custom["text_retrieval_head"])
        if (checkpoint / "llm_adapter").exists():
            model.llm = PeftModel.from_pretrained(
                model.llm,
                checkpoint / "llm_adapter",
                is_trainable=trainable_adapter,
            )
        return model

def visual_prefix(n: int) -> str:
    return " ".join([VIS_TOKEN] * n)
