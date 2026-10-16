# Live2Shop 三平台直播采集器：无桌面服务器版

这是上一版 `live2shop_live_collector` 的服务器升级版。

目标平台仍固定为：

- 抖音
- 小红书
- 淘宝

数据目标仍然是：

```text
120 个直播间
= 抖音 40 + 小红书 40 + 淘宝 40

每间约 15 分钟
≈ 30 小时直播
≈ 21,600 个 5 秒 CommerceEvent windows
```

采集后的数据结构完全不变：

```text
data/self/raw_live/<room_id>/
├── live.mp4
├── room.json
├── catalog.json
├── timeline.csv
├── markers.jsonl
├── screenshots/
└── ffmpeg.log
```

后续仍然直接接：

```text
make_manifests.py
    ↓
build_commerce_windows.py
    ↓
5 秒 window + ASR + OCR + room_candidates
    ↓
Qwen3-VL teacher
    ↓
audit / human review
    ↓
P2 / P3 / P4
```

---

## 1. 服务器版架构

服务器没有物理桌面也没有关系。

容器内部会建立：

```text
Xvfb :99
  ↓
1280×720 virtual display
  ↓
Openbox
  ↓
Playwright headed Chromium
  ↓
抖音 / 小红书 / 淘宝
```

音频：

```text
Chromium
  ↓
PulseAudio virtual sink: live2shop
  ↓
live2shop.monitor
  ↓
FFmpeg
```

远程查看：

```text
x11vnc
  ↓
container :5901
  ↓
Docker 仅映射 server 127.0.0.1:5901
  ↓
SSH tunnel
  ↓
本地 VNC Viewer
```

因此不需要：
- 服务器真实显示器；
- 服务器真实声卡；
- Ubuntu 宿主机桌面环境；
- 修改 NVIDIA/CUDA。

---

# 2. 容器版本

固定使用：

```text
mcr.microsoft.com/playwright/python:v1.63.0-noble
```

Python 依赖固定：

```text
playwright==1.63.0
```

二者必须匹配。

这个容器基于 Ubuntu 24.04 Noble，因此即使你的宿主机仍是 Ubuntu 18.04，浏览器环境也不会直接依赖宿主机的老系统库。

---

# 3. 正确放置目录

推荐：

```text
~/data_desk/live2shop-v1.0/
├── data_tools/
├── ml/
├── data/
├── ...
└── live2shop_live_collector_server/
    ├── Dockerfile
    ├── docker-compose.yml
    ├── collect_live.py
    ├── make_plan.py
    ├── make_manifests.py
    ├── audit_collection.py
    ├── README_SERVER_CN.md
    └── server/
```

默认情况下：

```text
live2shop_live_collector_server 的父目录
=
Live2Shop repo root
```

如果你放在别处：

```bash
export LIVE2SHOP_ROOT=/absolute/path/to/live2shop-v1.0
```

再执行服务器脚本。

---

# 4. 第一次先检查宿主机

```bash
cd ~/data_desk/live2shop-v1.0/live2shop_live_collector_server

./server/check_host.sh
```

会检查：

```text
Docker CLI
Docker daemon
Docker Compose
repo 写权限
磁盘余量
VNC 端口
```

如果：

```text
docker info
```

本身就 permission denied，需要先解决当前 SSH 用户访问 Docker daemon 的权限。

---

# 5. 一条命令启动虚拟采集桌面

```bash
cd ~/data_desk/live2shop-v1.0/live2shop_live_collector_server

./server/start_server.sh
```

第一次会执行 Docker build。

它会自动启动：

```text
Xvfb
Openbox
PulseAudio
live2shop null sink
x11vnc
```

然后自动 smoke test：

```text
Playwright version == 1.63.0
Xvfb 可访问
PulseAudio 可访问
live2shop.monitor 存在
x11vnc 运行
FFmpeg 可以 x11grab
headed Chromium 可以真正启动并截图
```

只有通过以后才会打印：

```text
Live2Shop virtual capture desktop is READY.
```

查看：

```bash
./server/status.sh
```

完整复检：

```bash
./server/check_all.sh
```

日志：

```bash
./server/logs.sh
```

停止：

```bash
./server/stop_server.sh
```

---

# 6. 连接虚拟桌面

Docker 默认只暴露：

```text
server 127.0.0.1:5901
```

不要直接开放公网 5901。

在你自己的电脑执行：

```bash
ssh -N \
  -L 5901:127.0.0.1:5901 \
  user25@你的服务器地址
```

保持这个 SSH tunnel 运行。

然后本地 VNC Viewer：

```text
127.0.0.1:5901
```

应该看到一个 1280×720 虚拟桌面。

如果本地 5901 已占用：

```bash
ssh -N \
  -L 5902:127.0.0.1:5901 \
  user25@你的服务器地址
```

然后 VNC Viewer 连：

```text
127.0.0.1:5902
```

---

# 7. 第一次不要直接采 120 间

先做三平台 smoke test：

```text
douyin_001
xiaohongshu_001
taobao_001
```

各 1 间。

抖音例子：

```bash
./server/collect_room.sh \
  --room-id douyin_001 \
  --url "真实抖音直播间URL" \
  --category "服饰鞋包" \
  --minutes 15 \
  --viewer-count 86
```

wrapper 会自动补：

```text
--root data/self/raw_live
--profile-root profiles
--display :99.0
--size 1280x720
--audio-source live2shop.monitor
--browser-channel chromium
```

VNC 中会看到 Chromium 出现。

第一次手工：

1. 登录；
2. 扫码；
3. 手工处理验证码；
4. 打开直播；
5. 确认播放器正常；
6. 确认没有静音；
7. 回 SSH terminal 按 Enter。

---

# 8. 登录 profile

登录状态保存到宿主机：

```text
profiles/douyin/
profiles/xiaohongshu/
profiles/taobao/
```

不是只存在 Docker 临时层。

因此容器：

```text
stop
start
rebuild
```

后 profile 仍会保留。

这个目录包含登录会话：

**不要提交 Git，不要上传，不要分享。**

---

# 9. 建议正式采集放到 tmux

VNC 可以随时断开。

但是交互式：

```text
collect_room.sh
```

最好放在 tmux 中，避免 SSH 网络断线影响进程。

例如：

```bash
tmux new -s livecap
```

然后：

```bash
cd ~/data_desk/live2shop-v1.0/live2shop_live_collector_server

./server/collect_room.sh \
  --room-id douyin_001 \
  --url "..." \
  --category "服饰鞋包" \
  --minutes 15
```

退出 tmux 而不停止：

```text
Ctrl+B
D
```

回来：

```bash
tmux attach -t livecap
```

---

# 10. 录制期间命令不变

添加商品：

```text
add 1|男士羽绒服|服饰>男装>羽绒服|示例品牌|39900
```

当前商品：

```text
item 1
```

切换：

```text
item 2
```

无商品 / 无法可靠确认：

```text
blank
```

困难事件：

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

备注：

```text
note 主播手中已换新品，但桌面仍保留上一件商品
```

提前结束：

```text
stop
```

---

# 11. 先测试“虚拟桌面能不能录”

启动服务器桌面后：

```bash
./server/test_video.sh
```

它会在 container 中生成：

```text
/tmp/live2shop_video_test.mp4
```

复制到 host：

```bash
docker cp \
  live2shop-capture:/tmp/live2shop_video_test.mp4 \
  ./video_test.mp4
```

下载播放。

确认：

```text
画面不是黑屏
分辨率正常
浏览器可见
网页视频确实在动
```

---

# 12. 再单独测试声音

先在 VNC 里打开一个**确认正在发声**的视频或直播。

然后：

```bash
./server/test_audio.sh
```

生成：

```text
/tmp/live2shop_audio_test.wav
```

复制：

```bash
docker cp \
  live2shop-capture:/tmp/live2shop_audio_test.wav \
  ./audio_test.wav
```

下载试听。

如果没有声音：

```bash
docker compose exec --user pwuser collector pactl info
docker compose exec --user pwuser collector pactl list short sinks
docker compose exec --user pwuser collector pactl list short sources
docker compose exec --user pwuser collector pactl list sink-inputs
```

应该至少有：

```text
live2shop
live2shop.monitor
```

直播发声时 `sink-inputs` 里应该有 Chromium 音频流。

---

# 13. Chromium 能登录，但某个平台直播播放异常怎么办

默认先用 Playwright 官方镜像里的 Chromium。

三个平台都能播：

```text
什么都不要改
```

如果只有某个平台：

```text
登录正常
页面正常
但直播黑屏/媒体不兼容
```

可以尝试 branded Google Chrome：

```bash
./server/install_chrome.sh
```

然后把采集器 browser channel 改为：

```text
chrome
```

默认不在镜像 build 时强制安装 Chrome，是为了减少：
- Google 下载失败；
- Docker build 被墙/代理卡住；
- 额外镜像体积。

---

# 14. 服务器代理的坑

如果你的宿主机以前设置：

```text
http_proxy=http://127.0.0.1:17890
https_proxy=http://127.0.0.1:17890
```

不要直接照搬到容器。

因为：

```text
container 127.0.0.1
≠
host 127.0.0.1
```

如果抖音/小红书/淘宝本来能直连：

**采集容器不配置代理最简单。**

如果必须经过代理，需要让代理地址能够从 Docker bridge 访问，然后：

```bash
cp server/server.env.example server/server.env
```

在 `server.env` 中填写 container-reachable proxy。

---

# 15. 自定义分辨率/VNC 端口

```bash
cp server/server.env.example server/server.env
```

例如：

```bash
SCREEN_WIDTH=1600
SCREEN_HEIGHT=900
VNC_HOST_PORT=5902
```

重启：

```bash
./server/stop_server.sh
./server/start_server.sh
```

如果 host VNC 端口改为 5902，本地 SSH tunnel 对应：

```bash
ssh -N \
  -L 5901:127.0.0.1:5902 \
  user25@SERVER
```

本地仍可以连接：

```text
127.0.0.1:5901
```

---

# 16. 为什么 browser sandbox 默认关

当前服务器 workflow 中 Chromium：

```text
运行在 Docker 容器内
+
运行用户是非 root pwuser
```

为了提高老宿主机 Docker/kernel 兼容性，默认：

```text
LIVE2SHOP_DISABLE_BROWSER_SANDBOX=1
```

不会要求 Ubuntu 18.04 宿主机额外配置 Chromium user namespace。

如果以后你自己配置并验证了 Chromium sandbox，可以在：

```text
server/server.env
```

改：

```bash
LIVE2SHOP_DISABLE_BROWSER_SANDBOX=0
```

但 V1 数据采集不建议先增加这个变量。

---

# 17. UID/GID 为什么自动映射

容器 entrypoint 会读取：

```text
COLLECTOR_UID
COLLECTOR_GID
```

`start_server.sh` 自动设置为当前服务器用户：

```bash
id -u
id -g
```

然后把容器的 `pwuser` 对齐到这个 UID/GID。

因此生成：

```text
data/self/raw_live/*
profiles/*
```

通常不会变成 host 上的 root-owned 文件。

---

# 18. 宿主机 Ubuntu 18.04 不需要改

宿主机只需要：

```text
Docker daemon
Docker Compose
磁盘
网络
```

不要为了直播采集去：

```text
升级 Ubuntu
装桌面
修改 NVIDIA driver
修改 CUDA
apt 安装 Chromium 到宿主机
```

浏览器和桌面依赖都封在 Ubuntu 24.04 container 里。

---

# 19. 三平台正确验证顺序

请严格：

```text
A.
./server/check_host.sh

B.
./server/start_server.sh

C.
SSH tunnel + VNC
能看到虚拟桌面

D.
./server/test_video.sh
X11 录屏正常

E.
VNC 中播放普通有声网页
./server/test_audio.sh
PulseAudio 正常

F.
抖音登录 + 任意直播正常播放

G.
小红书登录 + 任意直播正常播放

H.
淘宝登录 + 任意直播正常播放

I.
三个平台各录 1×15min

J.
检查：
live.mp4
catalog.json
timeline.csv
markers.jsonl

K.
这 3 间跑 build_commerce_windows.py

L.
确认：
ASR
OCR
room_candidates

M.
扩大到：
10 + 10 + 10

N.
audit_collection.py

O.
最后：
40 + 40 + 40
```

---

# 20. 采完后的流程不变

先整场切 split：

```bash
python live2shop_live_collector_server/make_manifests.py \
  --root data/self/raw_live \
  --out-dir data/self/manifests \
  --seed 42
```

再在原来的 data env：

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

Val/Test 同理。

注意：

```text
capture Docker
```

和：

```text
live2shop-data Conda
live2shop-commerce Conda
```

完全独立。

---

# 21. 最短工作流

服务器：

```bash
cd ~/data_desk/live2shop-v1.0/live2shop_live_collector_server
./server/start_server.sh
```

本地电脑：

```bash
ssh -N -L 5901:127.0.0.1:5901 user25@SERVER
```

VNC：

```text
127.0.0.1:5901
```

服务器：

```bash
tmux new -s livecap
```

然后：

```bash
cd ~/data_desk/live2shop-v1.0/live2shop_live_collector_server

./server/collect_room.sh \
  --room-id douyin_001 \
  --url "真实直播URL" \
  --category "服饰鞋包" \
  --minutes 15
```

第一次不要再多做别的。

先让：

```text
抖音 1
小红书 1
淘宝 1
```

全部跑通，再扩采。
