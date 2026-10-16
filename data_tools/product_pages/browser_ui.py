"""Loopback-only manual browser control; screenshots live in memory only.

Use SSH/IDE port forwarding to reach this interface on a headless server.
The token is kept in the URL fragment (not HTTP access logs). No credentials,
request bodies, or browser screenshots are persisted by this service.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


class BrowserUI:
    def __init__(self, port: int):
        self.port = port
        self.token = secrets.token_urlsafe(32)
        self.page = None
        self.message = "正在打开商品页"
        self.waiting = False
        self.resume = asyncio.Event()
        self.lock = asyncio.Lock()
        self.server = None
        self.thread = None

    async def start(self):
        loop = asyncio.get_running_loop()
        ui = self
        html = Path(__file__).with_name("browser_ui.html").read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, status, body, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; frame-ancestors 'none'; connect-src 'self'")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def authorized(self):
                # Host validation also prevents DNS-rebinding access.
                host = urlparse("http://" + self.headers.get("Host", "")).hostname
                origin = self.headers.get("Origin")
                expected_origin = "http://" + self.headers.get("Host", "")
                return (
                    host in {"localhost", "127.0.0.1"}
                    and (not origin or origin == expected_origin)
                    and hmac.compare_digest(
                        self.headers.get("Authorization", ""), "Bearer " + ui.token
                    )
                )

            def dispatch(self, action=None):
                future = asyncio.run_coroutine_threadsafe(ui.handle(self.path, action), loop)
                try:
                    result, mime = future.result(timeout=20)
                    self.reply(200, result, mime)
                except Exception:
                    future.cancel()
                    # Never log exceptions that might contain submitted credentials.
                    self.reply(503, b'{"error":"browser_busy_or_invalid_action"}')

            def do_GET(self):
                if self.path == "/":
                    self.reply(200, html, "text/html; charset=utf-8")
                elif self.path in {"/state", "/frame"} and self.authorized():
                    self.dispatch()
                else:
                    self.reply(403, b'{"error":"unauthorized"}')

            def do_POST(self):
                if self.path != "/action" or not self.authorized():
                    self.reply(403, b'{"error":"unauthorized"}')
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 16384:
                        raise ValueError("invalid size")
                    action = json.loads(self.rfile.read(length))
                    if not isinstance(action, dict):
                        raise ValueError("invalid action")
                except (ValueError, UnicodeError):
                    self.reply(400, b'{"error":"invalid_request"}')
                    return
                self.dispatch(action)

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        print(f"人工浏览器界面：http://127.0.0.1:{self.port}/#{self.token}", flush=True)
        print(f"远程服务器请转发 TCP {self.port} 到本机同一端口，再打开上面的地址。", flush=True)

    def attach(self, page):
        self.page = page

    async def wait(self, message: str, timeout: float):
        self.message = message
        self.resume.clear()
        self.waiting = True
        print(message, flush=True)
        try:
            await asyncio.wait_for(self.resume.wait(), timeout)
        finally:
            self.waiting = False

    async def handle(self, path, action):
        if path == "/state":
            parsed = urlparse(self.page.url if self.page else "")
            return json.dumps({
                "message": self.message, "waiting": self.waiting,
                "page": f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
            }, ensure_ascii=False).encode(), "application/json"
        async with self.lock:
            if not self.page or self.page.is_closed():
                raise ValueError("page unavailable")
            if path == "/frame":
                return await self.page.screenshot(type="jpeg", quality=80), "image/jpeg"
            kind = action.get("type")
            if kind == "continue" and self.waiting:
                self.resume.set()
            elif kind == "click":
                size = self.page.viewport_size or {"width": 1280, "height": 900}
                x, y = float(action["x"]), float(action["y"])
                if not (0 <= x < size["width"] and 0 <= y < size["height"]):
                    raise ValueError("invalid coordinates")
                await self.page.mouse.click(x, y)
            elif kind == "text":
                await self.page.keyboard.insert_text(str(action.get("text", ""))[:4096])
            elif kind == "key" and action.get("key") in {
                "Tab", "Enter", "Backspace", "Escape", "Control+A", "ArrowDown", "ArrowUp"
            }:
                await self.page.keyboard.press(action["key"])
            elif kind == "wheel":
                await self.page.mouse.wheel(0, max(-900, min(900, float(action["delta"]))))
            else:
                raise ValueError("invalid action")
            return b'{"ok":true}', "application/json"

    async def close(self):
        self.page = None
        if self.server:
            await asyncio.to_thread(self.server.shutdown)
            self.server.server_close()
            self.thread.join(timeout=2)
