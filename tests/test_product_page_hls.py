import subprocess
import sys
import tempfile
import unittest
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data_tools"))
from collect_product_pages import _download_hls


class HlsDownloadTests(unittest.TestCase):
    def test_hls_remux_works_without_system_ffmpeg_and_decodes(self):
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory(prefix="p2-hls-test-") as directory:
            root = Path(directory)
            manifest = root / "source.m3u8"
            output = root / "video.mp4"
            subprocess.run([
                ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "testsrc2=size=64x64:rate=5", "-t", "2",
                "-an", "-c:v", "libx264", "-preset", "ultrafast",
                "-f", "hls", "-hls_time", "1", "-hls_list_size", "0", str(manifest),
            ], check=True, capture_output=True, timeout=30)
            class Handler(SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=directory, **kwargs)

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with patch("collect_product_pages.shutil.which", return_value=None):
                    _download_hls(f"http://127.0.0.1:{server.server_port}/source.m3u8", output, "https://item.taobao.com/", [])
            finally:
                server.shutdown()
                server.server_close()
                worker.join()
            result = subprocess.run([
                ffmpeg, "-hide_banner", "-i", str(output), "-f", "null", "-"
            ], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Duration: 00:00:02", result.stderr)
            self.assertGreater(output.stat().st_size, 1000)
