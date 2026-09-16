from __future__ import annotations

import json
import logging
import os
import threading
import time
import secrets
import uuid
from pathlib import Path

import numpy as np
from confluent_kafka import Consumer, Producer
from fastapi import FastAPI, HTTPException
from prometheus_client import Gauge, Histogram, make_asgi_app
from pydantic import BaseModel, Field
from pymilvus import (
    Collection, CollectionSchema, DataType, FieldSchema,
    connections, utility,
)
from redis import Redis

from live2shop.v1 import common_pb2, interest_pb2
from live2shop_rec.model import ACTION_TO_ID
from .runtime import RecRuntime

logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"))
log = logging.getLogger("rec-model-server")

BROKERS = os.getenv("KAFKA_BROKERS","kafka:9092")
REDIS_URL = os.getenv("REDIS_URL","redis://redis:6379/0")
MODEL_DIR = os.getenv("MODEL_DIR","/models/rec-model/champion")
MODEL_BACKEND = os.getenv("MODEL_BACKEND","trained")
MODEL_VERSION = os.getenv("MODEL_VERSION","rec-model-v1")
MILVUS_HOST = os.getenv("MILVUS_HOST","milvus")
MILVUS_PORT = os.getenv("MILVUS_PORT","19530")
COLLECTION = os.getenv("MILVUS_COLLECTION","live2shop_items")

redis = Redis.from_url(REDIS_URL, decode_responses=True)
producer = Producer({"bootstrap.servers":BROKERS,"enable.idempotence":True,"acks":"all"})
runtime: RecRuntime | None = None
collection: Collection | None = None

EVENT_LAG = Gauge("recmodel_event_lag_seconds","Attributed event processing lag")
INFER = Histogram("recmodel_inference_seconds","Interest model inference latency")

app = FastAPI(title="Live2Shop RecModel Server")

def new_uuid7():
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    value = ((ts << 80) | (0x7 << 76) | (secrets.randbits(12) << 64) |
             (0b10 << 62) | secrets.randbits(62))
    return str(uuid.UUID(int=value))

app.mount("/metrics", make_asgi_app())

class RecallRequest(BaseModel):
    embedding: list[float] = Field(min_length=128, max_length=128)
    top_k: int = Field(default=300, ge=1, le=1000)

class RankRequest(BaseModel):
    user_embedding: list[float] = Field(min_length=128, max_length=128)
    item_ids: list[int]
    business_features: list[list[float]] | None = None

def ensure_milvus():
    global collection
    connections.connect(alias="default", host=MILVUS_HOST, port=MILVUS_PORT)
    if not utility.has_collection(COLLECTION):
        fields = [
            FieldSchema("item_id", DataType.INT64, is_primary=True, auto_id=False),
            FieldSchema("embedding_version", DataType.VARCHAR, max_length=64),
            FieldSchema("embedding", DataType.FLOAT_VECTOR, dim=128),
        ]
        schema = CollectionSchema(fields, description="Live2Shop commerce-space-v1 items")
        collection = Collection(COLLECTION, schema)
        collection.create_index(
            "embedding",
            {"metric_type":"COSINE","index_type":"HNSW","params":{"M":32,"efConstruction":200}},
        )
    else:
        collection = Collection(COLLECTION)
    collection.load()

@app.on_event("startup")
def startup():
    global runtime
    if MODEL_BACKEND != "trained":
        raise RuntimeError("Final mode requires REC_MODEL_BACKEND=trained and a checkpoint.")
    runtime = RecRuntime(MODEL_DIR)
    ensure_milvus()
    threading.Thread(target=consume_loop, daemon=True).start()

@app.get("/healthz")
def healthz():
    return {"ok": runtime is not None and collection is not None, "model": MODEL_VERSION}

@app.post("/v1/semantic-recall")
def semantic_recall(req: RecallRequest):
    if collection is None: raise HTTPException(503, "milvus unavailable")
    hits = collection.search(
        data=[req.embedding],
        anns_field="embedding",
        param={"metric_type":"COSINE","params":{"ef":128}},
        limit=req.top_k,
        expr='embedding_version == "commerce-space-v1"',
        output_fields=["item_id","embedding_version"],
    )[0]
    return {"items":[{"item_id":int(h.id),"score":float(h.score)} for h in hits]}

@app.post("/v1/rank")
def rank(req: RankRequest):
    if not req.item_ids:
        return {"items":[]}
    if collection is None or runtime is None: raise HTTPException(503, "model unavailable")
    expr = "item_id in [" + ",".join(str(int(x)) for x in req.item_ids) + "]"
    rows = collection.query(expr=expr, output_fields=["item_id","embedding"])
    by_id = {int(x["item_id"]): x["embedding"] for x in rows}
    ids, emb, feat = [], [], []
    for i, item_id in enumerate(req.item_ids):
        if item_id not in by_id: continue
        ids.append(item_id)
        emb.append(by_id[item_id])
        if req.business_features:
            feat.append(req.business_features[i])
    scores = runtime.rank(req.user_embedding, emb, feat if req.business_features else None)
    return {"items":[{"item_id":i,"score":float(s)} for i,s in zip(ids,scores)]}

def consume_loop():
    c = Consumer({
        "bootstrap.servers":BROKERS,
        "group.id":"rec-model-online-v1",
        "auto.offset.reset":"earliest",
        "enable.auto.commit":False,
    })
    c.subscribe(["live2shop.behavior.attributed.v1"])
    while True:
        msg = c.poll(1.0)
        if msg is None: continue
        if msg.error():
            log.error("kafka error %s", msg.error()); continue
        ev = interest_pb2.AttributedBehaviorEvent()
        try:
            ev.ParseFromString(msg.value())
            last_error = None
            for attempt in range(3):
                try:
                    process_attributed(ev)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.25 * (2 ** attempt))
            if last_error is not None:
                raise last_error
            c.commit(message=msg, asynchronous=False)
        except Exception as exc:
            log.exception("attributed event moved to DLQ")
            publish_dlq(msg, exc, "rec-model-server")
            c.commit(message=msg, asynchronous=False)


def publish_dlq(msg, exc: Exception, consumer_name: str):
    now = int(time.time() * 1000)
    event = common_pb2.DeadLetterEvent(
        meta=common_pb2.EventMeta(
            event_id=new_uuid7(),
            schema_version=1,
            event_time_ms=now,
            ingest_time_ms=now,
            trace_id=new_uuid7(),
            producer=consumer_name,
        ),
        source_topic=msg.topic(),
        source_partition=msg.partition(),
        source_offset=msg.offset(),
        source_key=msg.key() or b"",
        source_payload=msg.value() or b"",
        consumer=consumer_name,
        error=repr(exc)[:4000],
    )
    producer.produce(
        "live2shop.dead-letter.v1",
        key=msg.key() or f"{msg.topic()}:{msg.partition()}:{msg.offset()}".encode(),
        value=event.SerializeToString(),
    )
    producer.flush(5)

def process_attributed(ev: interest_pb2.AttributedBehaviorEvent):
    if runtime is None:
        return

    lag = max(0.0, time.time() - ev.meta.event_time_ms / 1000.0)
    EVENT_LAG.set(lag)

    action_name = ev.DESCRIPTOR.fields_by_name["action"].enum_type.values_by_number[
        int(ev.action)
    ].name

    # ENTER/EXIT are lifecycle/trigger signals, not preference evidence.
    # The Recommendation API freshness barrier therefore targets the most recent
    # meaningful behavior before EXIT, not EXIT itself.
    if action_name in {"ENTER_ROOM", "EXIT_ROOM"}:
        return

    # Aggregate a possibly multi-state heartbeat into one weighted content token.
    vec = np.zeros(128, np.float32)
    total_weight = 0.0
    tag_weight = {}
    duration_ms = 0

    for contribution in ev.contributions:
        if contribution.content_embedding.dim != 128:
            continue
        if contribution.content_embedding.version != "commerce-space-v1":
            continue

        weight = max(0.0, float(contribution.attribution_weight))
        arr = np.asarray(contribution.content_embedding.values, np.float32)
        if arr.size != 128:
            continue

        vec += weight * arr
        total_weight += weight
        duration_ms += int(contribution.overlap_ms)

        for tag in contribution.semantic_tags:
            tag_weight[tag] = tag_weight.get(tag, 0.0) + weight

    if total_weight <= 0:
        return

    vec /= total_weight
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm

    row = {
        "event_id": ev.source_event_id,
        "event_time_ms": int(ev.meta.event_time_ms),
        "action": action_name,
        "duration_ms": int(duration_ms),
        "embedding": vec.tolist(),
        "tags": tag_weight,
    }

    # Sorted set makes the online model consume event-time order even when
    # attributed Kafka records arrive out of order.
    seq_key = f"user:sequence:{ev.user_id}"
    member = json.dumps(row, separators=(",", ":"), ensure_ascii=False)
    redis.zadd(seq_key, {member: float(ev.meta.event_time_ms)})
    # Keep latest 64 by event time.
    count = redis.zcard(seq_key)
    if count > 64:
        redis.zremrangebyrank(seq_key, 0, count - 65)
    redis.expire(seq_key, 7 * 24 * 3600)

    rows = [
        json.loads(x)
        for x in redis.zrange(seq_key, 0, -1)
    ]
    rows.sort(key=lambda x: (int(x.get("event_time_ms", 0)), str(x.get("event_id", ""))))

    with INFER.time():
        user_embedding = runtime.encode(rows)

    # Human-readable tags are for explanation/debug only. Recompute them from
    # the event-time ordered session to avoid out-of-order decay artifacts.
    scores = {}
    n = len(rows)
    for idx, item in enumerate(rows):
        age_decay = 0.92 ** max(0, n - 1 - idx)
        action_weight = {
            "HEARTBEAT": min(
                1.0, max(0.1, int(item.get("duration_ms", 0)) / 30_000.0)
            ) * 0.35,
            "LIKE": 1.5,
            "FOLLOW": 2.0,
            "ITEM_CLICK": 2.5,
        }.get(str(item.get("action", "")), 0.5)
        for tag, weight in (item.get("tags") or {}).items():
            scores[tag] = scores.get(tag, 0.0) + age_decay * action_weight * float(weight)

    if scores:
        max_score = max(scores.values()) or 1.0
        top = sorted(
            ((tag, min(1.0, score / max_score)) for tag, score in scores.items()),
            key=lambda x: x[1],
            reverse=True,
        )[:10]
    else:
        top = []

    previous_watermark = 0
    previous_raw = redis.get(f"user:interest:{ev.user_id}")
    if previous_raw:
        try:
            previous_watermark = int(
                json.loads(previous_raw).get("source_watermark_ms", 0)
            )
        except Exception:
            previous_watermark = 0

    source_watermark = max(previous_watermark, int(ev.meta.event_time_ms))
    version = int(redis.incr(f"user:interest:version:{ev.user_id}"))

    state = {
        "user_id": ev.user_id,
        "session_id": ev.session_id,
        "version": version,
        "updated_at_ms": int(time.time() * 1000),
        "source_watermark_ms": source_watermark,
        "fused_embedding": user_embedding,
        "top_interests": [
            {"tag": tag, "score": float(score)}
            for tag, score in top
        ],
        "confidence": float(ev.attribution_confidence),
        "model_name": "rec-model",
        "model_version": MODEL_VERSION,
        "embedding_version": "commerce-space-v1",
    }

    redis.set(
        f"user:interest:{ev.user_id}",
        json.dumps(state, ensure_ascii=False),
        ex=7 * 24 * 3600,
    )

    out = interest_pb2.InterestState(
        user_id=ev.user_id,
        session_id=ev.session_id,
        version=version,
        updated_at_ms=state["updated_at_ms"],
        source_watermark_ms=source_watermark,
        fused_embedding=common_pb2.EmbeddingVector(
            dim=128,
            values=user_embedding,
            version="commerce-space-v1",
        ),
        top_interests=[
            interest_pb2.InterestTag(tag=tag, score=float(score))
            for tag, score in top
        ],
        source_event_count=len(rows),
        confidence=float(ev.attribution_confidence),
        model=common_pb2.ModelRef(
            name="rec-model",
            version=MODEL_VERSION,
            alias="champion",
        ),
    )

    producer.produce(
        "live2shop.interest.updated.v1",
        key=str(ev.user_id).encode(),
        value=out.SerializeToString(),
    )
    producer.poll(0)
