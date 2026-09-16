#!/usr/bin/env bash
set -euo pipefail

INPUT="${1:?usage: scripts/create_demo_stream.sh path/to/demo.mp4 [room_id]}"
ROOM_ID="${2:-20001}"

ffmpeg -re -stream_loop -1 -i "$INPUT" \
  -c:v libx264 -preset veryfast -tune zerolatency \
  -c:a aac -ar 44100 -b:a 128k \
  -f flv "rtmp://localhost:1935/live/${ROOM_ID}"
