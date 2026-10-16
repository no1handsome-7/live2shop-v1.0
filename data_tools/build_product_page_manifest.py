#!/usr/bin/env python3
"""Deduplicate collected product videos and assign product-grouped splits."""
from __future__ import annotations

import argparse
from collections import defaultdict

from common import read_jsonl, stable_split, write_jsonl


def build_manifest(rows, val_pct: int, test_pct: int):
    if val_pct < 0 or test_pct < 0 or val_pct + test_pct >= 100:
        raise ValueError("val_pct and test_pct must be >= 0 and sum to less than 100")
    by_hash = defaultdict(list)
    rejected = []
    for row in rows:
        video = row.get("selected_video") or {}
        digest = video.get("sha256")
        path = video.get("local_path")
        if row.get("status") != "collected" or not digest or not path:
            rejected.append({
                "source_url": row.get("source_url"),
                "status": row.get("status"),
                "reason": row.get("review_reason") or row.get("error") or "missing_downloaded_video",
            })
            continue
        by_hash[digest].append(row)

    manifest = []
    for digest, copies in sorted(by_hash.items()):
        groups = sorted({str(x["product_group_key"]) for x in copies})
        if len(groups) != 1:
            rejected.append({
                "video_sha256": digest,
                "reason": "same_video_assigned_to_multiple_products",
                "product_group_keys": groups,
            })
            continue
        row = copies[0]
        group = groups[0]
        manifest.append({
            "video": row["selected_video"]["local_path"],
            "video_sha256": digest,
            "source_kind": "product_page_clip",
            "source": row.get("platform"),
            "source_product_id": row.get("source_product_id"),
            "product_group_key": group,
            "split": stable_split(group, val_pct=val_pct, test_pct=test_pct),
            "page_facts": row.get("page_facts") or {},
            "provenance": {
                "page_record_schema": row.get("schema_version"),
                "source_url": row.get("source_url"),
                "canonical_url": row.get("canonical_url"),
                "collected_at": row.get("collected_at"),
                "snapshot": row.get("snapshot"),
                "rights_basis": row.get("rights_basis"),
            },
        })
    return manifest, rejected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--rejected-output", required=True)
    parser.add_argument("--val-pct", type=int, default=5)
    parser.add_argument("--test-pct", type=int, default=5)
    args = parser.parse_args()
    manifest, rejected = build_manifest(read_jsonl(args.input), args.val_pct, args.test_pct)
    write_jsonl(args.output, manifest)
    write_jsonl(args.rejected_output, rejected)
    print("manifest", len(manifest), "rejected", len(rejected))


if __name__ == "__main__":
    main()
