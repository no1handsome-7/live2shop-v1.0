# 30-Day Execution Plan

The rule is: reduce capacity, not system layers.

## Week 1 — contract/media/data foundation

Day 1-2:
- run protobuf/DB contract
- start Postgres/Redis/Kafka/MinIO/SRS
- RTMP → HLS → browser
- stream-session and canonical PTS verified

Day 3-4:
- media segmenter
- MinIO segment objects
- `MediaSegmentEvent`
- event gateway heartbeat/like/exit

Day 5-7:
- download/prepare MUGE + Products-10K
- prepare own live demo catalog/videos
- P0 alignment begins
- content-ai wiring with current checkpoint

**Gate:** real RTMP, real user event, real Kafka/MinIO.

## Week 2 — CommerceVLM

Day 8-9:
- P0 complete/eval
- ShareGPT4Video subset + E-VAds train data
- P1 CPT

Day 10-11:
- self live window extraction
- OCR/ASR
- 3-GPU teacher labeling

Day 12-13:
- audit + manual gold
- P2 SFT

Day 14:
- ContentState online
- catalog-constrained SKU
- Debug Content panel

**Gate:** live stream updates real AI ContentState.

## Week 3 — post-training + real-time recommendation

Day 15:
- P3 DPO
- P2 vs P3 ablation

Day 16:
- P4 RLVR start on GPU0
- TAOBAO RecModel prep/train on GPU1/2

Day 17:
- KuaiLive prep/R1
- Temporal Flink attribution final

Day 18:
- commerce item encoding
- R2 bridge
- Milvus

Day 19:
- multi-recall + rank + rerank
- freshness barrier

Day 20-21:
- full closed loop
- feedback → lake
- viewport impression
- attribution audit

**Gate:** watch/like → attributed item → live interest → exit → relevant items.

## Week 4 — engineering quality and evidence

Day 22-23:
- ablations
- E-VAds Test/eMCR/own gold evaluation
- KuaiLive/TAOBAO metrics

Day 24:
- failure tests
- Flink checkpoint/replay
- Redis/Milvus degradation

Day 25:
- load tests
- P50/P95
- Kafka lag
- content freshness

Day 26:
- Prometheus/Grafana
- experiment tags
- MLflow

Day 27:
- polish UI/debug timeline

Day 28:
- README architecture/results table

Day 29:
- record 3-5 min demo
- resume bullets
- interview question drill

Day 30:
- buffer only
- no new features

## Scope control

Do not add before v1 is done:

```text
Kubernetes
multi-region
payment
real order system
generative recommendation replacing ranker
7B model
mobile app
```

Those are scale/product extensions, not missing organs of this vertical slice.
