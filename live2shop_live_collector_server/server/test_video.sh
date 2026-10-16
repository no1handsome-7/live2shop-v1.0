#!/usr/bin/env bash
set -euo pipefail
SERVER_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SERVER_DIR}/lib.sh"

OUT="${1:-/tmp/live2shop_video_test.mp4}"
echo "Recording 10 seconds from virtual desktop -> ${OUT}"
compose exec -T --user pwuser collector \
  ffmpeg -hide_banner -loglevel warning -y \
    -f x11grab \
    -framerate 30 \
    -video_size "${SCREEN_WIDTH}x${SCREEN_HEIGHT}" \
    -i :99.0+0,0 \
    -t 10 \
    -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p \
    "${OUT}"

echo
echo "Copy to host:"
echo "  docker cp live2shop-capture:${OUT} ./video_test.mp4"
