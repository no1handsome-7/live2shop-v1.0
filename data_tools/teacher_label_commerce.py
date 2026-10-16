#!/usr/bin/env python3
"""Weak-label P2 windows with a multimodal teacher.

Product-page facts are annotation-time candidate facts, never observations. The
teacher must decide which fields are supported by video/ASR/OCR. Final student
prompts are generated independently and contain only ASR/OCR text; page facts
remain under ``annotation_context`` until the explicit export step removes them.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

import cv2
import numpy as np
import torch
from PIL import Image
from transformers import AutoModelForImageTextToText, AutoProcessor

from common import read_jsonl, write_jsonl
from product_pages.schema import build_student_prompt, empty_commerce_event


def frames(path, n=8):
    cap = cv2.VideoCapture(path)
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    out = []
    for idx in np.linspace(0, max(count - 1, 0), n).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
        ok, frame = cap.read()
        if ok:
            out.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
    cap.release()
    while out and len(out) < n:
        out.append(out[-1].copy())
    return out


def loose_json(text: str):
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        start, end = text.find("{"), text.rfind("}")
        try:
            return json.loads(text[start:end + 1]) if start >= 0 and end > start else None
        except json.JSONDecodeError:
            return None


def normalize_teacher_answer(obj: Any) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Accept the v2 envelope and tolerate legacy flat answers for review."""
    if not isinstance(obj, dict):
        return None, {"parse_error": True}
    if isinstance(obj.get("commerce_event"), dict):
        event = obj["commerce_event"]
        annotation = obj.get("annotation") if isinstance(obj.get("annotation"), dict) else {}
    else:
        event = dict(obj)
        annotation = {}
    if "confidence" in event:
        annotation.setdefault("confidence", event.pop("confidence"))
    if "active_item_id" in event:
        annotation.setdefault("forbidden_fields_removed", []).append("active_item_id")
        event.pop("active_item_id", None)
    template = empty_commerce_event()
    missing = sorted(set(template) - set(event))
    if missing:
        annotation["teacher_missing_fields"] = missing
    normalized = {key: event.get(key, default) for key, default in template.items()}
    normalized["schema_version"] = template["schema_version"]
    return normalized, annotation


def teacher_prompt(row: dict[str, Any]) -> str:
    weak = row.get("weak_supervision") or {}
    candidates = weak.get("page_candidates") or []
    page_reference = weak.get("page_reference") or {}
    output_shape = {
        "commerce_event": empty_commerce_event(),
        "annotation": {
            "confidence": 0.0,
            "page_fact_decisions": [
                {
                    "field": "brand",
                    "decision": "supported|unobserved|conflict",
                    "observed_value": None,
                    "reason": "简短理由",
                }
            ],
        },
    }
    return (
        "你是电商视频数据标注器。连续帧、ASR、OCR 是唯一可作为 CommerceEvent 标签依据的观察证据。"
        "页面候选事实和参考摘录仅用于提醒你检查可能属性，页面本身不是证据：若候选事实未在画面、ASR 或 OCR 中得到支持，"
        "不得复制进 commerce_event；若页面与观察证据冲突，采用观察证据并在 page_fact_decisions 标记 conflict。"
        "page_fact_decisions 必须逐一覆盖输入中的每个页面候选事实。"
        "active_item_id、商品链接和店铺 ID 禁止进入 commerce_event。price_cent 仅填写窗口中明确展示或口播的价格，"
        "单位为人民币分。evidence 只能摘录或描述真实观察；无法确认的标量用 null。只输出一个 JSON 对象。\n"
        f"ASR={row.get('asr', '')}\n"
        f"OCR={json.dumps(row.get('ocr') or [], ensure_ascii=False)}\n"
        f"页面候选事实（弱监督，非证据）={json.dumps(candidates, ensure_ascii=False)}\n"
        f"页面参考摘录（弱监督，非证据）={json.dumps(page_reference, ensure_ascii=False)}\n"
        f"输出结构={json.dumps(output_shape, ensure_ascii=False)}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="Qwen/Qwen3-VL-2B-Instruct")
    parser.add_argument("--shard-id", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--max-new-tokens", type=int, default=512)
    args = parser.parse_args()

    processor = AutoProcessor.from_pretrained(args.model)
    model = AutoModelForImageTextToText.from_pretrained(
        args.model,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        attn_implementation="sdpa",
    ).eval()
    rows = list(read_jsonl(args.input))[args.shard_id::args.num_shards]
    output = []
    for index, row in enumerate(rows):
        images = frames(row["media"], 8)
        if not images:
            continue
        prompt = teacher_prompt(row)
        content = [{"type": "image"} for _ in images] + [{"type": "text", "text": prompt}]
        messages = [{"role": "user", "content": content}]
        text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = processor(text=[text], images=images, padding=True, return_tensors="pt").to(model.device)
        with torch.inference_mode():
            ids = model.generate(**inputs, max_new_tokens=args.max_new_tokens, do_sample=False)
        generated = ids[:, inputs["input_ids"].shape[1]:]
        answer = processor.batch_decode(generated, skip_special_tokens=True)[0]
        parsed = loose_json(answer)
        event, annotation = normalize_teacher_answer(parsed)
        if event is None:
            annotation["raw_answer"] = answer

        target_text = json.dumps(event, ensure_ascii=False, sort_keys=True) if event else answer
        output.append({
            "media": row["media"],
            "media_type": "video",
            "prompt": build_student_prompt(row.get("asr", ""), row.get("ocr") or []),
            "target": target_text,
            "target_json": event,
            "retrieval_text": target_text,
            "context": {
                "asr": row.get("asr", ""),
                "ocr": row.get("ocr") or [],
                "start_ms": row.get("start_ms"),
                "end_ms": row.get("end_ms"),
            },
            "annotation": annotation,
            "annotation_context": {
                "weak_supervision": row.get("weak_supervision") or {},
                "provenance": row.get("provenance") or {},
            },
            "meta": {
                "source": "teacher",
                "teacher_model": args.model,
                "commerce_event_schema": "commerce_event.v2",
                "source_kind": row.get("source_kind"),
                "source_platform": row.get("source"),
                "source_product_id": row.get("source_product_id"),
                "product_group_key": row.get("product_group_key"),
                "video_sha256": row.get("video_sha256"),
                "split": row.get("split"),
            },
        })
        if (index + 1) % 20 == 0:
            print("labeled", index + 1)
    write_jsonl(args.output, output)
    print("teacher rows:", len(output))


if __name__ == "__main__":
    main()
