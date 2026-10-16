#!/usr/bin/env bash
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "${SERVER_DIR}/.." && pwd)"
COMPOSE_FILE="${PACKAGE_DIR}/docker-compose.yml"

if [[ -f "${SERVER_DIR}/server.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${SERVER_DIR}/server.env"
  set +a
fi

export LIVE2SHOP_ROOT="${LIVE2SHOP_ROOT:-$(cd "${PACKAGE_DIR}/.." && pwd)}"
export COLLECTOR_UID="${COLLECTOR_UID:-$(id -u)}"
export COLLECTOR_GID="${COLLECTOR_GID:-$(id -g)}"
export SCREEN_WIDTH="${SCREEN_WIDTH:-1280}"
export SCREEN_HEIGHT="${SCREEN_HEIGHT:-720}"
export SCREEN_DEPTH="${SCREEN_DEPTH:-24}"
export VNC_HOST_PORT="${VNC_HOST_PORT:-5901}"
export TZ="${TZ:-Asia/Shanghai}"
export LIVE2SHOP_DISABLE_BROWSER_SANDBOX="${LIVE2SHOP_DISABLE_BROWSER_SANDBOX:-1}"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose -f "${COMPOSE_FILE}")
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose -f "${COMPOSE_FILE}")
else
  echo "[ERROR] Docker Compose not found."
  exit 2
fi

compose() {
  "${COMPOSE[@]}" "$@"
}
