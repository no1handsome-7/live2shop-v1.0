#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"

fail=0
ok() { echo "[OK]   $*"; }
warn() { echo "[WARN] $*"; }
bad() { echo "[FAIL] $*"; fail=1; }

echo "=== Host preflight ==="
echo "Live2Shop root: ${LIVE2SHOP_ROOT}"
echo "Host UID:GID: ${COLLECTOR_UID}:${COLLECTOR_GID}"

command -v docker >/dev/null 2>&1 && ok "$(docker --version)" || bad "docker command not found"
docker info >/dev/null 2>&1 && ok "Docker daemon reachable" || bad "Docker daemon is not reachable by current user"

if docker compose version >/dev/null 2>&1; then
  ok "$(docker compose version)"
elif command -v docker-compose >/dev/null 2>&1; then
  ok "$(docker-compose --version)"
else
  bad "Docker Compose not found"
fi

[[ -d "${LIVE2SHOP_ROOT}" && -w "${LIVE2SHOP_ROOT}" ]] \
  && ok "Live2Shop root is writable" \
  || bad "Live2Shop root is missing or not writable: ${LIVE2SHOP_ROOT}"

avail_kb="$(df -Pk "${LIVE2SHOP_ROOT}" | awk 'NR==2 {print $4}')"
avail_gb=$(( avail_kb / 1024 / 1024 ))
echo "[INFO] Free disk: ~${avail_gb} GiB"
if (( avail_gb < 50 )); then
  warn "Less than 50 GiB free."
elif (( avail_gb < 100 )); then
  warn "Less than 100 GiB free; monitor disk usage."
else
  ok "Disk headroom is comfortable for the initial collection"
fi

if ss -ltn 2>/dev/null | grep -q "127.0.0.1:${VNC_HOST_PORT} "; then
  warn "Host port ${VNC_HOST_PORT} is already in use. Change VNC_HOST_PORT in server/server.env."
else
  ok "VNC host port ${VNC_HOST_PORT} appears available"
fi

echo "[INFO] Kernel: $(uname -srmo)"
echo "[INFO] GPU/NVIDIA is not required for capture."

(( fail == 0 )) || exit 1
