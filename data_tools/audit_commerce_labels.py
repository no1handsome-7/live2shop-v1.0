#!/usr/bin/env python3
"""Audit CommerceEvent v2 teacher labels and route uncertain rows to review."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from common import read_jsonl, write_jsonl

REQUIRED = {
    "schema_version", "product_visible", "category", "brand", "style_tags",
    "color", "attributes", "price_cent", "promotion_tags", "evidence",
}
FORBIDDEN_TARGET_KEYS = {"active_item_id", "confidence", "page_price_cent", "source_product_id"}


def nested_value(target: dict[str, Any], field: str):
    value: Any = target
    for part in field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def compact(value: Any) -> str:
    return "".join(char.lower() for char in str(value or "") if char.isalnum())


def scalar_target_values(target: dict[str, Any]):
    for field in ("category", "brand", "color"):
        value = target.get(field)
        if value not in (None, ""):
            yield field, value
    for field in ("style_tags", "promotion_tags"):
        for value in target.get(field) or []:
            yield field, value
    for key, value in (target.get("attributes") or {}).items():
        if value not in (None, "", [], {}):
            yield f"attributes.{key}", value


def evidence_quote_reasons(row: dict[str, Any], target: dict[str, Any]) -> list[str]:
    reasons = []
    evidence = target.get("evidence")
    if not isinstance(evidence, dict):
        return ["bad_evidence"]
    context = row.get("context") or {}
    sources = {
        "asr": str(context.get("asr") or ""),
        "ocr": "\n".join(str(x) for x in (context.get("ocr") or [])),
    }
    for source in ("asr", "ocr", "visual"):
        entries = evidence.get(source)
        if not isinstance(entries, list) or any(not isinstance(x, str) for x in entries):
            reasons.append(f"bad_evidence_{source}")
            continue
        if source in sources:
            for quote in entries:
                if quote.strip() and quote.strip() not in sources[source]:
                    reasons.append(f"ungrounded_{source}_quote")
                    break
    return reasons


def reasons(row: dict[str, Any]) -> list[str]:
    target = row.get("target_json")
    out = []
    if not isinstance(target, dict):
        return ["parse_error"]
    missing = REQUIRED - set(target)
    if missing:
        out.append("missing:" + ",".join(sorted(missing)))
    forbidden = FORBIDDEN_TARGET_KEYS & set(target)
    if forbidden:
        out.append("forbidden_target_key:" + ",".join(sorted(forbidden)))
    unknown = set(target) - REQUIRED
    if unknown:
        out.append("unknown_target_key:" + ",".join(sorted(unknown)))
    if target.get("schema_version") != "commerce_event.v2":
        out.append("wrong_schema_version")
    if not isinstance(target.get("product_visible"), bool):
        out.append("bad_product_visible")
    for field in ("category", "brand", "color"):
        if target.get(field) is not None and not isinstance(target.get(field), str):
            out.append(f"bad_{field}")
    for field in ("style_tags", "promotion_tags"):
        if not isinstance(target.get(field), list):
            out.append(f"bad_{field}")
    if not isinstance(target.get("attributes"), dict):
        out.append("bad_attributes")
    price = target.get("price_cent")
    if price is not None and (isinstance(price, bool) or not isinstance(price, int) or price < 0):
        out.append("bad_price")
    out.extend(evidence_quote_reasons(row, target))

    annotation = row.get("annotation") or {}
    if annotation.get("teacher_missing_fields"):
        out.append("teacher_missing_fields:" + ",".join(annotation["teacher_missing_fields"]))
    if annotation.get("forbidden_fields_removed"):
        out.append("teacher_emitted_forbidden_fields")
    confidence = annotation.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        out.append("bad_annotation_confidence")
    elif confidence < 0.65:
        out.append("low_annotation_confidence")

    weak = (row.get("annotation_context") or {}).get("weak_supervision") or {}
    candidates = weak.get("page_candidates") or []
    decisions = annotation.get("page_fact_decisions") or []
    decisions_by_field = {
        x.get("field"): x for x in decisions if isinstance(x, dict) and x.get("field")
    }
    observed_prices = weak.get("observed_prices_cent") or []
    if price is not None and observed_prices and price not in observed_prices:
        out.append("target_price_not_in_asr_or_ocr")
    if price is not None and not observed_prices:
        out.append("price_requires_visual_review")

    page_reference = weak.get("page_reference") or {}
    page_reference_text = compact(json.dumps(page_reference, ensure_ascii=False))
    observed_text = compact(
        str((row.get("context") or {}).get("asr") or "") + " " +
        " ".join(str(x) for x in ((row.get("context") or {}).get("ocr") or []))
    )
    for field, value in scalar_target_values(target):
        normalized = compact(value)
        if len(normalized) >= 2 and normalized in page_reference_text and normalized not in observed_text:
            out.append(f"page_reference_only_or_visual_only:{field}")

    for candidate in candidates:
        if not isinstance(candidate, dict) or not candidate.get("field"):
            out.append("bad_page_candidate")
            continue
        field = candidate["field"]
        status = candidate.get("text_alignment")
        target_value = nested_value(target, field)
        page_value = candidate.get("page_value")
        decision = decisions_by_field.get(field) or {}
        if field not in decisions_by_field:
            out.append(f"missing_page_decision:{field}")
        if decision.get("decision") == "conflict":
            observed = candidate.get("observed_value")
            expected_live_price = field == "price_cent" and isinstance(observed, list) and target_value in observed
            if not expected_live_price:
                out.append(f"teacher_page_conflict:{field}")
        if status == "conflict_text":
            # A differing live price is expected and is accepted only if the
            # target uses the observed video price. Other conflicts need review.
            if field == "price_cent" and target_value in (candidate.get("observed_value") or []):
                pass
            else:
                out.append(f"page_video_conflict:{field}")
        elif status == "unobserved_text" and target_value == page_value and target_value not in (None, "", [], {}):
            out.append(f"page_only_or_visual_only_field:{field}")

    prompt = str(row.get("prompt") or "")
    if "候选商品=" in prompt or "页面候选事实（弱监督" in prompt or "room_candidates" in prompt:
        out.append("student_prompt_contains_weak_supervision")
    return list(dict.fromkeys(out))


def leakage_reasons(rows: list[dict[str, Any]]) -> dict[int, list[str]]:
    by_group = defaultdict(set)
    by_video = defaultdict(set)
    for row in rows:
        meta = row.get("meta") or {}
        split = meta.get("split")
        if meta.get("product_group_key") and split:
            by_group[str(meta["product_group_key"])].add(str(split))
        if meta.get("video_sha256") and split:
            by_video[str(meta["video_sha256"])].add(str(split))
    bad_groups = {key for key, values in by_group.items() if len(values) > 1}
    bad_videos = {key for key, values in by_video.items() if len(values) > 1}
    result = defaultdict(list)
    for index, row in enumerate(rows):
        meta = row.get("meta") or {}
        if str(meta.get("product_group_key")) in bad_groups:
            result[index].append("product_group_split_leakage")
        if str(meta.get("video_sha256")) in bad_videos:
            result[index].append("video_split_leakage")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    rows = list(read_jsonl(args.input))
    leaks = leakage_reasons(rows)
    accepted = []
    review = []
    for index, row in enumerate(rows):
        row_reasons = list(dict.fromkeys(reasons(row) + leaks.get(index, [])))
        row.setdefault("meta", {})["audit_status"] = "needs_review" if row_reasons else "accepted"
        row["meta"]["audit_reasons"] = row_reasons
        if row_reasons:
            review.append((row, row_reasons))
        else:
            accepted.append(row)

    output_dir = Path(args.out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "accepted.jsonl", accepted)
    write_jsonl(output_dir / "needs_review.jsonl", [row for row, _ in review])
    with (output_dir / "review.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["media", "reason", "target_json", "corrected_target_json"],
        )
        writer.writeheader()
        for row, row_reasons in review:
            writer.writerow({
                "media": row["media"],
                "reason": ";".join(row_reasons),
                "target_json": json.dumps(row.get("target_json"), ensure_ascii=False),
                "corrected_target_json": "",
            })
    summary = {"total": len(rows), "accepted": len(accepted), "needs_review": len(review)}
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("accepted", len(accepted), "review", len(review))


if __name__ == "__main__":
    main()
