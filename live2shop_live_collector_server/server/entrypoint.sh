#!/usr/bin/env bash
set -euo pipefail

DISPLAY="${DISPLAY:-:99}"
SCREEN_WIDTH="${SCREEN_WIDTH:-1280}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-720}"
SCREEN_DEPTH="${SCREEN_DEPTH:-24}"
COLLECTOR_UID="${COLLECTOR_UID:-1000}"
COLLECTOR_GID="${COLLECTOR_GID:-1000}"
COLLECTOR_USER="${COLLECTOR_USER:-pwuser}"
XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/live2shop-runtime}"
PULSE_SERVER="${PULSE_SERVER:-unix:${XDG_RUNTIME_DIR}/pulse/native}"

export DISPLAY SCREEN_WIDTH SCREEN_HEIGHT SCREEN_DEPTH XDG_RUNTIME_DIR PULSE_SERVER

log() { printf '[desktop] %s\n' "$*"; }

current_uid="$(id -u "${COLLECTOR_USER}")"
current_gid="$(id -g "${COLLECTOR_USER}")"

if [[ "${current_gid}" != "${COLLECTOR_GID}" ]]; then
  if getent group "${COLLECTOR_GID}" >/dev/null 2>&1; then
    existing_group="$(getent group "${COLLECTOR_GID}" | cut -d: -f1)"
    usermod -g "${existing_group}" "${COLLECTOR_USER}"
  else
    groupmod -g "${COLLECTOR_GID}" "$(id -gn "${COLLECTOR_USER}")"
  fi
fi

if [[ "${current_uid}" != "${COLLECTOR_UID}" ]]; then
  usermod -u "${COLLECTOR_UID}" "${COLLECTOR_USER}"
fi

HOME_DIR="$(getent passwd "${COLLECTOR_USER}" | cut -d: -f6)"
GROUP_NAME="$(id -gn "${COLLECTOR_USER}")"
mkdir -p "${HOME_DIR}" "${XDG_RUNTIME_DIR}" /tmp/live2shop-logs
chown -R "${COLLECTOR_USER}:${GROUP_NAME}" "${HOME_DIR}" "${XDG_RUNTIME_DIR}" /tmp/live2shop-logs
chmod 700 "${XDG_RUNTIME_DIR}"
rm -f /tmp/live2shop-ready

run_user() {
  gosu "${COLLECTOR_USER}" env \
    HOME="${HOME_DIR}" \
    USER="${COLLECTOR_USER}" \
    LOGNAME="${COLLECTOR_USER}" \
    DISPLAY="${DISPLAY}" \
    XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR}" \
    PULSE_SERVER="${PULSE_SERVER}" \
    PATH="${PATH}" \
    "$@"
}

cleanup() {
  set +e
  rm -f /tmp/live2shop-ready
  run_user pulseaudio --kill >/dev/null 2>&1 || true
  [[ -n "${VNC_PID:-}" ]] && kill "${VNC_PID}" >/dev/null 2>&1 || true
  [[ -n "${OPENBOX_PID:-}" ]] && kill "${OPENBOX_PID}" >/dev/null 2>&1 || true
  [[ -n "${XVFB_PID:-}" ]] && kill "${XVFB_PID}" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

log "Starting Xvfb ${DISPLAY} (${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH})"
run_user Xvfb "${DISPLAY}" \
  -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" \
  -dpi 96 -ac -nolisten tcp +extension GLX +render -noreset \
  > /tmp/live2shop-logs/xvfb.log 2>&1 &
XVFB_PID=$!

for _ in $(seq 1 50); do
  run_user xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 && break
  sleep 0.2
done
run_user xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 || {
  echo "[ERROR] Xvfb failed. See /tmp/live2shop-logs/xvfb.log"
  exit 10
}

log "Starting Openbox"
run_user openbox --sm-disable > /tmp/live2shop-logs/openbox.log 2>&1 &
OPENBOX_PID=$!

log "Starting PulseAudio"
run_user pulseaudio --kill >/dev/null 2>&1 || true
run_user pulseaudio \
  --daemonize=yes \
  --exit-idle-time=-1 \
  --log-target="file:/tmp/live2shop-logs/pulseaudio.log"

for _ in $(seq 1 50); do
  run_user pactl info >/dev/null 2>&1 && break
  sleep 0.2
done
run_user pactl info >/dev/null 2>&1 || {
  echo "[ERROR] PulseAudio failed. See /tmp/live2shop-logs/pulseaudio.log"
  exit 11
}

if ! run_user pactl list short sinks | awk '{print $2}' | grep -qx 'live2shop'; then
  run_user pactl load-module module-null-sink \
    sink_name=live2shop \
    sink_properties=device.description=Live2Shop >/dev/null
fi
run_user pactl set-default-sink live2shop
run_user pactl set-default-source live2shop.monitor

log "Starting x11vnc on container port 5901"
run_user x11vnc \
  -display "${DISPLAY}" \
  -rfbport 5901 \
  -forever \
  -shared \
  -nopw \
  -noxdamage \
  -xkb \
  -quiet \
  > /tmp/live2shop-logs/x11vnc.log 2>&1 &
VNC_PID=$!

for _ in $(seq 1 50); do
  ss -ltn | grep -q ':5901 ' && break
  sleep 0.2
done

touch /tmp/live2shop-ready
chown "${COLLECTOR_USER}:${GROUP_NAME}" /tmp/live2shop-ready

log "READY"
log "Display: ${DISPLAY}"
log "Pulse source: live2shop.monitor"
log "VNC container port: 5901"

while true; do
  kill -0 "${XVFB_PID}" >/dev/null 2>&1 || { echo "[ERROR] Xvfb died"; exit 20; }
  kill -0 "${VNC_PID}" >/dev/null 2>&1 || { echo "[ERROR] x11vnc died"; exit 21; }
  sleep 5
done
