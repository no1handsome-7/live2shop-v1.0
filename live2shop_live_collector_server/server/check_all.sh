#!/usr/bin/env bash
set -euo pipefail
SERVER_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SERVER_DIR}/lib.sh"

"${SERVER_DIR}/check_host.sh"
echo
compose exec -T collector test -f /tmp/live2shop-ready >/dev/null 2>&1 || {
  echo "[FAIL] collector container is not ready. Run ${SERVER_DIR}/start_server.sh"
  exit 1
}
compose exec -T --user pwuser collector /opt/live2shop_collector/server/check_container.sh
