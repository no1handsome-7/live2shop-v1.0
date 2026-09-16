from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psycopg
from confluent_kafka import Producer
from minio import Minio
from redis.asyncio import Redis

from live2shop.v1 import common_pb2, media_pb2
from .proto_util import event_meta, uuid7_str
from .settings import settings

log = logging.getLogger("media-segmenter")

TOPIC = "live2shop.media.segment.v1"

@dataclass
class StreamWorker:
    room_id: int
    stream_name: str
    stream_session_id: str
    started_wallclock_ms: int
    producer: Producer
    minio: Minio
    redis: Redis

    process: Optional[asyncio.subprocess.Process] = None
    scan_task: Optional[asyncio.Task] = None
    next_seq: int = 0
    cumulative_pts_ms: int = 0
    stopped: bool = False

    @property
    def work_dir(self) -> Path:
        return Path(settings.work_dir) / self.stream_session_id

    async def start(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._db_start)

        rtmp = f"{settings.srs_rtmp_base}/{self.stream_name}"
        pattern = str(self.work_dir / "seg_%06d.mkv")

        cmd = [
            "ffmpeg",
            "-hide_banner", "-loglevel", "warning",
            "-i", rtmp,

            # Normalize the video so canonical segment boundaries do not
            # depend on the publisher's GOP/keyframe cadence.
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-sc_threshold", "0",
            "-force_key_frames",
            f"expr:gte(t,n_forced*{settings.segment_seconds})",

            # Keep the input audio bitstream when available. Video keyframes,
            # rather than audio packets, determine segment boundaries.
            "-c:a", "copy",

            "-f", "segment",
            "-segment_time", str(settings.segment_seconds),
            "-segment_time_delta", "0.05",
            "-reset_timestamps", "1",
            "-segment_format", "matroska",
            pattern,
        ]
        log.info("starting ffmpeg room=%s session=%s", self.room_id, self.stream_session_id)
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self.scan_task = asyncio.create_task(self._scan_loop())
        asyncio.create_task(self._log_stderr())

    async def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True

        try:
            if self.process and self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()

            # Give ffmpeg time to close the final segment.
            await asyncio.sleep(0.5)

            # The final tail segment may be shorter than the configured
            # segment size, or occasionally lack container-level duration
            # metadata. Publishing it is best-effort; stream lifecycle
            # cleanup must never depend on it succeeding.
            try:
                await self._publish_ready(include_latest=True)
            except Exception:
                log.exception(
                    "failed to publish final segment room=%s session=%s; "
                    "continuing shutdown",
                    self.room_id,
                    self.stream_session_id,
                )

        finally:
            if self.scan_task:
                self.scan_task.cancel()
                try:
                    await self.scan_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    log.exception(
                        "segment scan task failed during shutdown room=%s",
                        self.room_id,
                    )

            try:
                await asyncio.to_thread(self._db_stop)
            except Exception:
                log.exception(
                    "failed to mark stream ended room=%s session=%s",
                    self.room_id,
                    self.stream_session_id,
                )

            try:
                await self.redis.delete(f"live:timeline:{self.room_id}")
            except Exception:
                log.exception(
                    "failed to delete live timeline room=%s",
                    self.room_id,
                )

            shutil.rmtree(self.work_dir, ignore_errors=True)

            log.info(
                "stopped stream room=%s session=%s",
                self.room_id,
                self.stream_session_id,
            )

    async def _scan_loop(self) -> None:
        try:
            while not self.stopped:
                await self._publish_ready(include_latest=False)
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("segment scan loop failed")

    async def _publish_ready(self, include_latest: bool) -> None:
        files = sorted(self.work_dir.glob("seg_*.mkv"))
        if not files:
            return

        # The newest file may still be open. Publish it only at stream shutdown.
        ready = files if include_latest else files[:-1]
        by_seq = {int(p.stem.split("_")[-1]): p for p in ready}

        while self.next_seq in by_seq:
            path = by_seq[self.next_seq]
            await self._publish_one(path, self.next_seq)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            self.next_seq += 1

    async def _publish_one(self, path: Path, seq: int) -> None:
        duration_ms = await asyncio.to_thread(probe_duration_ms, path)
        if duration_ms <= 0:
            duration_ms = int(settings.segment_seconds * 1000)

        start_pts = self.cumulative_pts_ms
        end_pts = start_pts + duration_ms
        self.cumulative_pts_ms = end_pts

        segment_id = uuid7_str()
        object_key = (
            f"rooms/{self.room_id}/streams/{self.stream_session_id}/"
            f"segments/{seq:06d}.mkv"
        )

        await asyncio.to_thread(
            self.minio.fput_object,
            settings.media_bucket,
            object_key,
            str(path),
            "video/x-matroska",
        )

        frame_pts = evenly_spaced_pts(start_pts, end_pts, 8)
        event_time_ms = self.started_wallclock_ms + start_pts

        event = media_pb2.MediaSegmentEvent(
            meta=event_meta(common_pb2, "media-segmenter", event_time_ms),
            segment_id=segment_id,
            room_id=self.room_id,
            stream_session_id=self.stream_session_id,
            sequence_no=seq,
            start_pts_ms=start_pts,
            end_pts_ms=end_pts,
            object_uri=f"s3://{settings.media_bucket}/{object_key}",
            sampled_frame_pts_ms=frame_pts,
            duration_ms=duration_ms,
            sha256_hex=await asyncio.to_thread(sha256_file, path),
        )

        self.producer.produce(
            TOPIC,
            key=f"{self.room_id}:{self.stream_session_id}".encode(),
            value=event.SerializeToString(),
        )
        self.producer.poll(0)
        self.producer.flush(5)

        timeline = {
            "room_id": self.room_id,
            "stream_session_id": self.stream_session_id,
            "latest_end_pts_ms": end_pts,
            "latest_segment_seq": seq,
            "live_edge_updated_at_ms": int(time.time() * 1000),
            "timeline_source": "media-segmenter",
        }
        await self.redis.set(
            f"live:timeline:{self.room_id}",
            json.dumps(timeline, ensure_ascii=False),
            ex=30,
        )

        await asyncio.to_thread(
            self._db_segment,
            segment_id, seq, start_pts, end_pts,
            f"s3://{settings.media_bucket}/{object_key}",
            event.sha256_hex,
        )
        log.info(
            "published segment room=%s seq=%s pts=[%s,%s)",
            self.room_id, seq, start_pts, end_pts,
        )

    async def _log_stderr(self) -> None:
        if not self.process or not self.process.stderr:
            return
        while True:
            line = await self.process.stderr.readline()
            if not line:
                return
            log.warning("ffmpeg[%s]: %s", self.room_id, line.decode(errors="replace").rstrip())

    def _db_start(self) -> None:
        with psycopg.connect(settings.postgres_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO live_stream_sessions(
                        stream_session_id, room_id, ingest_protocol,
                        status, started_at
                    )
                    VALUES (%s::uuid, %s, 'RTMP', 'LIVE', to_timestamp(%s/1000.0))
                    ON CONFLICT (stream_session_id) DO NOTHING
                    """,
                    (self.stream_session_id, self.room_id, self.started_wallclock_ms),
                )
                cur.execute(
                    "UPDATE live_rooms SET status='LIVE', updated_at=now() WHERE room_id=%s",
                    (self.room_id,),
                )
            conn.commit()

    def _db_stop(self) -> None:
        with psycopg.connect(settings.postgres_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE live_stream_sessions
                       SET status='ENDED', ended_at=now()
                     WHERE stream_session_id=%s::uuid
                    """,
                    (self.stream_session_id,),
                )
                cur.execute(
                    "UPDATE live_rooms SET status='OFFLINE', updated_at=now() WHERE room_id=%s",
                    (self.room_id,),
                )
            conn.commit()

    def _db_segment(
        self, segment_id: str, seq: int, start_pts: int, end_pts: int,
        object_uri: str, sha256_hex: str
    ) -> None:
        with psycopg.connect(settings.postgres_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO live_segments(
                        segment_id, stream_session_id, room_id, seq_no,
                        start_pts_ms, end_pts_ms, object_uri, sha256_hex
                    )
                    VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (stream_session_id, seq_no) DO NOTHING
                    """,
                    (
                        segment_id, self.stream_session_id, self.room_id, seq,
                        start_pts, end_pts, object_uri, sha256_hex,
                    ),
                )
            conn.commit()

def probe_duration_ms(path: Path) -> int:
    # Fast path: finalized containers normally expose format.duration.
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]

    try:
        out = subprocess.check_output(
            cmd,
            text=True,
            stderr=subprocess.STDOUT,
        ).strip()

        if out and out.upper() != "N/A":
            duration_s = float(out)
            if duration_s > 0:
                return max(1, int(round(duration_s * 1000)))
    except (subprocess.CalledProcessError, ValueError):
        pass

    # Matroska tail segments created during stream shutdown may not contain
    # container-level duration metadata. Recover duration from packet PTS.
    packet_cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "packet=pts_time,dts_time,duration_time",
        "-of", "json",
        str(path),
    ]

    try:
        raw = subprocess.check_output(
            packet_cmd,
            text=True,
            stderr=subprocess.STDOUT,
        )
        data = json.loads(raw)

        starts = []
        ends = []

        for packet in data.get("packets", []):
            ts_raw = packet.get("pts_time")
            if ts_raw in (None, "N/A"):
                ts_raw = packet.get("dts_time")

            if ts_raw in (None, "N/A"):
                continue

            try:
                ts = float(ts_raw)
            except (TypeError, ValueError):
                continue

            dur = 0.0
            dur_raw = packet.get("duration_time")
            if dur_raw not in (None, "N/A"):
                try:
                    dur = max(0.0, float(dur_raw))
                except (TypeError, ValueError):
                    pass

            starts.append(ts)
            ends.append(ts + dur)

        if starts and ends:
            duration_s = max(ends) - min(starts)
            if duration_s > 0:
                return max(1, int(round(duration_s * 1000)))

    except (
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        ValueError,
    ):
        pass

    raise ValueError(
        f"unable to determine media duration for {path}"
    )

def sha256_file(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def evenly_spaced_pts(start: int, end: int, n: int) -> list[int]:
    if n <= 1:
        return [(start + end) // 2]
    width = max(1, end - start)
    return [start + int((i + 0.5) * width / n) for i in range(n)]
