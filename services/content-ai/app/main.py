from __future__ import annotations

import json
import logging
import os
import tempfile
import secrets
import uuid
import threading
import time
from pathlib import Path

import psycopg
from confluent_kafka import Consumer, Producer
from fastapi import FastAPI
from minio import Minio
from prometheus_client import Counter, Histogram, make_asgi_app
from redis import Redis

from live2shop.v1 import common_pb2, content_pb2, media_pb2

from .catalog import Catalog
from .perception import ASR, OCR, build_prompt, parse_json_loose, sample_frames
from .runtime import CommerceRuntime
from .baseline import BaselineRuntime

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("content-ai")

BROKERS = os.getenv("KAFKA_BROKERS", "kafka:9092")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "live2shop")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "live2shop-secret")
POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://live2shop:live2shop@postgres:5432/live2shop")
MODEL_DIR = os.getenv("MODEL_DIR", "/models/commerce-vlm/champion")
MODEL_BACKEND = os.getenv("MODEL_BACKEND", "trained")
MODEL_VERSION = os.getenv("MODEL_VERSION", "commerce-vlm-v1")
MODEL_ALIAS = os.getenv(
    "MODEL_ALIAS",
    "champion" if MODEL_BACKEND == "trained" else "baseline",
)
EMBEDDING_VERSION = os.getenv(
    "EMBEDDING_VERSION",
    "commerce-space-v1" if MODEL_BACKEND == "trained"
    else "baseline-hash-v1",
)
ASR_BACKEND = os.getenv("ASR_BACKEND", "whisper")
OCR_BACKEND = os.getenv("OCR_BACKEND", "rapidocr")
CONSUMER_GROUP = os.getenv("CONTENT_AI_CONSUMER_GROUP", "content-ai-v1")
AUTO_OFFSET_RESET = os.getenv("CONTENT_AI_AUTO_OFFSET_RESET", "earliest")

SEGMENT_LATENCY = Histogram("content_ai_segment_seconds", "Segment processing latency")
SEGMENTS = Counter("content_ai_segments_total", "Processed segments", ["status"])

app = FastAPI(title="Live2Shop Content AI")
app.mount("/metrics", make_asgi_app())

state_lock = threading.Lock()
last_states: dict[tuple[int, str], dict] = {}

minio = Minio(MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, secure=False)
producer = Producer({"bootstrap.servers": BROKERS, "enable.idempotence": True, "acks": "all"})
catalog = Catalog(POSTGRES_DSN)
redis_client = Redis.from_url(os.getenv("REDIS_URL","redis://redis:6379/0"), decode_responses=True)

runtime = None
asr = None
ocr = None

def new_uuid7():
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    value = (
        (ts << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return str(uuid.UUID(int=value))

def meta(event_time_ms: int):
    now = int(time.time()*1000)
    return common_pb2.EventMeta(
        event_id=new_uuid7(), schema_version=1,
        event_time_ms=event_time_ms, ingest_time_ms=now,
        trace_id=new_uuid7(), producer="content-ai",
    )

def transition(prev: dict | None, cur: dict) -> int:
    prev_visible = bool(prev and prev.get("product_visible"))
    cur_visible = bool(cur.get("product_visible"))

    # Invisible -> visible is an appearance, even when a previous
    # ContentState exists for this stream session.
    if cur_visible and not prev_visible:
        return content_pb2.PRODUCT_APPEARED

    if prev_visible and not cur_visible:
        return content_pb2.PRODUCT_DISAPPEARED

    # Switching only makes sense while a product remains visible.
    if (
        prev_visible
        and cur_visible
        and prev.get("active_item_id") != cur.get("active_item_id")
        and cur.get("active_item_id") is not None
    ):
        return content_pb2.PRODUCT_SWITCH

    if (
        prev_visible
        and cur_visible
        and prev.get("price_cent") != cur.get("price_cent")
    ):
        return content_pb2.PRICE_CHANGE

    if (
        prev_visible
        and cur_visible
        and prev.get("promotion_tags") != cur.get("promotion_tags")
    ):
        return content_pb2.PROMOTION_CHANGE

    return content_pb2.TRANSITION_NONE

def object_parts(uri: str):
    assert uri.startswith("s3://")
    bucket_key = uri[5:]
    bucket, key = bucket_key.split("/", 1)
    return bucket, key

def process_segment(ev: media_pb2.MediaSegmentEvent):
    global runtime, asr, ocr
    with SEGMENT_LATENCY.time():
        bucket, key = object_parts(ev.object_uri)
        suffix = Path(key).suffix or ".mkv"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            local = tmp.name
        try:
            minio.fget_object(bucket, key, local)
            frames = sample_frames(local, 8)
            asr_text = asr.transcribe(local) if asr is not None else ""
            ocr_text = ocr.run(frames) if ocr is not None else []
            candidates = catalog.candidates(ev.room_id)

            state_key = (ev.room_id, ev.stream_session_id)
            with state_lock:
                prev = last_states.get(state_key)

            if MODEL_BACKEND == "baseline":
                obj, emb = runtime.infer(
                    asr_text,
                    ocr_text,
                    candidates,
                )
            else:
                # CommerceVLM v2 receives video + ASR + OCR only. Catalog and
                # previous state remain outside the model boundary.
                prompt = build_prompt(asr_text, ocr_text)
                obj, emb = runtime.infer(frames, prompt)

            # Business validation: SKU must be in this room's candidate set.
            allowed = {int(x["item_id"]) for x in candidates}
            active = obj.get("active_item_id") if MODEL_BACKEND == "baseline" else None
            if active is not None:
                try: active = int(active)
                except Exception: active = None
            if active not in allowed:
                active = None

            if MODEL_BACKEND == "baseline":
                conf = max(0.0, min(1.0, float(obj.get("confidence", 0.0))))
            else:
                # Annotation confidence is not a v2 prediction target. This is
                # an explicit service-side quality heuristic for the protobuf.
                has_evidence = bool(asr_text or ocr_text or (obj.get("evidence") or {}).get("visual"))
                conf = 0.75 if obj.get("product_visible") and has_evidence else 0.4
            if active is None and conf > 0.8:
                conf = 0.8

            current = {
                "product_visible": bool(obj.get("product_visible", active is not None)),
                "active_item_id": active,
                "category": str(obj.get("category") or ""),
                "brand": str(obj.get("brand") or ""),
                "style_tags": [str(x) for x in (obj.get("style_tags") or [])][:16],
                "color": str(obj.get("color") or ""),
                "attributes": {str(k): str(v) for k,v in (obj.get("attributes") or {}).items()},
                "price_cent": obj.get("price_cent"),
                "promotion_tags": [str(x) for x in (obj.get("promotion_tags") or [])][:16],
                "confidence": conf,
            }
            trans = transition(prev, current)
            state_id = new_uuid7()

            pstate_builder = content_pb2.ProductState(
                category_path=[current["category"]] if current["category"] else [],
                brand=current["brand"],
                style_tags=current["style_tags"],
                color=current["color"],
                attributes=current["attributes"],
                promotion_tags=current["promotion_tags"],
                catalog_match_confidence=conf if active is not None else 0.0,
            )
            if active is not None:
                pstate_builder.active_item_id = active
            if current["price_cent"] is not None:
                pstate_builder.price_cent = int(current["price_cent"])
            pstate = pstate_builder

            evidence = []
            if asr_text:
                evidence.append(content_pb2.Evidence(
                    type=content_pb2.ASR, start_pts_ms=ev.start_pts_ms,
                    end_pts_ms=ev.end_pts_ms, text=asr_text[:1000], confidence=0.8,
                ))
            if ocr_text:
                evidence.append(content_pb2.Evidence(
                    type=content_pb2.OCR, start_pts_ms=ev.start_pts_ms,
                    end_pts_ms=ev.end_pts_ms, text=" | ".join(ocr_text)[:1000], confidence=0.8,
                ))
            evidence.append(content_pb2.Evidence(
                type=content_pb2.VISUAL, start_pts_ms=ev.start_pts_ms,
                end_pts_ms=ev.end_pts_ms, text="8-frame temporal sample", confidence=conf,
            ))

            out = content_pb2.ContentStateEvent(
                meta=meta(ev.meta.event_time_ms),
                state_id=state_id,
                segment_id=ev.segment_id,
                room_id=ev.room_id,
                stream_session_id=ev.stream_session_id,
                start_pts_ms=ev.start_pts_ms,
                end_pts_ms=ev.end_pts_ms,
                product_visible=current["product_visible"],
                product=pstate,
                transition=trans,
                catalog_candidates=[
                    content_pb2.CatalogCandidate(
                        item_id=int(c["item_id"]),
                        score=(conf if active is not None and int(c["item_id"]) == active else 0.0),
                    )
                    for c in candidates[:50]
                ],
                evidence=evidence,
                content_embedding=common_pb2.EmbeddingVector(
                    dim=len(emb), values=emb, version=EMBEDDING_VERSION
                ),
                confidence=conf,
                model=common_pb2.ModelRef(
                    name=(
                        "commerce-vlm"
                        if MODEL_BACKEND == "trained"
                        else "commerce-baseline"
                    ),
                    version=MODEL_VERSION,
                    alias=MODEL_ALIAS,
                ),
            )
            if prev and prev.get("state_id"):
                out.previous_state_id = prev["state_id"]

            producer.produce(
                "live2shop.content.state.v1",
                key=f"{ev.room_id}:{ev.stream_session_id}".encode(),
                value=out.SerializeToString(),
            )
            producer.flush(5)
            persist_content_state(out)

            current["state_id"] = state_id
            current["room_id"] = ev.room_id
            current["stream_session_id"] = ev.stream_session_id
            current["segment_id"] = ev.segment_id
            current["start_pts_ms"] = ev.start_pts_ms
            current["end_pts_ms"] = ev.end_pts_ms
            current["transition"] = content_pb2.ContentTransition.Name(trans)
            current["asr"] = asr_text
            current["ocr"] = ocr_text
            current["model_version"] = MODEL_VERSION
            current["embedding_version"] = EMBEDDING_VERSION
            with state_lock:
                last_states[state_key] = current
            redis_client.set(
                f"live:content:{ev.room_id}",
                json.dumps(current, ensure_ascii=False),
                ex=60,
            )
            SEGMENTS.labels(status="ok").inc()
        except Exception:
            SEGMENTS.labels(status="error").inc()
            log.exception("failed segment %s", ev.segment_id)
            raise
        finally:
            try: os.unlink(local)
            except OSError: pass

def persist_content_state(ev: content_pb2.ContentStateEvent):
    p = ev.product
    with psycopg.connect(POSTGRES_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO content_states(
                  state_id, segment_id, room_id, stream_session_id,
                  start_pts_ms, end_pts_ms, product_visible, active_item_id,
                  category_path, brand, style_tags, attributes, price_cent,
                  promotion_tags, transition, catalog_candidates, evidence,
                  confidence, model_name, model_version, model_alias,
                  embedding_version, previous_state_id
                ) VALUES (
                  %s::uuid,%s::uuid,%s,%s::uuid,%s,%s,%s,%s,
                  %s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s::jsonb,
                  %s,%s,%s,%s,%s,%s::uuid
                )
                ON CONFLICT (segment_id, model_name, model_version) DO NOTHING
                """,
                (
                    ev.state_id, ev.segment_id, ev.room_id, ev.stream_session_id,
                    ev.start_pts_ms, ev.end_pts_ms, ev.product_visible,
                    p.active_item_id if p.HasField("active_item_id") else None,
                    list(p.category_path), p.brand, list(p.style_tags),
                    json.dumps(dict(p.attributes), ensure_ascii=False),
                    p.price_cent if p.HasField("price_cent") else None,
                    list(p.promotion_tags), content_pb2.ContentTransition.Name(ev.transition),
                    json.dumps([{"item_id":x.item_id,"score":x.score} for x in ev.catalog_candidates]),
                    json.dumps([{"type":content_pb2.EvidenceType.Name(x.type),"text":x.text,
                                 "start_pts_ms":x.start_pts_ms,"end_pts_ms":x.end_pts_ms,
                                 "confidence":x.confidence} for x in ev.evidence], ensure_ascii=False),
                    ev.confidence, ev.model.name, ev.model.version, ev.model.alias,
                    ev.content_embedding.version,
                    ev.previous_state_id if ev.HasField("previous_state_id") else None,
                ),
            )
        conn.commit()

def consume_loop():
    c = Consumer({
        "bootstrap.servers": BROKERS,
        "group.id": CONSUMER_GROUP,
        "auto.offset.reset": AUTO_OFFSET_RESET,
        "enable.auto.commit": False,
    })
    c.subscribe(["live2shop.media.segment.v1"])
    while True:
        msg = c.poll(1.0)
        if msg is None: continue
        if msg.error():
            log.error("kafka: %s", msg.error()); continue
        ev = media_pb2.MediaSegmentEvent()
        try:
            ev.ParseFromString(msg.value())
            last_error = None
            for attempt in range(3):
                try:
                    process_segment(ev)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    time.sleep(0.5 * (2 ** attempt))
            if last_error is not None:
                raise last_error
            c.commit(message=msg, asynchronous=False)
        except Exception as exc:
            log.exception("media event moved to DLQ")
            publish_dlq(msg, exc, "content-ai")
            c.commit(message=msg, asynchronous=False)
    c.close()

def publish_dlq(msg, exc: Exception, consumer_name: str):
    now = int(time.time() * 1000)
    event = common_pb2.DeadLetterEvent(
        meta=common_pb2.EventMeta(
            event_id=new_uuid7(), schema_version=1,
            event_time_ms=now, ingest_time_ms=now,
            trace_id=new_uuid7(), producer=consumer_name,
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
        key=(msg.key() or f"{msg.topic()}:{msg.partition()}:{msg.offset()}".encode()),
        value=event.SerializeToString(),
    )
    producer.flush(5)

@app.get("/healthz")
def healthz():
    return {"ok": runtime is not None, "backend": MODEL_BACKEND}

@app.on_event("startup")
def startup():
    global runtime, asr, ocr

    if MODEL_BACKEND == "trained":
        runtime = CommerceRuntime(MODEL_DIR)
    elif MODEL_BACKEND == "baseline":
        runtime = BaselineRuntime()
        log.warning(
            "starting integration baseline; "
            "this is NOT the CommerceVLM champion model"
        )
    else:
        raise RuntimeError(
            f"unsupported MODEL_BACKEND={MODEL_BACKEND!r}"
        )

    if ASR_BACKEND == "whisper":
        asr = ASR(os.getenv("WHISPER_MODEL", "small"))
    elif ASR_BACKEND == "disabled":
        asr = None
    else:
        raise RuntimeError(
            f"unsupported ASR_BACKEND={ASR_BACKEND!r}"
        )

    if OCR_BACKEND == "rapidocr":
        ocr = OCR()
    elif OCR_BACKEND == "disabled":
        ocr = None
    else:
        raise RuntimeError(
            f"unsupported OCR_BACKEND={OCR_BACKEND!r}"
        )

    log.info(
        "content-ai ready backend=%s model_version=%s "
        "alias=%s asr=%s ocr=%s group=%s offset_reset=%s",
        MODEL_BACKEND,
        MODEL_VERSION,
        MODEL_ALIAS,
        ASR_BACKEND,
        OCR_BACKEND,
        CONSUMER_GROUP,
        AUTO_OFFSET_RESET,
    )

    threading.Thread(target=consume_loop, daemon=True).start()

app.mount("/metrics", make_asgi_app())
