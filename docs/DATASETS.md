# Live2Shop v1.0 数据集说明与下载

> 本项目只把公开数据用于研究/求职项目。请在下载前再次阅读上游最新许可证和使用条款。
> 数据集、视频、商品图像不包含在本代码包内。

## 0. 数据在整个训练链路中的位置

| 数据 | 是否训练 | 阶段 | 主要作用 |
|---|---:|---|---|
| MUGE | 是 | CommerceVLM P0 | 中文电商图文对齐 |
| Products-10K | 是 | CommerceVLM P0 | SKU 级细粒度视觉辨别/hard negative |
| ShareGPT4Video 子集 | 是 | CommerceVLM P1 | 通用视频时序能力 |
| E-VAds SFT / RL | 是 | CommerceVLM P1/P2/P4 | 电商视频域、商业语义 |
| **E-VAds Test** | **否** | Evaluation only | 防止 benchmark 泄漏 |
| 自建 CommerceEvent | 是 | P2/P3/P4 | Live2Shop 特有的结构化直播状态任务 |
| TAOBAO-MM | 是 | RecModel R0 | 电商长序列、召回和 CTR 排序 |
| KuaiLive-M3 | 是 | RecModel R1 | 直播动态内容、时序行为适配 |
| Live2Shop 自身回流 | 是 | RecModel R2 | 将 RecModel 适配到 commerce-space-v1 |
| eMCR | 否 | Evaluation only | 中文多条件电商检索外部评测 |
| 自建 manual gold | 否 | Validation/Test | CommerceEvent 的真实可靠评测 |

---

## 1. MUGE

用途：`CommerceVLM P0 Alignment Pre-training`

### 官方/公开入口

- 天池原始入口  
  https://tianchi.aliyun.com/muge
- ModelScope 可用镜像/测试数据页面  
  https://modelscope.cn/datasets/wangxingjun778/muge_test

### 说明

MUGE 是中文多模态电商场景数据，包含图文检索、caption 等任务。ModelScope 页面展示了
`query/image` pair，可以作为中文电商视觉-语言 alignment 数据。

**许可证提醒：** 当前 ModelScope 页面顶部和正文许可证描述存在不完全一致的情况。
求职/科研项目建议按更严格的非商业研究约束处理，并在 README 中保留原始来源。
不要把数据文件重新打包进自己的 GitHub 仓库。

### 安装

```bash
pip install -r data_tools/requirements.txt
```

### ModelScope 方式

```bash
python data_tools/prepare_muge.py \
  --modelscope-id wangxingjun778/muge_test \
  --image-out-dir data/raw/muge/images \
  --output data/processed/commerce_vlm/muge.jsonl
```

如果你从天池下载了原始版本，则先整理成 JSONL，再：

```bash
python data_tools/prepare_muge.py \
  --input-jsonl data/raw/muge/meta.jsonl \
  --root data/raw/muge \
  --output data/processed/commerce_vlm/muge.jsonl
```

目标格式：

```json
{
  "media": "/abs/path/797458.jpg",
  "media_type": "image",
  "prompt": "请根据商品图片生成准确、简洁的中文商品语义描述...",
  "target": "t恤 命运石之门",
  "retrieval_text": "t恤 命运石之门"
}
```

建议规模：`100k ~ 250k` pair。

---

## 2. Products-10K

用途：`P0 SKU/fine-grained alignment`

### 官方入口

https://products-10k.github.io/challenge.html

项目主页：

https://products-10k.github.io/

官方页面提供 OneDrive 和京东网盘下载入口。数据约 10,000 个 SKU、近 190k 商品图片，
来自京东真实商品识别场景。

### 许可证

官方条款限定**非商业科研/教育用途**。不要重新分发图片。

### 转换

如果图片目录本身按 SKU 分类：

```bash
python data_tools/prepare_products10k.py \
  --root data/raw/products10k \
  --output data/processed/commerce_vlm/products10k.jsonl
```

如果官方版本使用 annotation CSV：

```bash
python data_tools/prepare_products10k.py \
  --root data/raw/products10k \
  --annotation-csv data/raw/products10k/train.csv \
  --image-col image \
  --label-col class \
  --output data/processed/commerce_vlm/products10k.jsonl
```

### 为什么要它

不要把 Products-10K 当普通分类集。重点是：

- 同品牌/同系列高度相似 SKU；
- 包装规格、容量、颜色、型号差异；
- 为后续 preference hard negative 提供“看起来很像但不是同一商品”的样本。

---

## 3. ShareGPT4Video

用途：`P1 General Temporal Continual Pre-training`

### 官方入口

项目：
https://sharegpt4video.github.io/

Hugging Face：
https://huggingface.co/datasets/ShareGPT4Video/ShareGPT4Video

### 重要：不要下载全量

当前 Hugging Face repository 的完整视频文件约 **1.52 TB**；
`sharegpt4video_40k.jsonl` 约 361 MB、40,178 行。

Live2Shop 只需要 **3k~8k 个视频**。

先下载元数据：

```bash
hf download ShareGPT4Video/ShareGPT4Video \
  sharegpt4video_40k.jsonl \
  --repo-type dataset \
  --local-dir data/raw/sharegpt4video
```

不要“随机挑 5k 行然后下载媒体”：随机样本很容易散落到大量 zip archive。
仓库提供 archive-aware selector，优先选择样本最多的少数 archive，从源头控制下载量。

先只生成 5k 子集和所需 archive 清单：

```bash
python data_tools/select_sharegpt4video_subset.py \
  --metadata data/raw/sharegpt4video/sharegpt4video_40k.jsonl \
  --limit 5000 \
  --output-metadata data/raw/sharegpt4video/subset_5k.jsonl
```

确认磁盘空间后，只下载这些 archive 并解压：

```bash
python data_tools/select_sharegpt4video_subset.py \
  --metadata data/raw/sharegpt4video/sharegpt4video_40k.jsonl \
  --limit 5000 \
  --output-metadata data/raw/sharegpt4video/subset_5k.jsonl \
  --archive-dir data/raw/sharegpt4video/archives \
  --video-root data/raw/sharegpt4video/videos \
  --download
```

转换为训练 JSONL：

```bash
python data_tools/prepare_sharegpt4video.py \
  --metadata data/raw/sharegpt4video/subset_5k.jsonl \
  --video-root data/raw/sharegpt4video/videos \
  --limit 5000 \
  --output data/processed/commerce_vlm/sharegpt_video.jsonl
```

官方仓库也提供自己的辅助下载脚本：
https://huggingface.co/datasets/ShareGPT4Video/ShareGPT4Video/tree/main/user_download

许可证：CC BY-NC 4.0，只用于非商业研究/项目学习。

---

## 4. E-VAds

用途：

- P1 电商视频 domain CPT
- P2 电商 QA 辅助 SFT
- P4 RL prompts 辅助
- Test 完全 held out

官方 GitHub：

https://github.com/TaobaoTmall-AlgorithmProducts/E-VAds_Benchmark

Hugging Face：

https://huggingface.co/datasets/TaobaoTmall-AlgorithmProducts/E-VAds_Benchmark

公开 benchmark 规模：

- 总计 3,961 视频 / 19,785 QA
- SFT：376 视频 / 1,880 QA
- RL：196 视频 / 980 QA
- Test：3,389 视频 / 16,925 QA

### 下载

```bash
git clone https://github.com/TaobaoTmall-AlgorithmProducts/E-VAds_Benchmark.git \
  data/raw/e-vads-code

hf download TaobaoTmall-AlgorithmProducts/E-VAds_Benchmark \
  --repo-type dataset \
  --local-dir data/raw/e-vads
```

### 转换 SFT

上游 annotations 当前使用 Parquet。脚本同时支持 JSON/JSONL/Parquet：

```bash
python data_tools/prepare_evads.py \
  --split sft \
  --annotation data/raw/e-vads-code/annotations/EVAds_TRAIN_SFT.parquet \
  --video-root data/raw/e-vads/videos \
  --output data/processed/commerce_vlm/evads_sft.jsonl
```

RL：

```bash
python data_tools/prepare_evads.py \
  --split rl \
  --annotation data/raw/e-vads-code/annotations/EVAds_TRAIN_RL.parquet \
  --video-root data/raw/e-vads/videos \
  --output data/processed/commerce_vlm/evads_rl.jsonl
```

如果上游文件实际大小写/文件名发生变化，以官方 annotations 目录为准。

### 防泄漏规则

脚本对带 `test` 字样的训练输入直接拒绝。

**绝对不要：**

```text
E-VAds Test -> CPT
E-VAds Test -> SFT
E-VAds Test -> teacher label
E-VAds Test -> preference
```

Test 只在所有训练完成后运行官方 evaluation。

---

## 5. 自建 CommerceEvent P2 数据（商品详情页弱监督）

当前方案使用淘宝/天猫、京东商品详情页中的商家商品讲解视频。页面标题、属性和页面价只作为
离线弱监督；CommerceVLM 的训练输入和在线输入均只有视频、ASR、OCR。不得把页面描述或候选
商品放入最终学生 prompt，也不得让模型预测平台 `active_item_id`。

只采集有权用于本项目的数据。采集器使用正常浏览器会话，不绕过登录、验证码或反爬限制。
原始 HTML、截图和浏览器 profile 可能含敏感状态，必须放在已忽略的 `data/` 或 `profiles/` 下。

### 5.1 采集商品页、主详情和对应视频

URL 清单：

```json
{"url":"https://item.jd.com/123456.html","rights_basis":"research_authorized"}
{"url":"https://item.taobao.com/item.htm?id=123456","rights_basis":"research_authorized"}
```

首次登录建议用独立 profile 和可见浏览器：

```bash
pip install -r data_tools/requirements.txt
playwright install chromium

python data_tools/collect_product_pages.py \
  --input data/self/product_urls.jsonl \
  --output-root data/self/product_pages \
  --output-jsonl data/self/product_page_records.jsonl \
  --profile-dir profiles/p2-collector \
  --headed --interactive \
  --ocr-detail-images
```

若页面跳转到平台登录页或安全校验页，记录会标为 `access_blocked`，并以
`login_required`、`anti_bot_challenge` 等 `review_reason` 说明原因；采集器不会把登录页误当成
商品页，也不会尝试绕过访问控制。登录/安全校验页的 HTML 与截图不会落盘，避免保留临时登录
二维码或认证令牌；输入 URL 的签名、追踪参数也不会写入记录或控制台。

适配器只从主商品详情/主媒体区域提取文本和详情图片，并排除推荐、猜你喜欢、相似商品区域；
`--ocr-detail-images` 可补充图片式详情页中的文字。网络中出现的
视频不会自动视为主商品视频；只有主商品 DOM 关联达到阈值时才自动选择。多视频歧义会进入
`needs_video_review`，人工确认后在 URL 清单中填写 `video_url` 再采集。

#### 无桌面服务器的人工登录与视频入口核实

~~~bash
python data_tools/collect_product_pages.py \
  --url 'https://item.taobao.com/item.htm?id=商品ID' \
  --output-root data/self/product_pages \
  --output-jsonl data/self/product_page_records.jsonl \
  --profile-dir profiles/p2-taobao \
  --login-ui --login-ui-port 8765 --inspect-media
~~~

在自己电脑上执行下面的端口转发，或使用 IDE 的端口转发功能：

~~~bash
ssh -N -L 8765:127.0.0.1:8765 用户名@服务器地址
~~~

打开终端打印的完整 localhost 地址（包括 # 后的访问令牌）。操作页显示服务器浏览器的
实时画面，支持扫码登录、点击、输入和滚动。只监听服务器回环地址；界面截图与输入不落盘，
不要分享操作页地址。独立 profile 保留可持久化的登录会话。

发现登录、验证页或无法确认主商品详情时，程序等待人工处理。点击“完成，继续”后重新访问
原商品 URL，使用新响应检查，默认最多尝试 3 次，由 --login-attempts 配置。
每次操作页等待默认 1800 秒，由 --login-timeout-sec 配置；有效会话直接复用，失效时再次等待，
非交互运行则记录失败。有图形桌面的机器仍可使用 --headed --interactive。

--inspect-media 会在商品页确认可用后再暂停一次，供人工点击视频入口；普通采集可省略。
media_entries 记录播放器、blob 播放器、直接媒体链接、播放器页面链接和嵌入框架。
播放器页面链接只作为核实线索，不作为视频文件下载；主商品区域的直接媒体链接才进入选择。
人工打开的新窗口会接入同一操作界面。网络请求中的媒体仍需主商品关联证据，
不能仅因点击后出现就自动认定为目标商品视频。

淘宝适配器会展开“参数信息”和“图文详情”，优先定位主标题，并以商品 ID 校验页面内的主商品标题。
description_sources 明确标记描述来自图文文本还是结构化参数；图片式详情保留为独立图片，
data-src 等懒加载地址优先于占位图。没有启用 OCR 时，不会声称已经读取图片中的文字。

自动处理“直播讲解”时，采集器先开始观察，再点击主商品侧栏入口；仅在新出现的 Canvas/video
播放器可见、选中商品标题与主商品一致、主页面中只出现一个新 HLS 清单时自动关联。
证据保存在 live_video_association 和 selected_video.association 中。多个清单、商品不一致或
播放器已提前打开时保留人工复核状态。这个关联说明入口属于该商品，不保证整段视频只谈该商品。

HLS 下载优先使用系统 FFmpeg，缺失时使用 imageio-ffmpeg 自带的可执行文件，将流转封装为 MP4，
不重编码。视频时长不限于 1–5 分钟；后续切窗时应以实际媒体时长为准。

### 5.2 视频去重和按商品分组划分

```bash
python data_tools/build_product_page_manifest.py \
  --input data/self/product_page_records.jsonl \
  --output data/self/product_video_manifest.jsonl \
  --rejected-output data/self/product_video_rejected.jsonl
```

脚本按视频 SHA-256 去重；同一商品的所有视频/窗口固定进入同一 split。同一视频若归属多个商品，
不会静默选一个，而是拒绝并要求人工处理。

### 5.3 切窗、ASR、OCR 和页面证据预对齐

```bash
CUDA_VISIBLE_DEVICES=2 python data_tools/build_commerce_windows.py \
  --manifest data/self/product_video_manifest.jsonl \
  --output-root data/self/windows \
  --output-jsonl data/self/windows_with_page_facts.jsonl \
  --window-ms 5000 \
  --whisper-model small

python data_tools/align_page_video_evidence.py \
  --input data/self/windows_with_page_facts.jsonl \
  --output data/self/windows_for_teacher.jsonl
```

对齐器将页面事实标记为 `supported_text`、`unobserved_text` 或 `conflict_text`。它只判断 ASR/OCR；
视觉支持仍由多模态 Teacher 或人工审核决定。完整页面描述不会进入结构化候选事实；Teacher 只会
看到有长度上限的参考摘录，且该摘录也会在最终导出时删除。

不要只保留“商品清晰出现”的窗口。片头、转场、只口播未展示、无商品画面等负样本应保留，
并定期检查 `product_visible=false` 的比例，避免模型学成“任何窗口都强行输出商品属性”。

### 5.4 Teacher 标注、审计与无泄漏导出

```bash
CUDA_VISIBLE_DEVICES=0 python data_tools/teacher_label_commerce.py \
  --input data/self/windows_for_teacher.jsonl \
  --output data/self/teacher_0.jsonl \
  --shard-id 0 --num-shards 3

# 在 GPU 1/2 上分别运行 --shard-id 1/2 后合并：
cat data/self/teacher_{0,1,2}.jsonl > data/self/teacher_all.jsonl

python data_tools/audit_commerce_labels.py \
  --input data/self/teacher_all.jsonl \
  --out-dir data/self/audit
```

自动审计会检查页面有/视频无、页面与视频冲突、价格证据、引文真实性、schema、prompt 泄漏及
商品/视频跨 split 泄漏。人工在 `review.csv` 填写 `corrected_target_json` 后导入：

```bash
python data_tools/import_reviewed_labels.py \
  --review-csv data/self/audit/review.csv \
  --source-jsonl data/self/audit/needs_review.jsonl \
  --output data/self/audit/reviewed.jsonl

python data_tools/export_p2_student_samples.py \
  --input data/self/audit/accepted.jsonl \
  --input data/self/audit/reviewed.jsonl \
  --split train \
  --output data/processed/commerce_vlm/p2_sft.jsonl
```

导出器物理删除 `annotation`、`annotation_context`、页面候选事实与采集溯源，只保留训练所需
字段和非输入元数据。Teacher 标签不能直接作为最终训练文件。

### 5.5 Gold Set

Teacher label 不是评测真值。应从 train/val/test 商品组中分别抽样，人工核验至少 500 个窗口，
理想为 800–1000；Gold 仅用于 val/test，不回灌训练。

---

## 6. TAOBAO-MM

用途：`RecModel R0`

官方主页：

https://taobao-mm.github.io/

Hugging Face：

https://huggingface.co/datasets/TaoBao-MM/Taobao-MM

许可证：Apache-2.0，科研用途说明见官方页面。

公开统计：

- 8.79M users
- 35.4M items
- 99M labeled samples
- history up to 1000
- item SCL multimodal embedding = 128D
- 全量约 139 GB

### 下载

```bash
hf download TaoBao-MM/Taobao-MM \
  --repo-type dataset \
  --local-dir data/raw/taobao-mm
```

一个月版本可以只处理部分预连接 train shards。比如先 8 个：

```bash
python data_tools/prepare_taobao_mm.py \
  --train-glob 'data/raw/taobao-mm/train/train-shard-00000[0-7].parquet' \
  --max-rows 1500000 \
  --output-dir data/processed/rec/taobao
```

如果指标稳定，再扩到 16/32 shards。

脚本使用官方字段：

```text
150_2_180_c   历史商品 128D embedding sequence
205_c         target item 128D embedding
label_0       click/non-click
```

输出统一 NPZ schema，供 SessionRecModel 直接训练。

---

## 7. KuaiLive-M3

用途：`RecModel R1`

官方代码：

https://github.com/imgkkk574/KuaiLive-M3

Hugging Face：

https://huggingface.co/datasets/imgkkk2004/KuaiLive-M3

当前公开文件总量约 61 GB。

关键文件：

```text
user_id_set.csv
live_interaction.csv
live_like.csv
live_emb_128_ts/
  live_emb_128_ts_part*.parquet
```

官方 loader `klm3.py` 对 segment embedding 的定义：

```text
live_stream_id
segment_id
feature_128
timestamp
```

且 `live_interaction` 包含：

```text
user_id
live_id
live_play_start_timestamp
live_play_end_timestamp
play_duration
like_cnt
comment_cnt
follow_author_cnt
...
```

### 下载

最省事：

```bash
hf download imgkkk2004/KuaiLive-M3 \
  --repo-type dataset \
  --local-dir data/raw/kuailive-m3
```

磁盘紧张时，用 Hugging Face CLI 的 `--include` 只拉：

```text
user_id_set.csv
live_interaction.csv
live_like.csv
live_emb_128_ts/*
```

### 处理

先 1500 用户 + 6 个 segment embedding shard：

```bash
python data_tools/prepare_kuailive_m3.py \
  --data-dir data/raw/kuailive-m3 \
  --output-dir data/processed/rec/kuailive \
  --users 1500 \
  --max-segment-shards 6
```

如果命中的 segment embedding 太少：

```text
increase --max-segment-shards
```

建议最终：

```text
users = 1500~3000
segment shards = 6~18
```

这里保留真实 timestamped segment embedding，而不是把整场直播压成静态 room embedding。

---

## 8. Live2Shop Commerce Bridge

TAOBAO-MM 的 128D、KuaiLive-M3 的 128D 和 Live2Shop 的 128D **不是同一语义空间**。

因此 R1 结束后必须重置 `content_adapter/item_adapter`，再适配：

```text
commerce-space-v1
```

### 冷启动：合成 session

先把真实商品库编码：

```bash
python scripts/encode_item_catalog.py \
  --checkpoint checkpoints/commerce-vlm/p4/final \
  --catalog-jsonl data/catalog/items.jsonl \
  --output data/processed/catalog/item_embeddings.parquet
```

然后：

```bash
python data_tools/bootstrap_commerce_sessions.py \
  --embeddings data/processed/catalog/item_embeddings.parquet \
  --catalog-jsonl data/catalog/items.jsonl \
  --sessions 30000 \
  --output-dir data/processed/rec/commerce_bridge
```

这批数据**只作为 adapter bootstrap**，不能拿来宣称真实推荐业务提升。

### 系统上线后：真实事件替换 bootstrap

`lake-sink` 会把 `behavior.attributed` 原始 protobuf 写入 MinIO。

把对应 Parquet 拉到本地后：

```bash
export PYTHONPATH=$PWD/gen/python:$PYTHONPATH

python data_tools/build_commerce_bridge_from_lake.py \
  --parquet-glob 'data/lake/behavior_attributed/**/*.parquet' \
  --item-embeddings data/processed/catalog/item_embeddings.parquet \
  --output-dir data/processed/rec/commerce_bridge_real
```

真实回流量足够后，用 real 数据重新跑 R2。

---

## 9. eMCR：只做外部评测

官方：

https://huggingface.co/datasets/alibabagroup/eMCR

下载：

```bash
hf download alibabagroup/eMCR \
  --repo-type dataset \
  --local-dir data/eval/emcr
```

公开统计：

- 1,399 queries
- 36,722 products
- 42,022 relevance judgments
- 48 task types
- 4-level relevance

许可证：Apache-2.0。

**不要拿它训练 Live2Shop。**

它的价值是作为完全外部的中文电商复杂检索测试，检验 commerce representation 的泛化。

---

## 10. 推荐目录

```text
data/
├── raw/
│   ├── muge/
│   ├── products10k/
│   ├── sharegpt4video/
│   ├── e-vads/
│   ├── taobao-mm/
│   └── kuailive-m3/
├── self/
│   ├── originals/
│   ├── windows/
│   ├── audit/
│   └── gold.jsonl
├── processed/
│   ├── commerce_vlm/
│   ├── rec/
│   └── catalog/
└── eval/
    └── emcr/
```

不要把 `data/` 提交到 Git。
