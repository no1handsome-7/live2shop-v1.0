#!/usr/bin/env bash
set -euo pipefail
SERVER_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SERVER_DIR}/lib.sh"

mkdir -p \
  "${LIVE2SHOP_ROOT}/data/self/raw_live" \
  "${LIVE2SHOP_ROOT}/profiles" \
  "${LIVE2SHOP_ROOT}/logs/capture"

"${SERVER_DIR}/check_host.sh"

echo
echo "=== Building/starting virtual capture desktop ==="
compose up -d --build

echo
echo "Waiting for Xvfb + PulseAudio + VNC ..."
ready=0
for _ in $(seq 1 90); do
  if compose exec -T collector test -f /tmp/live2shop-ready >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if [[ "${ready}" != "1" ]]; then
  echo "[ERROR] Virtual desktop did not become ready."
  compose logs --tail=200 collector || true
  exit 1
fi

echo
echo "=== Running container self-check ==="
compose exec -T --user pwuser collector /opt/live2shop_collector/server/check_container.sh

echo
echo "============================================================"
echo "Live2Shop virtual capture desktop is READY."
echo "============================================================"
echo
echo "VNC is exposed only on server loopback: 127.0.0.1:${VNC_HOST_PORT}"
echo
echo "On YOUR LOCAL COMPUTER:"
echo "  ssh -N -L 5901:127.0.0.1:${VNC_HOST_PORT} <server-user>@<server-host>"
echo
echo "Then open VNC Viewer -> 127.0.0.1:5901"
echo
echo "Collect a room:"
echo "  ${SERVER_DIR}/collect_room.sh --room-id douyin_001 --url 'https://...' --category '服饰鞋包' --minutes 15"
