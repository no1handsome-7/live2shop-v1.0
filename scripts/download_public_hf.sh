#!/usr/bin/env bash
set -euo pipefail

mkdir -p data/raw data/eval

echo "[1/5] E-VAds"
hf download TaobaoTmall-AlgorithmProducts/E-VAds_Benchmark \
  --repo-type dataset --local-dir data/raw/e-vads

echo "[2/5] TAOBAO-MM (large: ~139GB)"
hf download TaoBao-MM/Taobao-MM \
  --repo-type dataset --local-dir data/raw/taobao-mm

echo "[3/5] KuaiLive-M3 (large: ~61GB)"
hf download imgkkk2004/KuaiLive-M3 \
  --repo-type dataset --local-dir data/raw/kuailive-m3

echo "[4/5] eMCR evaluation"
hf download alibabagroup/eMCR \
  --repo-type dataset --local-dir data/eval/emcr

echo "[5/5] ShareGPT4Video metadata only"
hf download ShareGPT4Video/ShareGPT4Video \
  sharegpt4video_40k.jsonl \
  --repo-type dataset --local-dir data/raw/sharegpt4video

echo
echo "MUGE and Products-10K have separate official download/terms workflows."
echo "See docs/DATASETS.md. ShareGPT4Video media is intentionally NOT downloaded in full."
