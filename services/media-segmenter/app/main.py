from __future__ import annotations

import logging
import asyncio
import os
import time
from typing import Dict

from confluent_kafka import Producer
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from minio import Minio
from prometheus_client import make_asgi_app
from redis.asyncio import Redis

from .proto_util import uuid7_str
from .settings import settings
from .worker import StreamWorker

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("media-segmenter")

app = FastAPI(title="Live2Shop Media Segmenter")
app.mount("/metrics", make_asgi_app())

workers: Dict[str, StreamWorker] = {}

producer = Producer({
    "bootstrap.servers": settings.kafka_brokers,
    "enable.idempotence": True,
    "acks": "all",
})
minio = Minio(
    settings.minio_endpoint,
    access_key=settings.minio_access_key,
    secret_key=settings.minio_secret_key,
    secure=False,
)
redis = Redis.from_url(settings.redis_url, decode_responses=True)

@app.get("/healthz")
async def healthz():
    await redis.ping()
    return {"ok": True, "active_streams": len(workers)}

@app.post("/hooks/srs/publish")
async def on_publish(request: Request):
    payload = await request.json()
    # SRS hook: stream is the stream name after /live/.
    stream = str(payload.get("stream", "")).strip()
    if not stream:
        return JSONResponse({"code": 400, "error": "missing stream"}, status_code=400)
    try:
        room_id = int(stream)
    except ValueError:
        return JSONResponse(
            {"code": 422, "error": "for v1 the RTMP stream name must be numeric room_id"},
            status_code=422,
        )

    old = workers.get(stream)
    if old is not None:
        await old.stop()

    sid = uuid7_str()
    worker = StreamWorker(
        room_id=room_id,
        stream_name=stream,
        stream_session_id=sid,
        started_wallclock_ms=int(time.time() * 1000),
        producer=producer,
        minio=minio,
        redis=redis,
    )
    workers[stream] = worker

    async def delayed_start():
        # on_publish fires before SRS has fully accepted the publisher. Starting
        # the subscriber in the callback can race the stream becoming readable.
        await asyncio.sleep(0.4)
        if workers.get(stream) is worker and not worker.stopped:
            await worker.start()

    asyncio.create_task(delayed_start())
    log.info("SRS publish room=%s stream_session=%s", room_id, sid)
    # SRS accepts HTTP 200 with JSON {"code":0}.
    return {"code": 0, "stream_session_id": sid}

@app.post("/hooks/srs/unpublish")
async def on_unpublish(request: Request):
    payload = await request.json()
    stream = str(payload.get("stream", "")).strip()
    worker = workers.pop(stream, None)
    if worker is not None:
        await worker.stop()
    return {"code": 0}

@app.on_event("shutdown")
async def shutdown():
    for worker in list(workers.values()):
        await worker.stop()
    producer.flush(5)
    await redis.aclose()
