"""
Select ShareGPT4Video by whole zip archives so a 5k-video subset does NOT
accidentally require downloading all upstream archives.

Official metadata fields include:
  video_path, captions, zip_folder

Example:
  video_path = pixabay/<id>.mp4
  zip_folder = pixabay_videos_9.zip

The official repository stores the archive as:
  zip_folder/<video source>/<zip_folder>
"""
from __future__ import annotations

import argparse
import json
import zipfile
from collections import defaultdict
from pathlib import Path

from common import write_jsonl


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


ap = argparse.ArgumentParser()
ap.add_argument("--metadata", required=True)
ap.add_argument("--limit", type=int, default=5000)
ap.add_argument("--output-metadata", required=True)
ap.add_argument("--archive-dir", default="data/raw/sharegpt4video/archives")
ap.add_argument("--video-root", default="data/raw/sharegpt4video/videos")
ap.add_argument("--download", action="store_true")
args = ap.parse_args()

groups = defaultdict(list)
for row in load_jsonl(args.metadata):
    archive = row.get("zip_folder")
    video_path = row.get("video_path")
    if archive and video_path:
        groups[archive].append(row)

chosen = []
archives = []
for archive, rows in sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True):
    source = str(rows[0]["video_path"]).split("/", 1)[0]
    archives.append((archive, source))
    chosen.extend(rows)
    if len(chosen) >= args.limit:
        break

chosen = chosen[: args.limit]
write_jsonl(args.output_metadata, chosen)

print("selected samples:", len(chosen))
print("required archives:", len(archives))
for archive, source in archives:
    print(" ", f"zip_folder/{source}/{archive}")

if args.download:
    from huggingface_hub import hf_hub_download

    archive_dir = Path(args.archive_dir)
    video_root = Path(args.video_root)
    archive_dir.mkdir(parents=True, exist_ok=True)
    video_root.mkdir(parents=True, exist_ok=True)

    for archive, source in archives:
        remote = f"zip_folder/{source}/{archive}"
        print("downloading", remote)
        local = hf_hub_download(
            repo_id="ShareGPT4Video/ShareGPT4Video",
            repo_type="dataset",
            filename=remote,
            local_dir=str(archive_dir),
        )
        print("extracting", local)
        with zipfile.ZipFile(local) as zf:
            zf.extractall(video_root)

    print("videos extracted under", video_root)
