#!/usr/bin/env python3
"""Align page candidate facts with ASR/OCR before multimodal teacher labeling.

The result is an annotation-time record. It is never a student training sample.
Visual support/conflicts remain ``unknown_visual`` for the multimodal teacher or
human reviewer to decide; this script only makes deterministic text decisions.
"""
from __future__ import annotations

import argparse
import re
from typing import Any

from common import read_jsonl, write_jsonl

COLORS = {
    "黑色", "白色", "灰色", "红色", "蓝色", "绿色", "黄色", "紫色", "粉色",
    "棕色", "橙色", "米色", "金色", "银色", "卡其色", "藏青色", "透明",
}


def compact(value: Any) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "")).lower()


def extract_observed_prices(text: str) -> list[int]:
    patterns = (
        r"(?:¥|￥)\s*(\d+(?:\.\d{1,2})?)",
        r"(\d+(?:\.\d{1,2})?)\s*(?:元|块钱)",
        r"(?:到手价|直播价|价格|售价|只要)\s*(?:是|为|¥|￥)?\s*(\d+(?:\.\d{1,2})?)",
    )
    out = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            cents = round(float(match.group(1)) * 100)
            if 0 <= cents <= 100_000_000:
                out.append(cents)
    return list(dict.fromkeys(out))


def candidate_facts(page_facts: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    for field in ("category", "brand", "color"):
        value = page_facts.get(field)
        if value not in (None, "", [], {}):
            candidates.append({"field": field, "page_value": value})
    for key, value in (page_facts.get("attributes") or {}).items():
        if value not in (None, "", [], {}):
            candidates.append({"field": f"attributes.{key}", "page_value": value})
    price = page_facts.get("page_price_cent")
    if isinstance(price, int) and not isinstance(price, bool) and price >= 0:
        candidates.append({"field": "price_cent", "page_value": price, "page_field": "page_price_cent"})
    return candidates


def align_row(row: dict[str, Any]) -> dict[str, Any]:
    asr = str(row.get("asr") or "")
    ocr = [str(x) for x in (row.get("ocr") or [])]
    combined = "\n".join([asr, *ocr])
    compact_evidence = compact(combined)
    observed_prices = extract_observed_prices(combined)
    observed_colors = sorted(color for color in COLORS if compact(color) in compact_evidence)
    page_facts = row.get("offline_page_facts") or {}
    aligned = []
    for candidate in candidate_facts(page_facts):
        field = candidate["field"]
        value = candidate["page_value"]
        status = "unobserved_text"
        observed_value = None
        if field == "price_cent":
            if value in observed_prices:
                status = "supported_text"
                observed_value = value
            elif observed_prices:
                status = "conflict_text"
                observed_value = observed_prices
        elif field == "color":
            if compact(value) and compact(value) in compact_evidence:
                status = "supported_text"
                observed_value = value
            elif observed_colors:
                status = "conflict_text"
                observed_value = observed_colors
        else:
            tokens = [value] if not isinstance(value, list) else value
            normalized = [compact(x) for x in tokens if compact(x)]
            if normalized and any(x in compact_evidence for x in normalized):
                status = "supported_text"
                observed_value = value
        aligned.append({
            **candidate,
            "text_alignment": status,
            "observed_value": observed_value,
            "visual_alignment": "unknown_visual",
        })

    result = dict(row)
    result["weak_supervision"] = {
        "source": "product_page",
        "page_candidates": aligned,
        "page_reference": {
            "title": str(page_facts.get("title") or "")[:500],
            "description_excerpt": str(page_facts.get("description") or "")[:2000],
            "detail_image_ocr_excerpt": [
                str(x)[:300] for x in (page_facts.get("detail_image_ocr") or [])[:100]
            ],
        },
        "observed_prices_cent": observed_prices,
        "observed_colors": observed_colors,
    }
    # Keep only a bounded page excerpt for the teacher and audit layer. The raw
    # page facts are removed here and all weak supervision is stripped at export.
    result.pop("offline_page_facts", None)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = [align_row(row) for row in read_jsonl(args.input)]
    write_jsonl(args.output, rows)
    print("aligned windows", len(rows))


if __name__ == "__main__":
    main()
