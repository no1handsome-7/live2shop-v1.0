#!/usr/bin/env python3
"""Physically strip annotation/page data and emit leak-resistant P2 JSONL."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

from common import read_jsonl, write_jsonl

META_ALLOWLIST = {
    "source", "teacher_model", "commerce_event_schema", "source_kind",
    "source_platform", "source_product_id", "product_group_key",
    "video_sha256", "split", "human_reviewed", "audit_status",
}
TARGET_KEYS = {
    "schema_version", "product_visible", "category", "brand", "style_tags",
    "color", "attributes", "price_cent", "promotion_tags", "evidence",
}


def export_row(row):
    target = row.get("target_json")
    if not isinstance(target, dict):
        raise ValueError(f"invalid target_json for {row.get('media')}")
    forbidden = {"active_item_id", "confidence", "page_price_cent", "source_product_id"} & set(target)
    if forbidden:
        raise ValueError(f"forbidden P2 target fields {sorted(forbidden)} in {row.get('media')}")
    if set(target) != TARGET_KEYS:
        raise ValueError(f"unexpected P2 target shape in {row.get('media')}")
    if target.get("schema_version") != "commerce_event.v2":
        raise ValueError(f"non-v2 target in {row.get('media')}")
    if (row.get("meta") or {}).get("audit_status") not in {"accepted", "human_reviewed"}:
        raise ValueError(f"row was not accepted/reviewed: {row.get('media')}")
    target_text = json.dumps(target, ensure_ascii=False, sort_keys=True)
    meta = {key: value for key, value in (row.get("meta") or {}).items() if key in META_ALLOWLIST}
    return {
        "media": row["media"],
        "media_type": row.get("media_type", "video"),
        "prompt": row["prompt"],
        "target": target_text,
        "target_json": target,
        "retrieval_text": target_text,
        "meta": meta,
    }


def validate_splits(rows):
    groups = defaultdict(set)
    videos = defaultdict(set)
    for row in rows:
        meta = row.get("meta") or {}
        split = meta.get("split")
        if meta.get("product_group_key") and split:
            groups[meta["product_group_key"]].add(split)
        if meta.get("video_sha256") and split:
            videos[meta["video_sha256"]].add(split)
    bad_groups = [key for key, values in groups.items() if len(values) > 1]
    bad_videos = [key for key, values in videos.items() if len(values) > 1]
    if bad_groups or bad_videos:
        raise ValueError(
            f"split leakage: product_groups={len(bad_groups)}, videos={len(bad_videos)}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--split", choices=("train", "val", "test"))
    args = parser.parse_args()
    rows = [export_row(row) for path in args.input for row in read_jsonl(path)]
    validate_splits(rows)
    if args.split:
        rows = [row for row in rows if row.get("meta", {}).get("split") == args.split]
    write_jsonl(args.output, rows)
    print("student samples", len(rows))


if __name__ == "__main__":
    main()
