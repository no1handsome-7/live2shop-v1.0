# Results Template — fill with real measurements only

## CommerceVLM

| Model | JSON valid | SKU Acc | Category F1 | Price EM | Style F1 | Hallucination |
|---|---:|---:|---:|---:|---:|---:|
| P0/P1 base | | | | | | |
| + SFT | | | | | | |
| + DPO | | | | | | |
| + RLVR | | | | | | |

## Ablation

| Variant | SKU Acc | Price EM | Hallucination |
|---|---:|---:|---:|
| full | | | |
| w/o Products-10K | | | |
| w/o Temporal Resampler | | | |
| w/o hard negative DPO | | | |

## RecModel

| Model | Recall@20 | NDCG@20 | MRR | AUC |
|---|---:|---:|---:|---:|
| simple EMA | | | | |
| R0 TAOBAO | | | | |
| R0 + R1 KuaiLive | | | | |
| R0 + R1 + R2 bridge | | | | |

## Online system

| Metric | Result |
|---|---:|
| behavior API P50 | |
| behavior API P95 | |
| recommendation P50 | |
| recommendation P95 | |
| recommendation QPS (local) | |
| Content AI P50 / segment | |
| Content AI P95 / segment | |
| attribution latency P95 | |
| Kafka lag | |
| DLQ rate | |
| interest freshness hit rate | |

## A/B replay/demo traffic

| Variant | Impression | Click | CTR | Cart Rate |
|---|---:|---:|---:|---:|
| A baseline rank | | | | |
| B learned ranker | | | | |

Do not turn simulated CTR into a resume claim. A/B infrastructure can be demonstrated with simulated/replay traffic, while offline model quality claims should use real public benchmarks/manual gold.
