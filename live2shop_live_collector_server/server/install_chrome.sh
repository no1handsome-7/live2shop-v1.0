#!/usr/bin/env bash
set -euo pipefail
SERVER_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SERVER_DIR}/lib.sh"

echo "Installing branded Google Chrome inside the running container..."
echo "Use this only if bundled Chromium cannot play a platform correctly."
compose exec -T --user root collector python -m playwright install chrome
echo
echo "Installed. To use it, run collect_live.py with: --browser-channel chrome"
