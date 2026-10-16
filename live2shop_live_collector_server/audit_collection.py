#!/usr/bin/env python3
from __future__ import annotations
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def blank_ratio(timeline: Path):
    if not timeline.exists():
        return None
    total = blank = 0
    with timeline.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                dur = max(0, int(r["end_ms"]) - int(r["start_ms"]))
            except Exception:
                continue
            if r.get("state") == "unknown":
                continue
            total += dur
            if r.get("state") == "blank":
                blank += dur
    return blank / total if total else None

def event_counts(markers: Path):
    c = Counter()
    if not markers.exists():
        return c
    for line in markers.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") == "event":
            c[r.get("event_type", "unknown")] += 1
    return c

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/self/raw_live")
    args = ap.parse_args()
    root = Path(args.root)

    platform = Counter()
    category = Counter()
    events = Counter()
    errors = []
    durations = []
    blank_ratios = []
    catalog_sizes = []

    room_dirs = [p for p in root.iterdir() if p.is_dir()] if root.exists() else []
    for d in sorted(room_dirs):
        try:
            meta = read_json(d / "room.json")
        except Exception as e:
            errors.append((d.name, f"room.json: {e}"))
            continue
        platform[meta.get("platform", "unknown")] += 1
        category[meta.get("category", "unknown")] += 1
        durations.append(float(meta.get("actual_duration_sec") or 0))
        if meta.get("status") != "complete":
            errors.append((d.name, f"status={meta.get('status')}"))
        if not (d / "live.mp4").exists():
            errors.append((d.name, "missing live.mp4"))
        try:
            cat = read_json(d / "catalog.json")
            if not isinstance(cat, list):
                errors.append((d.name, "catalog.json is not array"))
            else:
                catalog_sizes.append(len(cat))
                if len(cat) == 0:
                    errors.append((d.name, "empty catalog"))
        except Exception as e:
            errors.append((d.name, f"catalog: {e}"))

        br = blank_ratio(d / "timeline.csv")
        if br is not None:
            blank_ratios.append(br)
        events.update(event_counts(d / "markers.jsonl"))

    print("=== Live2Shop collection audit ===")
    print("rooms:", len(room_dirs))
    print("platforms:", dict(platform))
    print("categories:", dict(category))
    if durations:
        print("total_hours:", round(sum(durations)/3600, 3))
        print("avg_minutes:", round(sum(durations)/len(durations)/60, 3))
    if catalog_sizes:
        print("avg_catalog_items:", round(sum(catalog_sizes)/len(catalog_sizes), 2))
    if blank_ratios:
        avg = sum(blank_ratios)/len(blank_ratios)
        print("avg_marked_blank_ratio:", round(avg, 4),
              "(target for final P2 distribution: roughly 0.25-0.35)")
    print("event_markers:", dict(events))
    print("errors:", len(errors))
    for x in errors[:100]:
        print("  ", x[0], "->", x[1])

if __name__ == "__main__":
    main()
