#!/usr/bin/env python3
from __future__ import annotations
import argparse
import csv
import random
from pathlib import Path

PLATFORMS = ["douyin", "xiaohongshu", "taobao"]

CATEGORY_QUOTAS = [
    ("服饰鞋包", 25),
    ("美妆护肤", 15),
    ("食品饮料", 15),
    ("家居用品", 15),
    ("3C电子", 15),
    ("家电", 10),
    ("母婴", 5),
    ("运动户外", 5),
    ("珠宝饰品", 5),
    ("其他", 10),
]

def build_rows(seed: int = 42):
    # 连续轮转平台，保证总计 120 时恰好每个平台 40 间，
    # 同时让每个品类尽量均匀分布在三个平台。
    rows = []
    platform_counter = {p: 0 for p in PLATFORMS}
    cursor = 0
    for category, count in CATEGORY_QUOTAS:
        for _ in range(count):
            platform = PLATFORMS[cursor % len(PLATFORMS)]
            cursor += 1
            platform_counter[platform] += 1
            room_id = f"{platform}_{platform_counter[platform]:03d}"
            rows.append({
                "room_id": room_id,
                "platform": platform,
                "category": category,
                "target_minutes": 15,
                "max_displayed_viewers": 300,
                "target_blank_ratio": 0.30,
                "room_url": "",
                "observed_viewers": "",
                "status": "todo",
                "notes": "",
            })
    # 为了避免采集顺序被品类块影响，随机打散执行顺序；
    # room_id 本身保持稳定。
    rng = random.Random(seed)
    rng.shuffle(rows)
    for i, r in enumerate(rows, 1):
        r["collection_order"] = i
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/self/collection_plan.csv")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rows = build_rows(args.seed)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    fields = [
        "collection_order", "room_id", "platform", "category",
        "target_minutes", "max_displayed_viewers", "target_blank_ratio",
        "room_url", "observed_viewers", "status", "notes",
    ]
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rooms -> {out}")
    counts = {}
    for r in rows:
        counts[r["platform"]] = counts.get(r["platform"], 0) + 1
    print("Platform counts:", counts)

if __name__ == "__main__":
    main()
