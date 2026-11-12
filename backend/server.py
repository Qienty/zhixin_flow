"""零依赖 HTTP 服务器：同时提供 /api 接口与前端静态文件。

启动：
    python backend/server.py            # http://127.0.0.1:8000
    python backend/server.py 8080       # 指定端口
"""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import socketserver
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT.parent / "frontend"
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.api import Request, Response, dispatch, parse_query  # noqa: E402

mimetypes.add_type("application/javascript; charset=utf-8", ".js")
mimetypes.add_type("text/css; charset=utf-8", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json; charset=utf-8", ".json")


class Handler(BaseHTTPRequestHandler):
    server_version = "Tether/1.0"
    protocol_version = "HTTP/1.1"

    # -- 入口 --------------------------------------------------------------
    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # -- 核心 --------------------------------------------------------------
    def _handle(self, method: str) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path.startswith("/api/"):
            self._handle_api(method, path, parsed.query)
        elif method == "GET":
            self._serve_static(path)
        else:
            self._send(405, b"method not allowed", "text/plain; charset=utf-8")

    def _handle_api(self, method: str, path: str, query: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        req = Request(method, path, parse_query(query), body, headers)
        result = dispatch(req)
        if result.raw is not None:
            data = result.raw.encode("utf-8") if isinstance(result.raw, str) else result.raw
        else:
            data = json.dumps(result.payload or {}, ensure_ascii=False).encode("utf-8")
        self._send(result.status, data, result.content_type, result.headers)

    def _serve_static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        # Windows 上 pathlib 也把反斜杠当分隔符，所以先统一成 /，
        # 再用 resolve() 确认结果没跑出 frontend/ 之外。
        safe = posixpath.normpath(path.replace("\\", "/")).lstrip("/")
        target = (FRONTEND / safe).resolve()
        try:
            target.relative_to(FRONTEND)
        except ValueError:
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return
        if not target.is_file():
            index = FRONTEND / "index.html"
            if index.is_file():
                target = index  # 单页应用回退
            else:
                self._send(404, b"not found", "text/plain; charset=utf-8")
                return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        try:
            data = target.read_bytes()
        except OSError:
            self._send(500, b"read error", "text/plain; charset=utf-8")
            return
        self._send(200, data, ctype, {"Cache-Control": "no-cache"})

    # -- 输出 --------------------------------------------------------------
    def _cors(self) -> None:
        """只回本机来源。

        原来回的是 `*`，于是任何网页里的 JS 都能读写 127.0.0.1:8000 上的情绪
        记录——只绑本地回环，不等于别人访问不到。
        """
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).hostname in ("127.0.0.1", "localhost", "::1"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, status: int, data: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if data:
            self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # 精简日志
        sys.stderr.write("%s  %s\n" % (self.log_date_time_string(), fmt % args))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8000"))
    db.init_db()
    httpd = Server(("127.0.0.1", port), Handler)
    print(f"\n  Tether · 情绪记录与自我调节")
    print(f"  http://127.0.0.1:{port}")
    print(f"  API  http://127.0.0.1:{port}/api/meta")
    print("  Ctrl+C 停止\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
