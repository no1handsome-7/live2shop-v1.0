# 30-Day Execution Plan

The rule is: reduce capacity, not system layers.

> 当前范围调整：推荐模型和推荐闭环暂缓，优先完成 CommerceVLM P2 商品页弱监督数据、训练与评测。
> 下文涉及 RecModel、行为归因和推荐服务的里程碑属于后续阶段，不是当前 P2 阻塞项。

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
- 准备获准使用的商品详情页 URL 与商家讲解视频
- P0 alignment begins
- content-ai wiring with current checkpoint

**Gate:** real RTMP, real user event, real Kafka/MinIO.

## Week 2 — CommerceVLM

Day 8-9:
- P0 complete/eval
- ShareGPT4Video subset + E-VAds train data
- P1 CPT

Day 10-11:
- 商品页主详情/主视频采集、去重和按商品 split
- 商品视频切窗
- OCR/ASR
- 3-GPU teacher labeling

Day 12-13:
- audit + manual gold
- P2 SFT

Day 14:
- ContentState online
- CommerceEvent v2（不预测平台 SKU）
- Debug Content panel

**Gate:** live stream updates real AI ContentState.

## Week 3 — CommerceVLM post-training（推荐链路暂缓）

Day 15:
- P3 DPO
- P2 vs P3 ablation

Day 16:
- P4 RLVR start on GPU0
- 扩充 P2 冲突/负样本与人工 Gold

Day 17:
- P3/P4 数据质量与消融
- 在线 video + ASR + OCR 输入契约验证

Day 18:
- CommerceEvent v2 错误分析
- 页面弱监督泄漏检查

Day 19:
- Teacher/audit 阈值校准
- 商品组级 train/val/test 泄漏复核

Day 20-21:
- P2/P3/P4 训练与评测闭环
- 推荐模型工作保留到后续计划

**Gate:** CommerceVLM 仅凭 video + ASR + OCR 生成有证据支撑的 CommerceEvent v2。

## Week 4 — engineering quality and evidence

Day 22-23:
- ablations
- E-VAds Test/eMCR/own gold evaluation
- CommerceEvent JSON/字段 F1、价格准确率与无依据生成率

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
