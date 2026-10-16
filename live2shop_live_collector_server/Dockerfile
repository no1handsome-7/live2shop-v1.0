FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    SCREEN_WIDTH=1280 \
    SCREEN_HEIGHT=720 \
    SCREEN_DEPTH=24 \
    XDG_RUNTIME_DIR=/tmp/live2shop-runtime \
    PULSE_SERVER=unix:/tmp/live2shop-runtime/pulse/native

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg xvfb openbox x11vnc pulseaudio pulseaudio-utils x11-utils \
      dbus-x11 gosu procps psmisc iproute2 net-tools curl ca-certificates \
      fonts-noto-cjk fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/live2shop_collector
COPY requirements.txt /opt/live2shop_collector/requirements.txt
RUN python -m pip install --no-cache-dir -r /opt/live2shop_collector/requirements.txt

COPY . /opt/live2shop_collector
RUN chmod +x /opt/live2shop_collector/server/*.sh

WORKDIR /workspace/live2shop
ENTRYPOINT ["/opt/live2shop_collector/server/entrypoint.sh"]
