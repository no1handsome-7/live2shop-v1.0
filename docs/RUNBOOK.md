# Live2Shop v1.0 Runbook

## 1. Prerequisites

Recommended:

```text
Ubuntu 22.04/24.04
Docker 27+
Docker Compose v2
NVIDIA Driver + NVIDIA Container Toolkit
3× RTX 3090
64~128GB RAM
1~2TB NVMe
```

Verify:

```bash
docker version
docker compose version
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

---

## 2. Repository initialization

```bash
cp .env.example .env
mkdir -p checkpoints/commerce-vlm checkpoints/rec-model
```

Docker builds protobuf bindings internally. For local Python/Go development:

```bash
pip install grpcio-tools
# also install protoc + protoc-gen-go
bash scripts/generate_proto.sh
```

---

## 3. Train models first

Follow:

```text
docs/DATASETS.md
docs/TRAINING_GUIDE.md
```

At the end you should have:

```text
checkpoints/commerce-vlm/champion/
checkpoints/rec-model/champion/
```

The final online stack deliberately refuses to silently substitute a fake model.

---

## 4. Bring up infrastructure only

Before model serving:

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d \
  postgres redis minio kafka kafka-init etcd milvus mlflow srs prometheus grafana
```

Check:

```bash
docker compose -f infra/docker-compose.yml --env-file .env ps
```

URLs:

```text
MinIO console   http://localhost:9001
MLflow          http://localhost:5000
Prometheus      http://localhost:9090
Grafana         http://localhost:3000
Flink Web UI    http://localhost:8081
SRS API         http://localhost:1985
```

Grafana:

```text
user: admin
password: live2shop
```

---

## 5. Prepare item catalog

Copy:

```text
data_tools/templates/catalog.jsonl
```

to:

```text
data/catalog/items.jsonl
```

Every item should contain:

```json
{
  "item_id": 300001,
  "title": "Levi's 70505 Vintage Denim Jacket",
  "category_id": 1001,
  "category_path": ["服饰","男装","牛仔夹克"],
  "brand": "Levi's",
  "style_tags": ["古着","美式复古","牛仔"],
  "attributes": {"color":"blue"},
  "price_cent": 46900,
  "inventory": 10,
  "image": "/abs/path/product.jpg",
  "image_uri": "",
  "popularity_score": 8.1,
  "room_ids": [20001]
}
```

Import:

```bash
pip install "psycopg[binary]"

python scripts/import_catalog.py \
  --jsonl data/catalog/items.jsonl \
  --dsn postgresql://live2shop:live2shop@localhost:5432/live2shop
```

---

## 6. Encode items + index Milvus

```bash
source .venv-commerce/bin/activate
export PYTHONPATH=$PWD/ml/commerce_vlm:$PYTHONPATH

python scripts/encode_item_catalog.py \
  --checkpoint checkpoints/commerce-vlm/champion \
  --catalog-jsonl data/catalog/items.jsonl \
  --output data/processed/catalog/item_embeddings.parquet
```

Start RecModel server:

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d rec-model-server
```

Index:

```bash
pip install pymilvus pandas pyarrow

python services/rec-model-server/index_items.py \
  --parquet data/processed/catalog/item_embeddings.parquet \
  --host localhost \
  --port 19530
```

If the embedding space version changes, rebuild the Milvus collection rather than mixing spaces.

---

## 7. Start complete stack

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d --build
```

Smoke test:

```bash
python scripts/smoke_test.py
```

Expected:

```text
gateway         200
recommendation  200
segmenter       200
rec-model       200
srs-api         200
```

---

## 8. Start a real RTMP stream

Use any owned/authorized video:

```bash
bash scripts/create_demo_stream.sh \
  data/self/originals/vintage_live.mp4 \
  20001
```

This is a real RTMP publisher:

```text
FFmpeg -> SRS -> HLS/Web
```

The source happens to be a recorded file replayed at real-time speed.

OBS can use:

```text
Server: rtmp://localhost:1935/live
Stream key: 20001
```

---

## 9. Open Live2Shop

```text
http://localhost:5173
```

Expected flow:

1. SRS shows the live stream.
2. `media-segmenter` begins 5-second canonical segments.
3. MinIO receives segment files.
4. Content AI produces `ContentState`.
5. Right-side Content panel updates.
6. Click **进入直播**.
7. Every 10s a valid foreground heartbeat is emitted.
8. Click Like while a product is shown.
9. Flink attributes that behavior to the visible product/window.
10. Interest panel changes.
11. Click **退出并推荐**.
12. Recommendation cards appear.
13. Scrolling a card into the viewport emits true `IMPRESSION`.
14. Clicking emits `CLICK`.

---

## 10. Kafka inspection

Host broker:

```text
localhost:29092
```

Use Kafka CLI inside container:

```bash
docker compose -f infra/docker-compose.yml exec kafka \
  /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server kafka:9092 \
  --list
```

Consumer example:

```bash
docker compose -f infra/docker-compose.yml exec kafka \
  /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server kafka:9092 \
  --topic live2shop.user.behavior.v1 \
  --from-beginning
```

Payload is protobuf binary, so console output itself is not human-readable. Use application logs/debug API for semantics.

---

## 11. PostgreSQL inspection

```bash
docker compose -f infra/docker-compose.yml exec postgres \
  psql -U live2shop -d live2shop
```

Useful:

```sql
SELECT * FROM live_stream_sessions ORDER BY started_at DESC LIMIT 10;

SELECT
  start_pts_ms,end_pts_ms,active_item_id,brand,style_tags,
  transition,confidence,model_version
FROM content_states
ORDER BY created_at DESC
LIMIT 20;

SELECT
  source_event_id,action,attribution_method,
  contributions,attribution_confidence
FROM attributed_interactions
ORDER BY created_at DESC
LIMIT 20;

SELECT *
FROM recommendation_requests
ORDER BY created_at DESC
LIMIT 10;
```

---

## 12. Redis inspection

```bash
docker compose -f infra/docker-compose.yml exec redis redis-cli
```

```text
GET live:timeline:20001
GET live:content:20001
GET user:interest:10001
LRANGE user:sequence:10001 0 -1
```

---

## 13. Debug HTTP

```bash
curl http://localhost:8088/api/v1/live/20001/timeline

curl http://localhost:8088/api/v1/debug/live/20001/state

curl http://localhost:8088/api/v1/debug/users/10001/interest
```

---

## 14. MinIO raw lake

Buckets:

```text
live2shop-media
live2shop-lake
mlflow
milvus
```

Raw lake layout:

```text
raw/live2shop.user.behavior.v1/dt=YYYY-MM-DD/hour=HH/
raw/live2shop.content.state.v1/...
raw/live2shop.behavior.attributed.v1/...
raw/live2shop.rec.served.v1/...
raw/live2shop.rec.feedback.v1/...
```

Rows contain:

```text
topic
partition
offset
kafka_timestamp_ms
key
original protobuf payload bytes
```

Dedup key during curated rebuild:

```text
(topic, partition, offset)
```

---

## 15. Failure tests worth running

### Duplicate feedback

Replay the same `event_id`/request flow and verify unique constraints prevent duplicate operational rows.

### Flink TaskManager failure/restart

```bash
docker compose -f infra/docker-compose.yml restart realtime-feature-taskmanager
```

Completed checkpoints live on the shared:

```text
flinkcheckpoints
```

volume and are used by Flink's normal task failure recovery while the application
cluster is alive.

**Boundary of the single-machine v1:** destroying/recreating the JobManager itself is
not advertised as automatic HA recovery. Production evolution would add Flink HA
metadata (for example ZooKeeper/Kubernetes HA) plus remote checkpoint storage.
Do not describe this local stack as JobManager-HA in interviews.

### Redis restart

```bash
docker compose -f infra/docker-compose.yml restart redis
```

AOF is enabled.

### Milvus unavailable

```bash
docker compose -f infra/docker-compose.yml stop milvus
```

Recommendation should still produce category/hot fallback results.

### RTMP reconnect

Stop/restart publisher.

A new:

```text
stream_session_id
```

must appear, and PTS restarts from 0 without contaminating the prior stream epoch.

---

## 16. Shutdown

```bash
docker compose -f infra/docker-compose.yml --env-file .env down
```

Keep volumes.

Full reset:

```bash
docker compose -f infra/docker-compose.yml --env-file .env down -v
```

The latter deletes databases, lake data and checkpoints stored in Docker volumes; use carefully.
