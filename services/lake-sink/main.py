"""
Append-only Kafka -> MinIO raw event sink.

Important: the lake stores the original protobuf bytes, Kafka key, partition and offset.
This means future schema/code changes can reparse the exact historical fact stream.
"""
from __future__ import annotations

import io
import os
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
from confluent_kafka import Consumer
from minio import Minio

BROKERS = os.getenv("KAFKA_BROKERS","kafka:9092")
ENDPOINT = os.getenv("MINIO_ENDPOINT","minio:9000")
ACCESS = os.getenv("MINIO_ACCESS_KEY","live2shop")
SECRET = os.getenv("MINIO_SECRET_KEY","live2shop-secret")
BUCKET = os.getenv("LAKE_BUCKET","live2shop-lake")
FLUSH_ROWS = int(os.getenv("FLUSH_ROWS","2000"))
FLUSH_SECONDS = int(os.getenv("FLUSH_SECONDS","30"))

TOPICS = [
    "live2shop.user.behavior.v1",
    "live2shop.content.state.v1",
    "live2shop.behavior.attributed.v1",
    "live2shop.interest.updated.v1",
    "live2shop.rec.served.v1",
    "live2shop.rec.feedback.v1",
    "live2shop.dead-letter.v1",
]

minio = Minio(ENDPOINT,ACCESS,SECRET,secure=False)
consumer = Consumer({
    "bootstrap.servers":BROKERS,
    "group.id":"lake-sink-v1",
    "auto.offset.reset":"earliest",
    "enable.auto.commit":False,
})
consumer.subscribe(TOPICS)

buffer = []
last_flush = time.time()

def flush():
    global buffer,last_flush
    if not buffer:
        last_flush=time.time();return

    grouped=defaultdict(list)
    for row in buffer:
        ts=datetime.fromtimestamp(row["kafka_timestamp_ms"]/1000,timezone.utc)
        partition=(row["topic"],ts.strftime("%Y-%m-%d"),ts.strftime("%H"))
        grouped[partition].append(row)

    for (topic,day,hour),rows in grouped.items():
        table=pa.Table.from_pylist(rows,schema=pa.schema([
            ("topic",pa.string()),
            ("partition",pa.int32()),
            ("offset",pa.int64()),
            ("kafka_timestamp_ms",pa.int64()),
            ("key",pa.binary()),
            ("payload",pa.binary()),
        ]))
        sink=io.BytesIO()
        pq.write_table(table,sink,compression="zstd")
        data=sink.getvalue()
        key=(
            f"raw/{topic}/dt={day}/hour={hour}/"
            f"part-{uuid.uuid4()}.parquet"
        )
        minio.put_object(
            BUCKET,key,io.BytesIO(data),len(data),
            content_type="application/vnd.apache.parquet",
        )
        print("lake:",key,len(rows))

    # At-least-once external sink: upload succeeds before offsets are committed.
    # A crash between upload and commit can duplicate rows; topic/partition/offset
    # form the immutable deduplication key for curated jobs.
    consumer.commit(asynchronous=False)
    buffer=[]
    last_flush=time.time()

try:
    while True:
        msg=consumer.poll(1.0)
        now=time.time()
        if msg is not None:
            if msg.error():
                print("kafka error:",msg.error())
            else:
                _,ts=msg.timestamp()
                if ts is None or ts < 0: ts=int(time.time()*1000)
                buffer.append({
                    "topic":msg.topic(),
                    "partition":msg.partition(),
                    "offset":msg.offset(),
                    "kafka_timestamp_ms":int(ts),
                    "key":msg.key(),
                    "payload":msg.value(),
                })
        if len(buffer)>=FLUSH_ROWS or (buffer and now-last_flush>=FLUSH_SECONDS):
            flush()
finally:
    flush()
    consumer.close()
