#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC1091
source "$(cd "$(dirname "$0")" && pwd)/lib.sh"
compose ps
echo
compose exec -T collector sh -lc '
  echo "DISPLAY=$DISPLAY"
  echo "PULSE_SERVER=$PULSE_SERVER"
  test -f /tmp/live2shop-ready && echo "READY=yes" || echo "READY=no"
  pgrep -a Xvfb || true
  pgrep -a x11vnc || true
'
