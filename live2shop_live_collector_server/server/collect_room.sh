#!/usr/bin/env bash
set -euo pipefail
SERVER_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SERVER_DIR}/lib.sh"

compose exec -T collector test -f /tmp/live2shop-ready >/dev/null 2>&1 || {
  echo "[ERROR] Virtual desktop is not ready. Run: ${SERVER_DIR}/start_server.sh"
  exit 1
}

if [[ "$#" -eq 0 ]]; then
  cat <<'EOF'
Usage:
  ./server/collect_room.sh \
    --room-id douyin_001 \
    --url 'https://...' \
    --category '服饰鞋包' \
    --minutes 15 \
    [--viewer-count 86]

Automatically added:
  --root data/self/raw_live
  --profile-root profiles
  --display :99.0
  --size SCREEN_WIDTHxSCREEN_HEIGHT
  --audio-source live2shop.monitor
  --browser-channel chromium
EOF
  exit 0
fi

exec "${COMPOSE[@]}" exec --user pwuser collector \
  python /opt/live2shop_collector/collect_live.py \
    --root data/self/raw_live \
    --profile-root profiles \
    --display :99.0 \
    --size "${SCREEN_WIDTH}x${SCREEN_HEIGHT}" \
    --x 0 \
    --y 0 \
    --audio-source live2shop.monitor \
    --browser-channel chromium \
    "$@"
