# Live2Shop v1.0 完整训练教程（3×RTX 3090）

本文档是从 **0 checkpoint → CommerceVLM → RecModel → 在线部署** 的主教程。

---

# 1. 硬件与软件

推荐机器：

```text
GPU: 3 × RTX 3090 24GB
RAM: 64GB minimum, 128GB recommended
Disk: 1TB minimum, 2TB NVMe recommended
CPU: 16 cores+ preferred
OS: Ubuntu 22.04 / 24.04
```

3090 支持 BF16，但驱动/CUDA/PyTorch 必须匹配。

推荐：

```bash
nvidia-smi
docker --version
docker compose version
ffmpeg -version
```

不要在同一个 Python venv 同时硬塞所有在线服务依赖。

建议三个环境：

```text
.venv-commerce
.venv-rec
.venv-data
```

---

# 2. CommerceVLM 模型定义

主干：

```text
8 video frames / 1 image
        ↓
SigLIP2-Base-patch16-224
        ↓
Temporal Resampler
16 learned queries
        ↓
MLP Projector
        ↓
16 visual tokens
        ↓
Qwen3-1.7B-Base
```

另有：

```text
Visual retrieval head ─┐
                       ├─→ commerce-space-v1 (128D)
Text retrieval head ───┘
```

在线内容 embedding：

```text
normalize(
  0.6 * visual_embedding +
  0.4 * structured_state_text_embedding
)
```

商品库同样使用 0.6 visual + 0.4 title/brand/style/attribute text。

基础模型：

- Qwen3-1.7B-Base  
  https://huggingface.co/Qwen/Qwen3-1.7B-Base
- SigLIP2 Base Patch16 224  
  https://huggingface.co/google/siglip2-base-patch16-224

二者都是预训练单模态 backbone。

你在简历/面试中的准确表述应该是：

> 基于预训练 vision/language backbone，自行训练 multimodal projector、temporal
> resampler 和 commerce embedding space，并完成 domain CPT、SFT、preference
> optimization 与 RLVR。

不要说：

> “从随机初始化预训练了 1.7B VLM”。

---

# 3. 建 CommerceVLM 环境

```bash
python3 -m venv .venv-commerce
source .venv-commerce/bin/activate

pip install --upgrade pip
pip install -r ml/commerce_vlm/requirements.txt

export PYTHONPATH=$PWD/ml/commerce_vlm:$PYTHONPATH
```

首次训练会自动从 Hugging Face 下载 Qwen/SigLIP2。

如果中国大陆访问 Hugging Face 较慢，请使用你合规可用的镜像/提前离线下载，然后把
YAML 的 `llm_model` / `vision_model` 改成本地目录。

---

# 4. P0 — Multimodal Alignment Pre-training

## 目标

只训练：

```text
TemporalResampler
Projector
Visual retrieval head
Text retrieval head
```

冻结：

```text
SigLIP2
Qwen3-1.7B
```

这一步建立“视觉 token 能进入语言模型”和“商品视觉/文本能进入同一 commerce space”。

## 数据

```text
MUGE
+
Products-10K
```

建议：

```text
MUGE:         100k~250k
Products10K:   50k~150k
```

先转换，详见 `docs/DATASETS.md`。

混合：

```bash
python data_tools/mix_jsonl.py \
  --input data/processed/commerce_vlm/muge.jsonl:2 \
  --input data/processed/commerce_vlm/products10k.jsonl:1 \
  --output data/processed/commerce_vlm/p0_alignment.jsonl
```

## Loss

```text
L_P0 = L_NTP + λ * L_contrastive
```

默认：

```text
λ = 0.25
temperature = 0.07
```

其中 NTP 让 injected visual tokens 能支持商品语义生成；
contrastive loss 让 image/text 进入同一 128D space。

## 三卡训练

```bash
source .venv-commerce/bin/activate
export PYTHONPATH=$PWD/ml/commerce_vlm:$PYTHONPATH

CUDA_VISIBLE_DEVICES=0,1,2 accelerate launch \
  --num_processes 3 \
  ml/commerce_vlm/train_stage.py \
  --config ml/commerce_vlm/configs/p0_alignment.yaml
```

默认关键超参：

```text
micro_batch = 2 / GPU
grad_accum  = 16
global effective batch ~= 96
max_steps   = 2500
lr          = 2e-4
frames/image = 1
BF16
```

### 预计时间

`6~12h`。

实际取决于：

- PCIe 拓扑；
- 3090 是否降频；
- NVMe 读取；
- CPU image decoding；
- dataset 实际规模。

### 验收

至少记录：

```text
train NTP loss
contrastive loss
image-text Recall@K on held-out MUGE
```

并检查随机 50 张商品图生成内容不再完全与图片无关。

---

# 5. P1 — Temporal + Commerce Continual Pre-training

## 目标

引入：

```text
Temporal Resampler
video frame ordering
commercial video domain
OCR/ASR-like dense semantics
```

训练：

```text
Projector / TemporalResampler: full
Qwen: LoRA rank 32
Vision: frozen
```

## 数据

建议：

```text
ShareGPT4Video: 3k~8k
E-VAds SFT split: all
E-VAds RL split: all
```

**不使用 E-VAds Test。**

合并：

```bash
python data_tools/mix_jsonl.py \
  --input data/processed/commerce_vlm/sharegpt_video.jsonl:2 \
  --input data/processed/commerce_vlm/evads_sft.jsonl:2 \
  --input data/processed/commerce_vlm/evads_rl.jsonl:1 \
  --output data/processed/commerce_vlm/p1_cpt.jsonl
```

训练：

```bash
CUDA_VISIBLE_DEVICES=0,1,2 accelerate launch \
  --num_processes 3 \
  ml/commerce_vlm/train_stage.py \
  --config ml/commerce_vlm/configs/p1_cpt.yaml
```

默认：

```text
frames/video = 8
micro_batch  = 1/GPU
grad_accum   = 24
LoRA rank    = 32
lr           = 5e-5
steps        = 1200
```

预计：

```text
8~14h
```

### 为什么不是全参 LLM

3×3090 做 1.7B 全参 AdamW 并配合视频 token，工程收益/成本比不合理。
我们的重点是：

```text
自训练 multimodal bridge
+
时序模块
+
领域适配
+
后训练
```

而不是为了“全参”两个字浪费整个项目周期。

---

# 6. P2 — CommerceEvent SFT

这是 Live2Shop 最关键的数据阶段。

## 数据组成

```text
teacher-audited CommerceEvent 8k~12k
+
human-reviewed difficult cases
+
可选 E-VAds SFT QA
```

必须按 `docs/DATASETS.md` 的商品页弱监督链路依次使用：

```text
collect_product_pages.py
build_product_page_manifest.py
build_commerce_windows.py
align_page_video_evidence.py
teacher_label_commerce.py
audit_commerce_labels.py
export_p2_student_samples.py
```

最终必须用导出器剥离页面候选事实，不能直接拼接 Teacher/Audit 中间文件：

```bash
python data_tools/export_p2_student_samples.py \
  --input data/self/audit/accepted.jsonl \
  --input data/self/audit/reviewed.jsonl \
  --split train \
  --output data/processed/commerce_vlm/p2_sft.jsonl
```

可以再加入一小部分 E-VAds：

```bash
python data_tools/mix_jsonl.py \
  --input data/processed/commerce_vlm/p2_sft.jsonl:4 \
  --input data/processed/commerce_vlm/evads_sft.jsonl:1 \
  --output data/processed/commerce_vlm/p2_sft_mixed.jsonl
```

然后把 YAML 的 `train_jsonl` 指向 mixed 版本。

## 输出 schema

模型学的是 Snapshot，不让大模型直接负责业务状态机：

```json
{
  "schema_version": "commerce_event.v2",
  "product_visible": true,
  "category": "牛仔夹克",
  "brand": "Levi's",
  "style_tags": ["古着", "美式复古", "牛仔"],
  "color": "蓝色",
  "attributes": {},
  "price_cent": 46900,
  "promotion_tags": [],
  "evidence": {
    "asr": ["..."],
    "ocr": ["..."],
    "visual": ["..."]
  }
}
```

`active_item_id`、页面价、商品链接和标注置信度均不是可观察目标，不得进入 `target_json`。
`price_cent` 专指当前视频窗口中由画面、OCR 或口播支持的人民币价格；页面价格只保存在审计层的
`page_price_cent`。Teacher 的 confidence 位于 `annotation`，最终学生样本会将其删除。

`PRODUCT_SWITCH/PRICE_CHANGE` 在线由前后 Snapshot 确定性比较得到，而不是让 LLM 自己猜。

这是有意的系统设计。

## 训练

```bash
CUDA_VISIBLE_DEVICES=0,1,2 accelerate launch \
  --num_processes 3 \
  ml/commerce_vlm/train_stage.py \
  --config ml/commerce_vlm/configs/p2_sft.yaml
```

预计：

```text
5~9h
```

### P2 验收

在 `manual_gold` 上测试：

```text
category accuracy/F1
brand F1
price exact match
style tag F1
JSON valid rate
unsupported/hallucination rate
```

---

# 7. P3 — Preference Optimization

## 为什么做

电商结构化理解非常怕“看起来合理但观察证据不支持”：

```text
256GB vs 512GB
500ml vs 750ml
直播价 399 vs 标价 469
```

普通 SFT 很容易学会“说得像”，而不是“不确定就不猜”。

## 构造数据

```bash
python data_tools/build_preferences.py \
  --input data/processed/commerce_vlm/p2_sft.jsonl \
  --output data/processed/commerce_vlm/p3_preference.jsonl \
  --negatives-per-sample 2
```

hard negative 类型：

```text
wrong price
wrong color
unsupported style
hallucinated promotion
fake evidence
```

## DPO

```bash
CUDA_VISIBLE_DEVICES=0,1,2 accelerate launch \
  --num_processes 3 \
  ml/commerce_vlm/train_dpo.py \
  --config ml/commerce_vlm/configs/p3_dpo.yaml
```

默认：

```text
beta = 0.1
lr = 1e-5
steps = 800
```

预计：

```text
6~10h
```

### 验收

比较：

```text
P2 SFT
vs
P3 DPO
```

重点看：

```text
SKU confusion rate ↓
wrong price rate ↓
unsupported attribute rate ↓
```

---

# 8. P4 — Small-scale RLVR / Group-relative Policy Optimization

项目包含一个紧凑、可审计的 RLVR 实现：

```text
ml/commerce_vlm/train_grpo.py
```

它不是声称复刻某厂内部训练框架，而是：

```text
on-policy group sampling
deterministic verifier reward
group-standardized advantage
clipped sequence-level policy ratio
frozen reference regularization
```

适合在 3×3090 项目中真正跑通并做消融。

## 数据

优先：

```text
human reviewed
+
high-confidence verified CommerceEvent
```

生成：

```bash
python data_tools/build_rlvr.py \
  --input data/processed/commerce_vlm/p2_sft.jsonl \
  --limit 1000 \
  --output data/processed/commerce_vlm/p4_rlvr.jsonl
```

## Reward

当前 verifier：

```text
schema validity
attribute exactness
style overlap
evidence presence
unsupported-key hallucination penalty
```

注意：

`PRODUCT_SWITCH` 等业务 transition 不让 RL 学，在线由 deterministic state machine 处理。

## 单 GPU 训练

紧凑版本默认使用一张卡：

```bash
CUDA_VISIBLE_DEVICES=0 python ml/commerce_vlm/train_grpo.py \
  --config ml/commerce_vlm/configs/p4_grpo.yaml
```

另外两张卡此时可同时：

- teacher labeling；
- RecModel；
- evaluation。

默认：

```text
group_size = 4
steps = 400
max_new_tokens = 192
lr = 5e-6
```

预计：

```text
8~18h
```

如果 OOM：

```text
group_size: 4 -> 2
max_new_tokens: 192 -> 128
```

---

# 9. CommerceVLM 最终 checkpoint

最终：

```text
checkpoints/commerce-vlm/p4/final
```

如果 RL 版本在人工 gold 上退化，则**不要因为关键词硬上 RL**。

正确做法：

```text
P3/DPO = champion
P4/RL = challenger
```

用真实评测选 champion。

晋升：

```bash
python scripts/promote_checkpoint.py \
  --family commerce-vlm \
  --checkpoint checkpoints/commerce-vlm/p4/final
```

若 P3 更好：

```bash
python scripts/promote_checkpoint.py \
  --family commerce-vlm \
  --checkpoint checkpoints/commerce-vlm/p3/final
```

---

# 10. RecModel 模型结构

```text
Content embedding 128D
+
Action embedding
+
Time-gap embedding
+
Watch-duration embedding
+
Position embedding
        ↓
2-layer Causal Transformer
hidden = 256
heads = 4
        ↓
User embedding 128D
        │
        ├── InfoNCE retrieval
        │
        └── Ranking MLP
```

Ranking 输入：

```text
user
item
user*item
dot(user,item)
retrieval score
category/style match
popularity
inventory
```

---

# 11. RecModel 环境

```bash
python3 -m venv .venv-rec
source .venv-rec/bin/activate

pip install --upgrade pip
pip install -r ml/rec_model/requirements.txt

export PYTHONPATH=$PWD/ml/rec_model:$PYTHONPATH
```

---

# 12. R0 — TAOBAO-MM 电商预训练

处理见 `docs/DATASETS.md`。

先 1.5M sample：

```bash
python data_tools/prepare_taobao_mm.py \
  --train-glob 'data/raw/taobao-mm/train/train-shard-00000[0-7].parquet' \
  --max-rows 1500000 \
  --output-dir data/processed/rec/taobao
```

训练：

```bash
CUDA_VISIBLE_DEVICES=0,1,2 accelerate launch \
  --num_processes 3 \
  ml/rec_model/train.py \
  --config ml/rec_model/configs/r0_taobao.yaml
```

Loss：

```text
L_R0 =
  λ_retrieval * InfoNCE
  +
  λ_rank * BCE(click)
```

non-click sample 的 retrieval loss 被 mask，只参与 ranking BCE。

预计：

```text
1~4h
```

RecModel 很小，真正耗时主要是数据 IO/预处理。

---

# 13. R1 — KuaiLive-M3 时间行为适配

处理：

```bash
python data_tools/prepare_kuailive_m3.py \
  --data-dir data/raw/kuailive-m3 \
  --output-dir data/processed/rec/kuailive \
  --users 1500 \
  --max-segment-shards 6
```

训练：

```bash
CUDA_VISIBLE_DEVICES=0,1,2 accelerate launch \
  --num_processes 3 \
  ml/rec_model/train.py \
  --config ml/rec_model/configs/r1_kuailive.yaml
```

这里**重新初始化外部 embedding adapter**，但保留：

```text
sequence dynamics
action embeddings
temporal transformer
ranking knowledge
```

原因：

```text
TAOBAO-MM 128D != KuaiLive-M3 128D
```

维数一样不代表语义空间一样。

---

# 14. R2 — Live2Shop Commerce Space Bridge

## 先编码商品库

```bash
source .venv-commerce/bin/activate
export PYTHONPATH=$PWD/ml/commerce_vlm:$PYTHONPATH

python scripts/encode_item_catalog.py \
  --checkpoint checkpoints/commerce-vlm/champion \
  --catalog-jsonl data/catalog/items.jsonl \
  --output data/processed/catalog/item_embeddings.parquet
```

## 冷启动 bootstrap

```bash
source .venv-rec/bin/activate

python data_tools/bootstrap_commerce_sessions.py \
  --embeddings data/processed/catalog/item_embeddings.parquet \
  --catalog-jsonl data/catalog/items.jsonl \
  --sessions 30000 \
  --output-dir data/processed/rec/commerce_bridge
```

## 训练

```bash
CUDA_VISIBLE_DEVICES=0,1,2 accelerate launch \
  --num_processes 3 \
  ml/rec_model/train.py \
  --config ml/rec_model/configs/r2_commerce_bridge.yaml
```

然后：

```bash
python scripts/promote_checkpoint.py \
  --family rec-model \
  --checkpoint checkpoints/rec-model/r2/final
```

---

# 15. RecModel 评测

例如 R0/TAOBAO validation NPZ：

```bash
python ml/rec_model/evaluate.py \
  --checkpoint checkpoints/rec-model/r0/final \
  --data 'data/processed/rec/taobao-val/*.npz'
```

输出：

```text
Recall@10
Recall@20
Recall@40
NDCG@10/20/40
MRR
AUC (有 rank label 时)
```

必须保存：

```text
Rule/EMA baseline
vs
R0
vs
R1
vs
R2
```

不要只展示最终模型。

---

# 16. 真实系统回流后的二次 R2

运行系统后：

```text
UserBehavior
→ Attribution
→ MinIO raw lake
```

用：

```bash
python data_tools/build_commerce_bridge_from_lake.py ...
```

重建真实 Commerce session。

然后复制一个新 config：

```text
r3_online_replay.yaml
```

从 R2 初始化，小学习率：

```text
1e-5 ~ 5e-5
```

训练：

```text
1000~3000 steps
```

这一步才代表：

> online feedback → offline training → model promotion

闭环真正成立。

---

# 17. 商品向量写入 Milvus

训练 CommerceVLM 后：

```bash
python scripts/encode_item_catalog.py \
  --checkpoint checkpoints/commerce-vlm/champion \
  --catalog-jsonl data/catalog/items.jsonl \
  --output data/processed/catalog/item_embeddings.parquet
```

先启动 `rec-model-server` 让 collection 创建，然后：

```bash
docker compose -f infra/docker-compose.yml --env-file .env up -d milvus rec-model-server
```

本机：

```bash
pip install pymilvus pandas pyarrow
python services/rec-model-server/index_items.py \
  --parquet data/processed/catalog/item_embeddings.parquet \
  --host localhost \
  --port 19530
```

---

# 18. MLflow 记录

启动完整 stack 后，MLflow：

```text
http://localhost:5000
```

登记：

```bash
pip install mlflow

python scripts/register_mlflow_artifact.py \
  --family commerce-vlm \
  --stage p4 \
  --checkpoint checkpoints/commerce-vlm/p4/final \
  --metric sku_acc=0.XX \
  --metric hallucination_rate=0.XX
```

RecModel 同理：

```bash
python scripts/register_mlflow_artifact.py \
  --family rec-model \
  --stage r2 \
  --checkpoint checkpoints/rec-model/r2/final \
  --metric recall20=0.XX
```

注意：代码把 MLflow 用作实验/lineage/artifact registry；
在线容器使用经过晋升的本地 immutable checkpoint。

---

# 19. 三张 3090 的训练排程

不要所有任务都三卡独占。

推荐：

### 前半程

```text
P0/P1/P2/P3       GPU 0,1,2（DDP）
```

训练在跑时 CPU 侧开发/预处理系统。

### Teacher Label

```text
GPU0 shard0
GPU1 shard1
GPU2 shard2
```

### P4 RLVR

```text
GPU0: RLVR
GPU1: RecModel
GPU2: teacher/evaluation
```

RecModel 不需要三卡才能训练。

---

# 20. 总训练时间预算

3×3090，NVMe，64~128GB RAM，大致：

| 阶段 | 预计 |
|---|---:|
| P0 Alignment | 6–12 h |
| P1 CPT | 8–14 h |
| P2 SFT | 5–9 h |
| P3 DPO | 6–10 h |
| P4 RLVR | 8–18 h |
| Teacher labeling | 4–12 h，取决于窗口量 |
| R0 TAOBAO | 1–4 h |
| R1 KuaiLive | 1–4 h |
| R2 Bridge | <2 h |
| 多轮 eval/ablation | 1–3 d 的墙钟时间 |

所以：

```text
一次主训练链路 ≈ 1.5~3 天 GPU wall time
完整项目实验 ≈ 7~12 天 GPU 使用期
```

这是“训练时间”，不等于项目总工期。

---

# 21. 必做消融

CommerceVLM：

```text
Base
+ P0
+ P1
+ P2
+ P3
+ P4

w/o Products-10K
w/o Temporal Resampler
w/o preference hard negatives
```

RecModel：

```text
EMA/rule baseline
R0 TAOBAO
R0 + R1 KuaiLive
R0 + R1 + R2 Commerce Bridge
```

Online：

```text
semantic only
vs
semantic + category + hot multi-recall

ranker off
vs
ranker on
```

---

# 22. 简历数字必须来自真实结果

最后可以写：

```text
SKU Accuracy +X%
Hallucination Rate -Y%
Recall@20 +Z%
Recommendation P95 = XX ms
Attribution success = XX%
Kafka/Flink throughput = XX events/s
```

但在你实际跑出来之前，**不要预填数字**。

这是求职项目可信度的一部分。
