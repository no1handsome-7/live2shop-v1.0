# Live2Shop v1.0 Architecture

## 1. Business problem

Live2Shop solves a narrow but real live-commerce problem:

> A live room changes products continuously. A raw “user liked room X” event is too coarse.
> We want to know **what content/product the viewer was actually seeing when the behavior
> happened**, update short-term intent, and recommend products immediately after leaving.

Golden path:

```text
Vintage live stream
  ↓
viewer watches Levi's denim section for several minutes
  ↓
LIKE at media PTS 318.420s
  ↓
Temporal Behavior-Content Attribution
  ↓
LIKE → Levi's 70505 / vintage / denim
  ↓
SessionRecModel updates short-term user embedding
  ↓
semantic/category/popular recalls
  ↓
ranking + deterministic business rerank
  ↓
post-live vintage products
  ↓
viewport impression/click feedback
  ↓
raw lake → next offline training cycle
```

---

## 2. Six logical planes

```text
MEDIA PLANE
OBS/FFmpeg ─RTMP→ SRS ─HLS→ Web
                   │
                   └→ media-segmenter → MinIO

CONTENT AI PLANE
MediaSegmentEvent
      ↓
8 frames + Faster-Whisper ASR + RapidOCR
      ↓
CommerceVLM
      ↓
CommerceEvent v2（可观察商品语义，不预测平台 SKU）
      ↓
ContentStateEvent

BEHAVIOR / STREAM PLANE
Web behavior ─→ Go Gateway ─→ Kafka
                                │
ContentState ───────────────────┤
                                ↓
                         Flink keyed co-process
                      event-time + canonical PTS
                                ↓
                      AttributedBehaviorEvent

ONLINE FEATURE PLANE
AttributedBehaviorEvent
      ↓
SessionRecModel
      ↓
InterestState(versioned)
      ↓
Redis

RECOMMENDATION PLANE
InterestState
  ├→ Milvus semantic recall
  ├→ PostgreSQL category/style recall
  └→ PostgreSQL popularity recall
            ↓
         merge/dedup
            ↓
     RecModel ranking head
            ↓
 deterministic rerank
            ↓
 Recommendation API

OFFLINE / MLOPS PLANE
Kafka raw topics
      ↓
lake-sink
      ↓
MinIO immutable raw parquet
      ↓
curated datasets / training
      ↓
CommerceVLM / RecModel
      ↓
MLflow experiment lineage
      ↓
champion checkpoint
```

---

## 3. Services

| Component | Language | Main responsibility |
|---|---|---|
| `web` | React/Vite | HLS playback, behavior, debug UI, viewport impression |
| `srs` | SRS | real RTMP ingest + HLS playback |
| `media-segmenter` | Python | canonical 5s segments, MinIO, PTS |
| `event-gateway` | Go | behavior/feedback validation and Kafka ingress |
| `content-ai` | Python/PyTorch | ASR/OCR/VLM/content state |
| `realtime-feature` | Java/Flink | temporal attribution + watermarks |
| `derived-sink` | Python | operational attribution audit to PostgreSQL |
| `rec-model-server` | Python/PyTorch | real-time session encoding, Milvus, rank inference |
| `recommendation-api` | Go | multi-recall orchestration/ranking/rerank/audit |
| `lake-sink` | Python | append-only Kafka → MinIO Parquet |
| `ml/*` | Python/PyTorch | offline model training/evaluation |
| `data_tools/*` | Python | public/self data construction |

Infrastructure:

```text
Kafka
PostgreSQL
Redis
MinIO
Milvus
MLflow
Prometheus
Grafana
```

---

## 4. Three clocks

Never conflate them.

### event_time_ms

Business event UTC time.

Used for:

```text
Flink watermark
session windows
pipeline lag
```

### ingest_time_ms

When the server receives the event.

Used for:

```text
client/server delay
data freshness
```

### media PTS

What the viewer was actually watching.

Used for behavior-content attribution.

Canonical key:

```text
(stream_session_id, media_pts_ms)
```

A new RTMP reconnect always gets a new `stream_session_id`.

---

## 5. Canonical media timeline

SRS/FFmpeg internal timestamps are implementation details.

Live2Shop creates one canonical timeline per stream session:

```text
segment0: [0, 5000)
segment1: [5000, 10000)
...
```

Actual segment duration from `ffprobe` is accumulated, so drift is not created by blindly assuming exactly 5000ms.

The browser estimates its current canonical playhead as:

```text
latest segmenter live edge
-
HLS playback latency
```

This is carried in user behavior.

---

## 6. Content state invariant

VLM inference may use overlapping context internally, but emitted canonical states are non-overlapping:

```text
[315000, 320000)
[320000, 325000)
...
```

`ContentState` is a snapshot.

The deterministic state layer computes transitions:

```text
observed price differs       → PRICE_CHANGE
promotion tags differ        → PROMOTION_CHANGE
visible → invisible          → PRODUCT_DISAPPEARED
```

精确的 `PRODUCT_SWITCH` 需要未来独立的 SKU matching 层；`commerce_event.v2` 本身不声称识别
平台 SKU。这使状态机和业务标识匹配保持在 LLM 之外。

---

## 7. CommerceVLM 的输入边界

CommerceVLM 的训练与在线推理严格使用：

```text
video + ASR + OCR
```

商品详情页描述和价格只在 P2 离线制数时作为 Teacher 弱监督，最终训练 JSONL 会物理删除这些字段。
平台 `active_item_id` 不能从视频可靠恢复，因此不属于 `commerce_event.v2` target。

如果未来恢复推荐链路，catalog/SKU matching 必须作为 CommerceVLM 之后的独立受约束模块；不能把候选
商品、上一状态或页面描述重新塞入 CommerceVLM prompt。

---

## 8. Behavior attribution

Point event:

```text
LIKE @ PTS 321200
```

matches:

```text
state.start <= 321200 < state.end
```

Heartbeat/watch interval:

```text
317000 → 327000
```

may overlap multiple states.

Product-bearing overlap is normalized:

```text
A: 3s
B: 5s
NO_PRODUCT: 2s

A weight = 3/8
B weight = 5/8
```

Flink uses a fast path:

- if state coverage already exists, emit immediately;
- if AI content output is late, buffer and wait for event-time grace;
- late content arrival rechecks pending behaviors;
- final timer acts as fallback.

This gives low latency without giving up out-of-order correctness.

---

## 9. Online interest

The learned `SessionRecModel` is the ranking/retrieval truth.

The human-readable top tags shown in the UI are a decayed explanation/debug layer only.

Online sequence is stored in **event-time order** (Redis Sorted Set), not Kafka
arrival order:

```text
action
content_embedding
time gap
effective dwell
```

→ Session Transformer → 128D user embedding.

`ENTER_ROOM` / `EXIT_ROOM` are lifecycle signals and are not fed as preference
tokens. The interest `source_watermark_ms` is monotonically non-decreasing.

Redis state is versioned:

```text
user:interest:{user_id}
```

Older versions must never overwrite newer versions.

---

## 10. Embedding type safety

Every embedding has:

```text
dim
values
version
```

Only:

```text
version == commerce-space-v1
```

is used in the production Milvus collection.

Same dimensionality does **not** imply compatible semantic spaces.

TAOBAO-MM 128D and KuaiLive-M3 128D are external spaces and go through reinitialized adapters during R0/R1/R2 training.

---

## 11. Recommendation

v1 uses three recall channels:

```text
Semantic ANN (Milvus)   ~300
Category/style          ~100
Popularity              ~100
        ↓
merge/dedup
        ↓
rank head
        ↓
deterministic rerank
```

Rerank handles:

```text
sold-out filtering
category diversity
duplicate removal
```

If Milvus/model inference fails:

```text
category + popularity fallback
```

The heavy VLM is never in recommendation request critical path.

---

## 12. Freshness barrier

User may LIKE and immediately EXIT.

Recommendation request contains:

```text
min_source_event_time_ms
```

Recommendation API waits briefly (500ms v1) for:

```text
InterestState.source_watermark_ms >= min_source_event_time_ms
```

If the barrier times out, it returns available state plus:

```text
interest_fresh=false
```

instead of blocking for seconds.

This makes freshness observable.

---

## 13. Served vs Impression

Server return:

```text
RecommendationServedEvent
```

does not mean the user viewed the item.

Actual `IMPRESSION` is emitted by the browser only when IntersectionObserver sees at least 50% of the product card.

Clicks/cart/purchase always carry the original `impression_id`.

That makes CTR/ranking labels reconstructable.

---

## 14. Raw, derived, online

Raw facts:

```text
Kafka → MinIO
```

append-only.

Derived:

```text
ContentState
Attribution
training datasets
```

replayable.

Online:

```text
Redis InterestState
candidate cache
```

replaceable/versioned.

PostgreSQL is operational/audit metadata, not the event warehouse.

---

## 15. Failure/recovery

| Failure | Behavior |
|---|---|
| Kafka duplicate | event IDs + DB unique keys |
| Flink TaskManager/job task failure | checkpointed state + Kafka replay/restart strategy |
| Redis stale write | monotonic interest version |
| Content AI slow | behavior waits only if needed |
| Content AI uncertain | SKU null; weak interest update |
| Milvus unavailable | category/hot fallback |
| RecModel unavailable | deterministic retrieval-score fallback |
| sold out | rerank filters |
| RTMP reconnect | new `stream_session_id` |
| process crash before lake offset commit | raw row can duplicate; dedup on topic/partition/offset |

---

## 16. Small scale, full semantics

The local version intentionally reduces capacity:

```text
Kafka:          1 broker / 3 partitions
Flink:          local mini cluster
Redis:          single node
Milvus:         standalone
GPU serving:    1 GPU VLM + 1 GPU RecModel
live rooms:     1~10
catalog:        10k~100k
```

What is **not** removed:

```text
real RTMP
real VLM
real event streaming
event-time attribution
learned interest/ranker
multi-recall
feedback
data lake
model lineage
monitoring
failure recovery
```

That is the intended meaning of “small but complete”.
