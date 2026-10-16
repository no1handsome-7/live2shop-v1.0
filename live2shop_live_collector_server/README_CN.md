# Live2Shop 三平台直播数据采集器

目标平台固定为：

- 抖音（douyin）
- 小红书（xiaohongshu）
- 淘宝（taobao）

这套工具实现的是 **可审计、UI 辅助的采集方案**：

1. 用正常可见浏览器打开用户提供的直播间 URL；
2. 登录/验证码完全由人手工完成；
3. 不调用平台私有 API，不绕过 CAPTCHA/风控，不提取签名接口；
4. FFmpeg 录制屏幕区域 + 系统声音；
5. 同时维护 room-local `catalog.json`；
6. 实时标记 `active item / blank / hard event`；
7. 最终生成与 Live2Shop 后续 `build_commerce_windows.py` 兼容的目录；
8. 再按整场直播拆分 train/val/test。

> 建议只采集公开可访问或你已获授权用于研究/训练的直播内容，并遵守平台规则、版权和隐私要求。不要为了采集去绕过登录限制、验证码、DRM、反爬或访问控制。

---

## 1. 数据规模

V1 固定：

- 120 个直播间
- 抖音 40
- 小红书 40
- 淘宝 40
- 每间 15 分钟
- 总时长约 30 小时
- 5 秒窗口约 21,600 个

品类总配额：

| 类别 | 房间数 |
|---|---:|
| 服饰鞋包 | 25 |
| 美妆护肤 | 15 |
| 食品饮料 | 15 |
| 家居用品 | 15 |
| 3C电子 | 15 |
| 家电 | 10 |
| 母婴 | 5 |
| 运动户外 | 5 |
| 珠宝饰品 | 5 |
| 其他 | 10 |

`make_plan.py` 会把这些品类尽量均匀分给三个平台，并保证每个平台 40 间。

原先“在线人数 <= 300”的标准保留为**优先筛选规则**：
- 页面明确显示观看人数时，优先 `<=300`；
- 如果平台只显示热度/人气而不是可比的真实人数，不要硬换算，`observed_viewers` 留空并在 notes 记录“低热度”。

---

## 2. 为什么必须保留 catalog

最终 CommerceVLM 不是让模型凭空“生成 SKU”，而是：

`5s video + ASR + OCR + room_candidates -> CommerceEvent`

因此每个直播间必须至少保存：

```text
data/self/raw_live/
└── douyin_001/
    ├── live.mp4
    ├── room.json
    ├── catalog.json
    ├── timeline.csv
    ├── markers.jsonl
    ├── screenshots/
    └── ffmpeg.log
```

`catalog.json` 必须是 **JSON array**：

```json
[
  {
    "item_id": 1,
    "title": "男士羽绒服",
    "category_path": ["服饰", "男装", "羽绒服"],
    "brand": "示例品牌",
    "style_tags": [],
    "attributes": {},
    "price_cent": 39900
  }
]
```

这里的 `item_id` 可以是**本直播间本地 ID**（1、2、3……），不需要逆向获取平台内部 SKU。只要：
- room 内唯一；
- catalog 和 timeline 使用同一个 ID；
- 后续 teacher / human label 都从 room_candidates 中选择即可。

---

## 3. 最推荐的部署方式

### 方式 A：GUI 采集机（推荐）

直播平台登录、验证码、视频播放都更适合有桌面的电脑。

采集机负责：
- Chrome
- Playwright
- FFmpeg
- 屏幕+系统音频录制

训练服务器负责：
- 5 秒切窗
- faster-whisper ASR
- RapidOCR
- teacher labeling
- P2/P3/P4

采集完成后把：

```text
data/self/raw_live/
```

复制到训练服务器。

### 方式 B：直接在 Linux GUI 服务器采

要求：
- X11 图形桌面
- Chrome/Chromium 可以正常播放直播
- PulseAudio 有 monitor source
- FFmpeg 可用

如果你的训练服务器本身没有桌面会话，不建议为了采集去折腾虚拟桌面/验证码；直接用一台普通 GUI 机器采集更稳。

---

## 4. 环境安装

建议单独建采集环境：

```bash
conda create -n live2shop-capture python=3.11 -y
conda activate live2shop-capture

pip install -r requirements.txt
playwright install chromium
```

检查 FFmpeg：

```bash
ffmpeg -version | head
```

如果脚本能找到系统 Google Chrome，会优先用 Chrome；否则回退到 Playwright 自带 Chromium。

---

## 5. Linux 下找到系统音频 monitor

运行：

```bash
pactl list short sources
```

通常会看到类似：

```text
alsa_output.pci-0000_00_1f.3.analog-stereo.monitor
alsa_input.pci-0000_00_1f.3.analog-stereo
```

请选择带 `.monitor` 的那一行作为：

```bash
--audio-source ...
```

先做一个 10 秒测试：

```bash
ffmpeg \
  -f pulse \
  -i alsa_output.pci-0000_00_1f.3.analog-stereo.monitor \
  -t 10 \
  /tmp/audio_test.wav
```

然后播放：

```bash
ffplay /tmp/audio_test.wav
```

确保录到的是浏览器直播声音，而不是麦克风。

---

## 6. 先生成 120 间采集计划

在 Live2Shop 仓库根目录：

```bash
python /path/to/live2shop_live_collector/make_plan.py \
  --out data/self/collection_plan.csv
```

输出 120 行。

建议把下面三列人工补上：

```text
room_url
observed_viewers
notes
```

`status` 可以从：

```text
todo
selected
done
rejected
```

中维护。

筛选时优先：
- 真实带货直播；
- 低热度/低观看人数；
- 画质和声音可用；
- 有真实商品切换；
- 商品候选能人工确认；
- 品类不要重复堆在某一类。

淘汰：
- 长时间没有带货；
- 画质太差；
- 音频长期不可用；
- 商品完全无法区分；
- 重复房间/重复录制；
- 不适合研究采集的内容。

---

## 7. 第一次登录每个平台

脚本使用 persistent profile：

```text
profiles/douyin/
profiles/xiaohongshu/
profiles/taobao/
```

第一次跑某个平台时：
1. 浏览器弹出；
2. 手工登录；
3. 遇到验证码手工处理；
4. 打开直播；
5. 确认声音正常；
6. 回终端按 Enter 开始。

以后同平台会复用本地登录状态。

**不要把 `profiles/` 上传、提交 Git 或分享给别人。**

---

## 8. 采第一间直播

示例：

```bash
python /path/to/live2shop_live_collector/collect_live.py \
  --root data/self/raw_live \
  --room-id douyin_001 \
  --url "把真实直播间URL放这里" \
  --category "服饰鞋包" \
  --minutes 15 \
  --viewer-count 86 \
  --display :0.0 \
  --size 1280x720 \
  --x 0 \
  --y 0 \
  --audio-source "alsa_output.xxx.monitor"
```

如果页面没有显示可比的真实观看人数，省略：

```bash
--viewer-count
```

### 浏览器位置

默认 FFmpeg 录：

```text
DISPLAY=:0.0
左上角 x=0, y=0
1280x720
```

开始前请确保：
- 直播视频位于这个区域；
- 不要让终端窗口挡住直播；
- 如果需要，可以调整 `--x --y --size`。

---

## 9. 录制期间的终端命令

脚本开始后可以一边录，一边输入命令。

### 9.1 添加 catalog 商品

```text
add 1|男士羽绒服|服饰>男装>羽绒服|示例品牌|39900
```

格式：

```text
add ITEM_ID|TITLE|CATEGORY_PATH|BRAND|PRICE_CENT
```

价格单位是“分”：
- 399 元 -> 39900
- 99.9 元 -> 9990

信息暂时不确定时可留空：

```text
add 2|黑色针织衫|服饰>女装||12900
```

### 9.2 标当前商品

```text
item 1
```

表示从当前时间开始，活动商品是 catalog 中的 item 1。

切品时：

```text
item 2
```

### 9.3 标 blank

主播聊天、商品消失、无法可靠确认当前商品时：

```text
blank
```

不要为了“让数据更完整”硬猜 SKU。

### 9.4 标困难事件

```text
event switch
event multi
event price
event promo
event blank
event occluded
event blur
event normal
```

这些 event tag 可以重叠，主要用于后续审计采集覆盖率。

### 9.5 加备注

```text
note 主播把旧商品放在桌上，新商品开始试穿
```

### 9.6 提前停止

```text
stop
```

---

## 10. blank 的目标

最终 P2 数据里：

```text
product_visible=false
```

建议约占 **25%–35%**。

因此：
- 不要只录“商品最精彩的 15 分钟”；
- 聊天、切品间隙、商品离开画面、无法可靠匹配都很重要；
- `blank` 是抑制模型“凭空认商品”的关键负样本。

注意：采集时 hard-event 标签不是互斥分桶，因此不要要求所有事件比例加起来等于 100%。

---

## 11. 单间采集后的文件

例如：

```text
data/self/raw_live/douyin_001/
├── live.mp4
├── room.json
├── catalog.json
├── timeline.csv
├── markers.jsonl
├── screenshots/
└── ffmpeg.log
```

`timeline.csv`：

```csv
start_ms,end_ms,item_id,state
0,15000,,blank
15000,92000,1,item
92000,128000,2,item
...
```

其中：
- `item`：有明确 room-local SKU；
- `blank`：无商品/无法确认；
- `unknown`：采集员尚未明确标记，不应直接当 blank ground truth。

---

## 12. DOM 文本默认不采

脚本默认**不保存**页面 body 文本，因为其中可能混入：
- 用户名；
- 弹幕；
- 与研究任务无关的个人信息。

如果你确认研究需要并且有合适的数据处理依据，可显式：

```bash
--save-dom-text
```

脚本只在可见文本发生变化时写 `dom_changes.jsonl`，不会调用隐藏接口。

---

## 13. 每采 10～20 间跑一次审计

```bash
python /path/to/live2shop_live_collector/audit_collection.py \
  --root data/self/raw_live
```

重点看：

```text
platforms
categories
total_hours
avg_minutes
avg_catalog_items
avg_marked_blank_ratio
event_markers
errors
```

目标最终：

```text
platforms:
  douyin: 40
  xiaohongshu: 40
  taobao: 40

total_hours ≈ 30
avg_minutes ≈ 15
```

如果 blank 远低于 25%，后面应优先补采 blank，而不是复制已有 blank 窗口。

---

## 14. 采完后按“整场直播”拆 train / val / test

绝对不要：

```text
同一直播 0-5s -> train
同一直播 5-10s -> test
```

必须先按 room/source video 切分。

执行：

```bash
python /path/to/live2shop_live_collector/make_manifests.py \
  --root data/self/raw_live \
  --out-dir data/self/manifests \
  --seed 42
```

如果三个平台都恰好采满 40 间，会得到：

```text
train:
  douyin 30
  xiaohongshu 30
  taobao 30
  total 90

val:
  5 + 5 + 5 = 15

test:
  5 + 5 + 5 = 15
```

输出：

```text
data/self/manifests/train.jsonl
data/self/manifests/val.jsonl
data/self/manifests/test.jsonl
```

每行：

```json
{
  "video": "/abs/path/live.mp4",
  "room_id": "douyin_001",
  "catalog": "/abs/path/catalog.json",
  "source": "douyin"
}
```

---

## 15. 接回你现有 Live2Shop pipeline

在训练服务器上：

```bash
conda activate live2shop-data
```

Train：

```bash
CUDA_VISIBLE_DEVICES=7 \
python data_tools/build_commerce_windows.py \
  --manifest data/self/manifests/train.jsonl \
  --output-root data/self/windows/train \
  --output-jsonl data/self/train_windows.jsonl \
  --window-ms 5000 \
  --whisper-model small
```

Val：

```bash
CUDA_VISIBLE_DEVICES=7 \
python data_tools/build_commerce_windows.py \
  --manifest data/self/manifests/val.jsonl \
  --output-root data/self/windows/val \
  --output-jsonl data/self/val_windows.jsonl \
  --window-ms 5000 \
  --whisper-model small
```

Test：

```bash
CUDA_VISIBLE_DEVICES=7 \
python data_tools/build_commerce_windows.py \
  --manifest data/self/manifests/test.jsonl \
  --output-root data/self/windows/test \
  --output-jsonl data/self/test_windows.jsonl \
  --window-ms 5000 \
  --whisper-model small
```

理论上 120×15 分钟全部有效时：

```text
约 21,600 个 5s windows
train ≈ 16,200
val ≈ 2,700
test ≈ 2,700
```

之后继续你已有流程：
- train windows -> Qwen3-VL teacher
- audit
- human review
- P2 SFT
- P3 DPO
- P4 challenger
- val/test held-out room -> manual gold

---

## 16. Gold Set 注意

`timeline.csv` 是采集阶段的**粗时间线**，主要用于数据审计和快速定位。

真正的 val/test manual Gold 仍建议按你现有模板人工精标：

```text
video,room_id,start_ms,end_ms,item_id,category,brand,style_tags,color,price_cent,promotion_tags
```

Blank 行的商品字段全部留空。

Gold 至少 500–1000 个 5 秒窗口，并且全部来自 held-out 的 val/test 直播，不能从 train 中抽。

---

## 17. 建议实际执行顺序

```text
1. make_plan.py
2. 先采 3 间 smoke test（抖音/小红书/淘宝各 1）
3. 检查 live.mp4 是否有画面+音频
4. 检查 catalog.json / timeline.csv
5. 用这 3 间跑 build_commerce_windows.py
6. 确认 ASR/OCR/room_candidates 正常
7. 再扩大到每平台 10 间
8. audit_collection.py
9. 最后采满 40+40+40
10. make_manifests.py
11. build_commerce_windows.py
12. teacher / audit / human review
```

不要一上来采满 120 间再发现声音没录到或 catalog 格式不兼容。
