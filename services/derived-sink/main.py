from __future__ import annotations
import json,os,time
import psycopg
from confluent_kafka import Consumer
from live2shop.v1 import interest_pb2, behavior_pb2

BROKERS=os.getenv("KAFKA_BROKERS","kafka:9092")
DSN=os.getenv("POSTGRES_DSN","postgresql://live2shop:live2shop@postgres:5432/live2shop")

c=Consumer({
 "bootstrap.servers":BROKERS,
 "group.id":"derived-audit-sink-v1",
 "auto.offset.reset":"earliest",
 "enable.auto.commit":False,
})
c.subscribe(["live2shop.behavior.attributed.v1"])

def contribution_json(x):
    return {
      "state_id":x.state_id,
      "item_id":x.item_id if x.HasField("item_id") else None,
      "overlap_ms":x.overlap_ms,
      "attribution_weight":x.attribution_weight,
      "semantic_tags":list(x.semantic_tags),
      "content_confidence":x.content_confidence,
      "embedding_version":x.content_embedding.version,
      "content_model":{
        "name":x.content_model.name,
        "version":x.content_model.version,
        "alias":x.content_model.alias,
      }
    }

while True:
    msg=c.poll(1.0)
    if msg is None:continue
    if msg.error():
        print("kafka",msg.error());continue
    ev=interest_pb2.AttributedBehaviorEvent()
    try:
        ev.ParseFromString(msg.value())
        with psycopg.connect(DSN) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                  INSERT INTO attributed_interactions(
                    attribution_id,source_event_id,user_id,session_id,room_id,
                    stream_session_id,action,attribution_method,contributions,
                    attribution_confidence,playback_discontinuity,created_at
                  ) VALUES(
                    %s::uuid,%s::uuid,%s,%s::uuid,%s,%s::uuid,%s,%s,%s::jsonb,%s,%s,
                    to_timestamp(%s/1000.0)
                  )
                  ON CONFLICT(source_event_id) DO NOTHING
                """,(
                    ev.meta.event_id,ev.source_event_id,ev.user_id,ev.session_id,ev.room_id,
                    ev.stream_session_id,
                    behavior_pb2.UserAction.Name(ev.action),
                    interest_pb2.AttributionMethod.Name(ev.method),
                    json.dumps([contribution_json(x) for x in ev.contributions],ensure_ascii=False),
                    ev.attribution_confidence,ev.playback_discontinuity,ev.meta.ingest_time_ms
                ))
            conn.commit()
        c.commit(message=msg,asynchronous=False)
    except Exception as e:
        # Keep offset uncommitted so a transient DB outage is replayed.
        print("derived sink error:",repr(e))
        c.seek(msg.topic_partition())
        time.sleep(1)
