"""A tiny local web server for the HUD: live events over Server-Sent Events, commands over POST.

Bound to 127.0.0.1 only. A random per-run token plus a Host-header check stop other websites
from sending commands to Jarvis.
"""
from __future__ import annotations

import json
import logging
import queue
import secrets
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from ..events import bus

if TYPE_CHECKING:
    from ..assistant import Jarvis

log = logging.getLogger(__name__)
INDEX = Path(__file__).with_name("index.html")


class HUDServer:
    def __init__(self, jarvis: "Jarvis", port: int = 7777):
        self.jarvis = jarvis
        self.port = port
        self.token = secrets.token_urlsafe(18)
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.httpd.daemon_threads = True
        bus.subscribe(self._on_event)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        threading.Thread(target=self.httpd.serve_forever, name="hud", daemon=True).start()

    def stop(self) -> None:
        bus.unsubscribe(self._on_event)
        self.httpd.shutdown()

    def open(self, app_window: bool = True) -> None:
        if app_window:
            try:
                subprocess.Popen(f'start "" msedge --app={self.url} --window-size=480,820', shell=True)
                return
            except OSError:
                pass
        webbrowser.open(self.url)

    def _on_event(self, event: dict) -> None:
        with self._lock:
            for q in list(self._clients):
                try:
                    q.put_nowait(event)
                except queue.Full:
                    pass

    def _handler(self):
        server = self
        allowed_hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # keep the console quiet
                log.debug("hud: " + fmt, *args)

            def _host_ok(self) -> bool:
                if self.headers.get("Host", "") not in allowed_hosts:
                    self.send_error(403)
                    return False
                return True

            def _json(self, data, code: int = 200) -> None:
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._host_ok():
                    return
                url = urlparse(self.path)
                if url.path == "/":
                    page = INDEX.read_text(encoding="utf-8").replace("__TOKEN__", server.token)
                    body = page.encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                elif url.path == "/events":
                    if parse_qs(url.query).get("token", [""])[0] != server.token:
                        self.send_error(403)
                        return
                    self._stream()
                elif url.path == "/api/status":
                    self._json(server.jarvis.status())
                else:
                    self.send_error(404)

            def _stream(self):
                q: queue.Queue = queue.Queue(maxsize=500)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    history = [e for e in bus.history if e["type"] in ("user", "jarvis", "tool", "notice")][-30:]
                    self.wfile.write(f"data: {json.dumps({'type': 'history', 'events': history})}\n\n".encode())
                    self.wfile.write(f"data: {json.dumps({'type': 'state', 'value': server.jarvis.state})}\n\n".encode())
                    self.wfile.flush()
                    with server._lock:
                        server._clients.append(q)
                    while not server.jarvis.stopping.is_set():
                        try:
                            event = q.get(timeout=15)
                            self.wfile.write(f"data: {json.dumps(event, default=str)}\n\n".encode())
                        except queue.Empty:
                            self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                finally:
                    with server._lock:
                        if q in server._clients:
                            server._clients.remove(q)

            def do_POST(self):
                if not self._host_ok():
                    return
                if self.headers.get("X-Jarvis-Token") != server.token:
                    self.send_error(403)
                    return
                length = min(int(self.headers.get("Content-Length", 0) or 0), 20000)
                try:
                    data = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    data = {}
                path = urlparse(self.path).path
                if path == "/api/command":
                    server.jarvis.submit(str(data.get("text", ""))[:4000], "hud")
                elif path == "/api/listen":
                    server.jarvis.push_to_talk()
                elif path == "/api/stop":
                    server.jarvis.submit("stop", "hud")
                else:
                    self.send_error(404)
                    return
                self._json({"ok": True})

        return Handler
