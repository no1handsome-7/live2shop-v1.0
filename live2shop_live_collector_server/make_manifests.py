#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

PLATFORMS = ["douyin", "xiaohongshu", "taobao"]

def load_room(d: Path):
    meta_p = d / "room.json"
    video_p = d / "live.mp4"
    catalog_p = d / "catalog.json"
    if not (meta_p.exists() and video_p.exists() and catalog_p.exists()):
        return None, "missing room.json/live.mp4/catalog.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    try:
        catalog = json.loads(catalog_p.read_text(encoding="utf-8"))
    except Exception as e:
        return None, f"bad catalog: {e}"
    if not isinstance(catalog, list):
        return None, "catalog is not JSON array"
    if len(catalog) == 0:
        return None, "empty catalog"
    if meta.get("status") != "complete":
        return None, f"status={meta.get('status')}"
    return {
        "room_id": meta["room_id"],
        "platform": meta["platform"],
        "category": meta.get("category"),
        "video": str(video_p.resolve()),
        "catalog": str(catalog_p.resolve()),
        "source": meta["platform"],
    }, None

def split_platform(rows, rng):
    rows = list(rows)
    rng.shuffle(rows)
    n = len(rows)
    if n >= 40:
        # Target design: 40 per platform -> 30/5/5.
        n_train, n_val = 30, 5
    else:
        n_train = int(round(n * 0.75))
        n_val = int(round(n * 0.125))
        if n_train + n_val >= n and n >= 3:
            n_val = max(1, n - n_train - 1)
    return {
        "train": rows[:n_train],
        "val": rows[n_train:n_train+n_val],
        "test": rows[n_train+n_val:],
    }

def write_jsonl(path: Path, rows):
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            out = {
                "video": r["video"],
                "room_id": r["room_id"],
                "catalog": r["catalog"],
                "source": r["source"],
            }
            f.write(json.dumps(out, ensure_ascii=False) + "\n")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/self/raw_live")
    ap.add_argument("--out-dir", default="data/self/manifests")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    root = Path(args.root)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    groups = defaultdict(list)
    rejected = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        room, err = load_room(d)
        if err:
            rejected.append((d.name, err))
            continue
        groups[room["platform"]].append(room)

    splits = {"train": [], "val": [], "test": []}
    for platform in PLATFORMS:
        part = split_platform(groups.get(platform, []), rng)
        for s in splits:
            splits[s].extend(part[s])

    # Deterministic order in output.
    for s in splits:
        splits[s].sort(key=lambda r: (r["platform"], r["room_id"]))
        write_jsonl(out / f"{s}.jsonl", splits[s])

    print("Manifest counts:")
    for s in ("train", "val", "test"):
        print(f"  {s}: {len(splits[s])}")
        by_p = defaultdict(int)
        for r in splits[s]:
            by_p[r["platform"]] += 1
        print("   ", dict(by_p))

    if rejected:
        print("\nRejected rooms:")
        for name, why in rejected:
            print(" ", name, "->", why)

if __name__ == "__main__":
    main()
