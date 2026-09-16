import secrets
import time
import uuid

def uuid7_str() -> str:
    # RFC 9562 UUIDv7: 48-bit Unix epoch milliseconds, version 7,
    # RFC variant, and cryptographically random remaining bits.
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    value = (
        (ts << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return str(uuid.UUID(int=value))

def event_meta(pb_common, producer: str, event_time_ms: int | None = None):
    now = int(time.time() * 1000)
    return pb_common.EventMeta(
        event_id=uuid7_str(),
        schema_version=1,
        event_time_ms=event_time_ms or now,
        ingest_time_ms=now,
        trace_id=uuid7_str(),
        producer=producer,
    )
