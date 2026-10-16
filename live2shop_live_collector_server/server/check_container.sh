#!/usr/bin/env bash
set -euo pipefail

DISPLAY="${DISPLAY:-:99}"
SCREEN_WIDTH="${SCREEN_WIDTH:-1280}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-720}"

ok() { echo "[OK]   $*"; }
bad() { echo "[FAIL] $*"; exit 1; }

echo "=== Container self-check ==="

python - <<'PY'
from importlib.metadata import version
v = version("playwright")
print("[INFO] Python Playwright:", v)
assert v == "1.63.0", v
PY
ok "Playwright package matches image version 1.63.0"

command -v ffmpeg >/dev/null || bad "ffmpeg missing"
ok "$(ffmpeg -version | head -1)"

xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 || bad "Xvfb display unavailable"
dims="$(xdpyinfo -display "${DISPLAY}" | awk '/dimensions:/ {print $2; exit}')"
ok "Xvfb display ${DISPLAY}, dimensions=${dims}"

pactl info >/dev/null 2>&1 || bad "PulseAudio unavailable"
pactl list short sources | awk '{print $2}' | grep -qx 'live2shop.monitor' || bad "live2shop.monitor missing"
ok "PulseAudio source live2shop.monitor exists"

pgrep -x x11vnc >/dev/null || bad "x11vnc is not running"
ok "x11vnc is running"

rm -f /tmp/live2shop-x11-test.jpg
ffmpeg -hide_banner -loglevel error -y \
  -f x11grab \
  -video_size "${SCREEN_WIDTH}x${SCREEN_HEIGHT}" \
  -i "${DISPLAY}.0+0,0" \
  -frames:v 1 \
  /tmp/live2shop-x11-test.jpg
test -s /tmp/live2shop-x11-test.jpg || bad "FFmpeg X11 capture smoke test failed"
ok "FFmpeg can capture the virtual desktop"

python - <<'PY'
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

args = ["--window-size=900,600", "--window-position=20,20", "--autoplay-policy=no-user-gesture-required"]
if os.environ.get("LIVE2SHOP_DISABLE_BROWSER_SANDBOX", "1") == "1":
    args += ["--no-sandbox", "--disable-setuid-sandbox"]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, args=args)
    page = browser.new_page(viewport={"width": 800, "height": 500})
    page.set_content("<html><body><h1>Live2Shop server capture smoke test</h1></body></html>")
    page.screenshot(path="/tmp/live2shop-browser-smoke.png")
    browser.close()

assert Path("/tmp/live2shop-browser-smoke.png").stat().st_size > 0
print("[OK]   Headed Chromium launched on Xvfb and rendered a screenshot")
PY

echo
echo "Container self-check PASSED."
echo "Manual checks still required: VNC view, platform login, live video playback and browser audio."
