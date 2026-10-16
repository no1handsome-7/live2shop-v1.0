#!/usr/bin/env python3
"""
Live2Shop visible-UI live-room collector.

Design goals:
- User supplies a public/authorized live-room URL.
- Login is manual in a normal headed browser; a persistent browser profile keeps the session.
- No private API reverse engineering, CAPTCHA bypass, or anti-bot evasion.
- FFmpeg captures the visible X11 region + PulseAudio monitor source.
- Operator can create room-local catalog items and mark active-item/blank/event transitions.
- Output is directly usable to build Live2Shop manifests.

Linux/X11 is the primary capture mode. For a headless training server, collect on a GUI
workstation and copy room folders to the server afterwards.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUPPORTED = {
    "douyin": ("douyin.com",),
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "taobao": ("taobao.com", "tb.cn", "tmall.com"),
}

EVENT_TYPES = {
    "normal", "switch", "multi", "price", "promo",
    "blank", "occluded", "blur", "other",
}

def iso_now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

def detect_platform(url: str) -> str | None:
    u = url.lower()
    for p, domains in SUPPORTED.items():
        if any(d in u for d in domains):
            return p
    return None

def parse_size(s: str):
    m = re.fullmatch(r"(\d+)x(\d+)", s.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError("size must look like 1280x720")
    return int(m.group(1)), int(m.group(2))

def atomic_json(path: Path, obj: Any):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def append_jsonl(path: Path, obj: Any):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def coerce_item_id(s: str):
    s = s.strip()
    return int(s) if re.fullmatch(r"\d+", s) else s

@dataclass
class RoomPaths:
    root: Path
    room_id: str

    @property
    def room(self): return self.root / self.room_id
    @property
    def room_json(self): return self.room / "room.json"
    @property
    def catalog(self): return self.room / "catalog.json"
    @property
    def markers(self): return self.room / "markers.jsonl"
    @property
    def timeline(self): return self.room / "timeline.csv"
    @property
    def dom_changes(self): return self.room / "dom_changes.jsonl"
    @property
    def screenshots(self): return self.room / "screenshots"
    @property
    def ffmpeg_log(self): return self.room / "ffmpeg.log"
    @property
    def partial_mkv(self): return self.room / "capture.mkv"
    @property
    def live_mp4(self): return self.room / "live.mp4"

def load_catalog(path: Path):
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON array")
    return data

def upsert_catalog(path: Path, item: dict):
    data = load_catalog(path)
    idx = {str(x.get("item_id")): i for i, x in enumerate(data)}
    k = str(item["item_id"])
    if k in idx:
        data[idx[k]].update(item)
    else:
        data.append(item)
    atomic_json(path, data)

def parse_add(payload: str):
    # add ITEM_ID|TITLE|CATEGORY_PATH|BRAND|PRICE_CENT
    # CATEGORY_PATH uses ">" as separator.
    parts = [x.strip() for x in payload.split("|")]
    if len(parts) < 2:
        raise ValueError("用法: add 1|商品标题|服饰>男装>夹克|品牌|39900")
    while len(parts) < 5:
        parts.append("")
    item_id, title, category_path, brand, price_cent = parts[:5]
    if not item_id or not title:
        raise ValueError("item_id 和 title 必填")
    price = None
    if price_cent:
        price = int(price_cent)
        if price < 0:
            raise ValueError("price_cent must be >= 0")
    return {
        "item_id": coerce_item_id(item_id),
        "title": title,
        "category_path": [x.strip() for x in category_path.split(">") if x.strip()],
        "brand": brand or None,
        "style_tags": [],
        "attributes": {},
        "price_cent": price,
    }

def build_ffmpeg_cmd(args, paths: RoomPaths):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found in PATH")
    w, h = parse_size(args.size)
    x11_input = f"{args.display}+{args.x},{args.y}"
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
        "-thread_queue_size", "1024",
        "-f", "x11grab",
        "-framerate", str(args.fps),
        "-video_size", f"{w}x{h}",
        "-i", x11_input,
    ]
    if not args.no_audio:
        if not args.audio_source:
            raise RuntimeError(
                "需要 --audio-source（运行: pactl list short sources | grep monitor），"
                "或显式使用 --no-audio。"
            )
        cmd += [
            "-thread_queue_size", "1024",
            "-f", "pulse",
            "-i", args.audio_source,
        ]
    cmd += [
        "-t", str(args.minutes * 60),
        "-c:v", "libx264",
        "-preset", args.preset,
        "-crf", str(args.crf),
        "-pix_fmt", "yuv420p",
    ]
    if not args.no_audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-af", "aresample=async=1"]
    cmd += [str(paths.partial_mkv)]
    return cmd

def remux_to_mp4(paths: RoomPaths):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not paths.partial_mkv.exists():
        return False
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(paths.partial_mkv),
        "-map", "0",
        "-c", "copy",
        "-movflags", "+faststart",
        str(paths.live_mp4),
    ]
    r = subprocess.run(cmd)
    if r.returncode == 0 and paths.live_mp4.exists() and paths.live_mp4.stat().st_size > 0:
        return True
    # Fallback: re-encode if container/codec copy fails.
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(paths.partial_mkv),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(paths.live_mp4),
    ]
    r = subprocess.run(cmd)
    return r.returncode == 0 and paths.live_mp4.exists() and paths.live_mp4.stat().st_size > 0

def input_worker(q: queue.Queue):
    help_text = (
        "\n采集中可输入命令：\n"
        "  add 1|标题|服饰>男装>夹克|品牌|39900   添加/更新本房间 catalog\n"
        "  item 1                                  当前 active item 切到 1\n"
        "  blank                                   当前无商品/不可确认商品\n"
        "  event switch|multi|price|promo|blank|occluded|blur|normal\n"
        "  note 任意备注\n"
        "  stop                                     提前结束\n"
    )
    print(help_text, flush=True)
    while True:
        try:
            line = input("collector> ").strip()
        except (EOFError, KeyboardInterrupt):
            q.put("stop")
            return
        if not line:
            continue
        q.put(line)
        if line == "stop":
            return

def marker_to_timeline(paths: RoomPaths, duration_ms: int):
    if not paths.markers.exists():
        return
    markers = []
    for line in paths.markers.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") in {"item", "blank"}:
            markers.append(r)
    markers.sort(key=lambda x: int(x["t_ms"]))
    # Collapse duplicate state markers at the same time / consecutive identical states.
    states = []
    last_key = object()
    for m in markers:
        key = ("blank", None) if m["kind"] == "blank" else ("item", m.get("item_id"))
        if key == last_key:
            continue
        states.append(m)
        last_key = key

    fields = ["start_ms", "end_ms", "item_id", "state"]
    with paths.timeline.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        if not states:
            return
        # Prefix before first explicit marker is unknown, not blank.
        first_t = max(0, min(duration_ms, int(states[0]["t_ms"])))
        if first_t > 0:
            w.writerow({"start_ms": 0, "end_ms": first_t, "item_id": "", "state": "unknown"})
        for i, m in enumerate(states):
            start = max(0, min(duration_ms, int(m["t_ms"])))
            end = duration_ms if i + 1 == len(states) else max(
                start, min(duration_ms, int(states[i + 1]["t_ms"]))
            )
            if end <= start:
                continue
            if m["kind"] == "blank":
                w.writerow({"start_ms": start, "end_ms": end, "item_id": "", "state": "blank"})
            else:
                w.writerow({
                    "start_ms": start, "end_ms": end,
                    "item_id": m.get("item_id", ""), "state": "item"
                })

def choose_page(context):
    pages = [p for p in context.pages if not p.is_closed()]
    return pages[-1] if pages else context.new_page()

def main():
    ap = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--root", default="data/self/raw_live")
    ap.add_argument("--room-id", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--platform", choices=sorted(SUPPORTED), default=None)
    ap.add_argument("--category", required=True)
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--viewer-count", type=int, default=None)
    ap.add_argument("--profile-root", default="profiles")
    ap.add_argument("--browser-channel", default="auto",
                    choices=["auto", "chrome", "chromium"])
    ap.add_argument("--display", default=os.environ.get("DISPLAY", ":0.0"))
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--x", type=int, default=0)
    ap.add_argument("--y", type=int, default=0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--audio-source", default=None)
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--screenshots-every", type=float, default=5.0,
                    help="0 disables screenshots")
    ap.add_argument("--save-dom-text", action="store_true",
                    help="保存可见 body 文本变化；可能包含弹幕/用户名，默认关闭")
    ap.add_argument("--crf", type=int, default=23)
    ap.add_argument("--preset", default="veryfast")
    ap.add_argument("--keep-mkv", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    if args.minutes <= 0:
        ap.error("--minutes must be > 0")

    inferred = detect_platform(args.url)
    platform = args.platform or inferred
    if not platform:
        ap.error("无法从 URL 判断平台，请显式传 --platform")
    if inferred and args.platform and inferred != args.platform:
        ap.error(f"URL 看起来属于 {inferred}，但 --platform={args.platform}")

    paths = RoomPaths(Path(args.root).resolve(), args.room_id)
    if paths.room.exists() and any(paths.room.iterdir()) and not args.overwrite:
        ap.error(f"{paths.room} 已存在且非空；若确定重采，请先备份后使用 --overwrite")
    paths.room.mkdir(parents=True, exist_ok=True)
    paths.screenshots.mkdir(parents=True, exist_ok=True)
    if not paths.catalog.exists():
        atomic_json(paths.catalog, [])

    room_meta = {
        "room_id": args.room_id,
        "platform": platform,
        "category": args.category,
        "url": args.url,
        "observed_viewers": args.viewer_count,
        "target_minutes": args.minutes,
        "capture_region": {
            "display": args.display, "x": args.x, "y": args.y, "size": args.size
        },
        "created_at": iso_now(),
        "status": "preparing",
    }
    atomic_json(paths.room_json, room_meta)

    # Import here so plan/audit scripts do not require Playwright.
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        raise SystemExit(
            "Playwright 未安装。先运行: pip install -r requirements.txt && playwright install chromium"
        ) from e

    profile_dir = (Path(args.profile_root).resolve() / platform)
    profile_dir.mkdir(parents=True, exist_ok=True)
    w, h = parse_size(args.size)

    with sync_playwright() as p:
        browser_args = [
            f"--window-size={w},{h}",
            f"--window-position={args.x},{args.y}",
            "--autoplay-policy=no-user-gesture-required",
        ]
        if os.environ.get("LIVE2SHOP_DISABLE_BROWSER_SANDBOX", "0") == "1":
            browser_args += ["--no-sandbox", "--disable-setuid-sandbox"]

        launch_common = dict(
            user_data_dir=str(profile_dir),
            headless=False,
            no_viewport=True,
            ignore_default_args=["--mute-audio"],
            args=browser_args,
        )
        context = None
        if args.browser_channel in {"auto", "chrome"}:
            try:
                context = p.chromium.launch_persistent_context(channel="chrome", **launch_common)
            except Exception:
                if args.browser_channel == "chrome":
                    raise
        if context is None:
            context = p.chromium.launch_persistent_context(**launch_common)

        page = choose_page(context)
        print(f"\n打开直播间: {args.url}")
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=120_000)
        except Exception as e:
            print(f"[WARN] page.goto 未完全成功: {e}")

        print(
            "\n请在浏览器里手工完成：登录/验证码（如有）→ 打开直播 → 确认声音正常 → "
            "把要录的画面放进采集区域。\n"
            "不要关闭浏览器。准备好后回到终端按 Enter 开始 15 分钟采集。"
        )
        input("READY> ")

        # Re-select last active page in case login opened another tab.
        page = choose_page(context)

        ffmpeg_cmd = build_ffmpeg_cmd(args, paths)
        print("[FFmpeg]", " ".join(ffmpeg_cmd))
        logf = paths.ffmpeg_log.open("wb")
        proc = subprocess.Popen(
            ffmpeg_cmd, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=logf,
        )

        room_meta["status"] = "recording"
        room_meta["capture_started_at"] = iso_now()
        atomic_json(paths.room_json, room_meta)

        cmd_q: queue.Queue[str] = queue.Queue()
        t = threading.Thread(target=input_worker, args=(cmd_q,), daemon=True)
        t.start()

        started = time.monotonic()
        stop_requested = False
        next_shot = 0.0
        dom_hash = None
        screenshot_index = 0

        try:
            while True:
                elapsed = time.monotonic() - started
                if proc.poll() is not None:
                    break
                if elapsed >= args.minutes * 60 + 5:
                    break

                while True:
                    try:
                        cmd = cmd_q.get_nowait()
                    except queue.Empty:
                        break

                    now_ms = int((time.monotonic() - started) * 1000)
                    if cmd == "stop":
                        stop_requested = True
                        break
                    if cmd == "blank":
                        append_jsonl(paths.markers, {
                            "t_ms": now_ms, "kind": "blank", "created_at": iso_now()
                        })
                        print(f"[MARK] {now_ms/1000:.1f}s blank")
                    elif cmd.startswith("item "):
                        item_id = coerce_item_id(cmd[5:].strip())
                        catalog_ids = {str(x.get("item_id")) for x in load_catalog(paths.catalog)}
                        if str(item_id) not in catalog_ids:
                            print(f"[WARN] item {item_id} 尚未在 catalog.json 中。先用 add ... 添加。")
                        append_jsonl(paths.markers, {
                            "t_ms": now_ms, "kind": "item",
                            "item_id": item_id, "created_at": iso_now()
                        })
                        print(f"[MARK] {now_ms/1000:.1f}s item={item_id}")
                    elif cmd.startswith("event "):
                        ev = cmd[6:].strip()
                        if ev not in EVENT_TYPES:
                            print("[WARN] event 可选:", ", ".join(sorted(EVENT_TYPES)))
                        else:
                            append_jsonl(paths.markers, {
                                "t_ms": now_ms, "kind": "event",
                                "event_type": ev, "created_at": iso_now()
                            })
                            print(f"[EVENT] {now_ms/1000:.1f}s {ev}")
                    elif cmd.startswith("note "):
                        append_jsonl(paths.markers, {
                            "t_ms": now_ms, "kind": "note",
                            "text": cmd[5:].strip(), "created_at": iso_now()
                        })
                    elif cmd.startswith("add "):
                        try:
                            item = parse_add(cmd[4:].strip())
                            upsert_catalog(paths.catalog, item)
                            append_jsonl(paths.markers, {
                                "t_ms": now_ms, "kind": "catalog_upsert",
                                "item_id": item["item_id"], "created_at": iso_now()
                            })
                            print(f"[CATALOG] upsert item={item['item_id']} title={item['title']}")
                        except Exception as e:
                            print("[WARN]", e)
                    elif cmd in {"help", "?"}:
                        print("命令: add ... | item ID | blank | event TYPE | note TEXT | stop")
                    else:
                        print("[WARN] 未知命令；输入 help 查看命令。")

                if stop_requested:
                    break

                if args.screenshots_every > 0 and elapsed >= next_shot:
                    screenshot_index += 1
                    shot = paths.screenshots / f"{int(elapsed*1000):010d}.jpg"
                    try:
                        page = choose_page(context)
                        page.screenshot(path=str(shot), type="jpeg", quality=70, timeout=5000)
                    except Exception as e:
                        print(f"[WARN] screenshot failed: {e}")
                    next_shot = elapsed + args.screenshots_every

                if args.save_dom_text and int(elapsed) % 5 == 0:
                    try:
                        page = choose_page(context)
                        text = page.locator("body").inner_text(timeout=2500)[:60000]
                        hsh = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
                        if hsh != dom_hash:
                            dom_hash = hsh
                            append_jsonl(paths.dom_changes, {
                                "t_ms": int(elapsed * 1000),
                                "title": page.title(),
                                "url": page.url,
                                "sha1": hsh,
                                "text": text,
                            })
                    except Exception:
                        pass

                time.sleep(0.2)
        except KeyboardInterrupt:
            stop_requested = True
        finally:
            if proc.poll() is None and proc.stdin:
                try:
                    proc.stdin.write(b"q\n")
                    proc.stdin.flush()
                except Exception:
                    proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            logf.close()

        duration_ms = int((time.monotonic() - started) * 1000)
        ok = remux_to_mp4(paths)
        if ok and not args.keep_mkv:
            try:
                paths.partial_mkv.unlink()
            except FileNotFoundError:
                pass

        marker_to_timeline(paths, duration_ms)

        room_meta["capture_finished_at"] = iso_now()
        room_meta["actual_duration_sec"] = round(duration_ms / 1000, 3)
        room_meta["status"] = "complete" if ok else "capture_failed"
        room_meta["video"] = str(paths.live_mp4) if ok else None
        room_meta["catalog_items"] = len(load_catalog(paths.catalog))
        room_meta["stopped_early"] = bool(stop_requested)
        atomic_json(paths.room_json, room_meta)

        print("\n=== 完成 ===")
        print("room:", paths.room)
        print("video:", paths.live_mp4 if ok else "(remux failed; see capture.mkv/ffmpeg.log)")
        print("catalog:", paths.catalog)
        print("timeline:", paths.timeline)
        print("markers:", paths.markers)
        print("status:", room_meta["status"])

        context.close()

if __name__ == "__main__":
    main()
