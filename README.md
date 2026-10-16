# Live2Shop v1.0

**Real-time live-commerce recommendation via temporal multimodal understanding and moment-level behavior attribution.**

Live2Shop is a small-scale but end-to-end live-commerce system built for a realistic Internet-platform scenario:

> understand what product/content a viewer is actually watching in a live stream, align watch/like/follow/click behavior to that moment, update short-term intent, and recommend relevant products after the viewer leaves.

The repository intentionally reduces **capacity**, not **system layers**.

> 当前开发范围（P2 数据阶段）：先完成 CommerceVLM；推荐模型暂缓。商品详情页仅为商家讲解视频
> 提供离线弱监督，CommerceVLM 的训练与在线推理输入始终只有视频、ASR 和 OCR。详见
> `docs/DATASETS.md` 第 5 节。

## What is implemented

### Multimodal model training

`CommerceVLM`:

```text
SigLIP2-Base
  ↓
Temporal Resampler (train from scratch)
  ↓
Multimodal Projector (train from scratch)
  ↓
Qwen3-1.7B-Base
  ↓
structured CommerceEvent
```

Training pipeline:

```text
P0 multimodal alignment pre-training
→ P1 temporal/e-commerce continual pre-training
→ P2 structured CommerceEvent SFT
→ P3 DPO preference alignment
→ P4 small-scale RLVR/group-relative policy optimization
```

The model also learns a shared 128D `commerce-space-v1` used by live content and catalog products.

### Recommendation model training

`SessionRecModel`:

```text
content embedding
+ action
+ time gap
+ effective dwell
  ↓
2-layer causal Session Transformer
  ↓
128D user intent
  ├→ retrieval
  └→ ranking head
```

Training:

```text
R0 TAOBAO-MM e-commerce sequence/rank
→ R1 KuaiLive-M3 temporal live adaptation
→ R2 Live2Shop commerce-space bridge
→ optional R3 real online replay
```

### Online system

```text
OBS/FFmpeg
   │ RTMP
   ▼
SRS ───── HLS ─────► React Web
   │
   ▼
media-segmenter
   │
   ├── MinIO
   ▼
Kafka MediaSegment
   │
   ▼
Content AI
8 frames + ASR + OCR + CommerceVLM
   │
   ▼
ContentState
   │
   │                       Web behavior
   │                            │
   └────────────── Kafka ◄──────┘
                        │
                        ▼
                Flink attribution
             event-time + media PTS
                        │
                        ▼
              AttributedBehavior
                        │
                        ▼
                SessionRecModel
                        │
                        ▼
                 Redis Interest
                        │
                        ▼
      Semantic + Category + Popular Recall
                        │
                        ▼
                  Ranking Head
                        │
                        ▼
              deterministic rerank
                        │
                        ▼
              Recommendation API
                        │
               served / impression
                        │
                        ▼
                    Feedback
                        │
                        ▼
                 MinIO Raw Lake
                        │
                        ▼
                Offline retraining
```

## Technology stack

| Layer | Technology |
|---|---|
| Web | React, Vite, hls.js |
| Media | SRS, RTMP, HLS, FFmpeg |
| API | Go |
| Model service | Python, PyTorch |
| Streaming | Kafka, Flink |
| Online feature | Redis |
| Vector recall | Milvus |
| Operational DB | PostgreSQL |
| Object/lake storage | MinIO |
| Experiment lineage | MLflow |
| Metrics | Prometheus, Grafana |
| Packaging | Docker Compose |

## Key engineering ideas

- user behavior is **not** “detected by VLM”; browser/backend event tracking records it;
- behavior is aligned to content using `(stream_session_id, media_pts)`, not wall-clock approximation;
- heartbeat represents actual foreground playback intervals;
- point events and watch intervals use different attribution logic;
- live AI state can arrive late, so Flink uses immediate-fast-path + watermark fallback;
- VLM runs asynchronously and is not in the recommendation critical path;
- SKU is constrained by the live room catalog; unknown is allowed instead of hallucinating;
- `ContentState` is immutable/replayable;
- embedding version is part of the semantic type;
- server **served** is different from real viewport **impression**;
- recommendation has semantic/category/popular multi-recall + learned rank + business rerank;
- raw events are append-only in MinIO;
- Flink task state is checkpointed to a shared persistent volume;
- user interest is monotonically versioned;
- A/B experiment A = deterministic baseline, B = learned ranker;
- failed consumer records have a DLQ contract.

## Repository

```text
live2shop-v1.0/
├── proto/                    # cross-language protobuf contracts
├── db/migrations/            # PostgreSQL schema
├── infra/                    # Docker Compose, SRS, Kafka, MLflow, monitoring
├── services/
│   ├── event-gateway/        # Go
│   ├── media-segmenter/      # Python/FFmpeg
│   ├── content-ai/           # OCR/ASR/CommerceVLM
│   ├── rec-model-server/     # SessionRecModel + Milvus
│   ├── recommendation-api/   # Go multi-recall/rank/rerank
│   ├── lake-sink/            # Kafka -> immutable raw lake
│   └── derived-sink/         # attribution audit -> PostgreSQL
├── stream/realtime-feature/  # Java/Flink
├── ml/
│   ├── commerce_vlm/
│   └── rec_model/
├── data_tools/               # public/self data pipelines
├── scripts/
├── web/
├── tests/
└── docs/
```

## Start here

1. [`docs/DATASETS.md`](docs/DATASETS.md) — public dataset links and self-data construction.
2. [`docs/TRAINING_GUIDE.md`](docs/TRAINING_GUIDE.md) — detailed training commands for 3×3090.
3. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system semantics and failure behavior.
4. [`docs/RUNBOOK.md`](docs/RUNBOOK.md) — train, index, start, RTMP stream, inspect.
5. [`docs/API.md`](docs/API.md) — HTTP APIs.
6. [`docs/THIRTY_DAY_PLAN.md`](docs/THIRTY_DAY_PLAN.md) — one-month execution order.
7. [`docs/RESULTS_TEMPLATE.md`](docs/RESULTS_TEMPLATE.md) — fill only after real runs.

## Important: data and weights are not bundled

This source archive does not redistribute:

- upstream public datasets;
- commercial/live videos;
- Qwen/SigLIP model weights;
- trained Live2Shop checkpoints.

That is intentional for size/licensing reasons. All download links and preparation/training instructions are included.

## Quick infrastructure check

After training/promoting the two models:

```bash
cp .env.example .env
docker compose -f infra/docker-compose.yml --env-file .env up -d --build
python scripts/smoke_test.py
```

Push a live source:

```bash
bash scripts/create_demo_stream.sh data/self/originals/vintage_live.mp4 20001
```

Open:

```text
http://localhost:5173
```

## Hardware target

Designed around:

```text
3 × RTX 3090 24GB
64~128GB RAM
1~2TB NVMe
```

The online system can run with GPU0 for Content AI and GPU1 for RecModel; GPU2 remains available for training/evaluation.

## What not to claim

Do not claim:

- “trained a 1.7B language model from random initialization”;
- “production scale”;
- “real online lift” from synthetic users;
- “exactly-once Redis”;
- business gains not measured from real data.

A strong project is credible because the boundaries are explicit.
