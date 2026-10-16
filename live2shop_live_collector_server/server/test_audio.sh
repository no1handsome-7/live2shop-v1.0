#!/usr/bin/env bash
set -euo pipefail
SERVER_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SERVER_DIR}/lib.sh"

OUT="${1:-/tmp/live2shop_audio_test.wav}"
echo "In VNC, start a page that is definitely playing sound."
echo "Recording 10 seconds from live2shop.monitor -> ${OUT}"
compose exec -T --user pwuser collector \
  ffmpeg -hide_banner -loglevel warning -y \
    -f pulse -i live2shop.monitor -t 10 "${OUT}"

echo
echo "Copy to host:"
echo "  docker cp live2shop-capture:${OUT} ./audio_test.wav"
