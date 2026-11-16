"""零依赖 HTTP 服务器：同时提供 /api 接口与前端静态文件。

启动：
    python backend/server.py            # http://127.0.0.1:8000
    python backend/server.py 8080       # 指定端口
"""

from __future__ import annotations

import json
import hashlib
import mimetypes
import os
import posixpath
import socketserver
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT.parent / "frontend"
sys.path.insert(0, str(ROOT))

from app import ai, db, __version__  # noqa: E402
from app.api import Request, Response, dispatch, parse_query  # noqa: E402

mimetypes.add_type("application/javascript; charset=utf-8", ".js")
mimetypes.add_type("text/css; charset=utf-8", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json; charset=utf-8", ".json")


class Handler(BaseHTTPRequestHandler):
    server_version = f"Tether/{__version__}"
    protocol_version = "HTTP/1.1"

    # -- 入口 --------------------------------------------------------------
    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def do_OPTIONS(self) -> None:
        self._dev_page = False
        self._send(204, b"", "text/plain; charset=utf-8")

    # -- 核心 --------------------------------------------------------------
    def _handle(self, method: str) -> None:
        self._dev_page = False
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        if path == "/api" or path.startswith("/api/"):
            self._handle_api(method, path, parsed.query)
        elif method == "GET":
            self._serve_static(path)
        else:
            self._send(405, b"method not allowed", "text/plain; charset=utf-8")

    def _handle_api(self, method: str, path: str, query: str) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > 8 * 1024 * 1024:
                raise ValueError()
        except ValueError:
            self.close_connection = True
            self._send(413, b'{"error":"body must be at most 8 MiB"}', "application/json")
            return
        body = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        req = Request(method, path, parse_query(query), body, headers)
        req.client_ip = self.client_address[0]
        # Trust only a proxy explicitly configured by the operator.
        trusted = {ip.strip() for ip in os.environ.get("TETHER_TRUSTED_PROXIES", "").split(",") if ip.strip()}
        if req.client_ip in trusted:
            req.client_ip = self.headers.get("X-Real-IP") or req.client_ip
        else:
            req.headers.pop("x-forwarded-proto", None)
        result = dispatch(req)
        result.headers["Cache-Control"] = "no-store"
        if "Set-Cookie" in result.headers and req.headers.get("x-forwarded-proto", "").lower() == "https":
            result.headers["Set-Cookie"] += "; Secure"
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
            self._send(404, "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>404 · Tether</title><h1>页面不存在 / Page not found</h1><p>请检查地址，或使用浏览器返回上一页。</p></html>".encode("utf-8"), "text/html; charset=utf-8")
            return
        self._dev_page = target.name.startswith("__") and os.environ.get("TETHER_DEV_PAGES") == "1"
        if target.name.startswith("__") and not self._dev_page:
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        try:
            data = target.read_bytes()
        except OSError:
            self._send(500, b"read error", "text/plain; charset=utf-8")
            return
        if target.name == "sw.js":
            digest = hashlib.sha256()
            for asset in sorted(FRONTEND.rglob("*")):
                if asset.is_file() and not asset.name.startswith("__"):
                    digest.update(asset.relative_to(FRONTEND).as_posix().encode())
                    digest.update(asset.read_bytes())
            data = data.replace(b"__ASSET_HASH__", digest.hexdigest()[:16].encode())
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
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    # 浏览器侧的基础防护。应用没有任何第三方脚本 / 字体 / 接口，所以 CSP 只放行同源；
    # style 要 'unsafe-inline'，是因为渲染时大量写了 style="--emo:…" 这类行内变量。
    SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "microphone=(self), camera=(), geolocation=()",
    }
    _dev_page = False  # 以 __ 开头的开发测试页带内联脚本，单独放宽 script-src

    def _csp(self) -> str:
        scripts = "'self' 'unsafe-inline'" if self._dev_page else "'self'"
        return (
            f"default-src 'self'; script-src {scripts}; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'self'"
        )

    def _send(self, status: int, data: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        # 文本类型没写 charset 的补上 utf-8：只靠 <meta charset> 兜底，
        # 非浏览器客户端会按 latin-1 解出乱码
        if ctype.startswith("text/") and "charset" not in ctype:
            ctype += "; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        for key, value in self.SECURITY_HEADERS.items():
            self.send_header(key, value)
        self.send_header("Content-Security-Policy", self._csp())
        # HSTS 只在原始请求是 https 时才发（前面的 Nginx 会带 X-Forwarded-Proto）：
        # 明文 HTTP 上发这个头没有意义
        trusted = {ip.strip() for ip in os.environ.get("TETHER_TRUSTED_PROXIES", "").split(",") if ip.strip()}
        if self.client_address[0] in trusted and (self.headers.get("X-Forwarded-Proto") or "").strip().lower() == "https":
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if data and self.command != "HEAD":
            self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # 精简日志
        sys.stderr.write("%s  %s\n" % (self.log_date_time_string(), fmt % args))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    _next_cache_cleanup = 0.0

    def service_actions(self) -> None:
        if time.monotonic() >= self._next_cache_cleanup:
            self._next_cache_cleanup = time.monotonic() + 3600
            try:
                ai.cache_prune()
            except Exception as exc:
                sys.stderr.write(f"AI cache cleanup failed: {type(exc).__name__}\n")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8000"))
    # 默认只听回环：本地跑或面板挂 Nginx 反代时都够用，也不会把记录暴露到公网。
    # 要在容器里直接对外（前面没有反代），把 HOST 设成 0.0.0.0。
    host = (os.environ.get("HOST") or "127.0.0.1").strip()
    db.init_db()
    httpd = Server((host, port), Handler)
    shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    print(f"\n  Tether · 情绪记录与自我调节")
    print(f"  监听 {host}:{port}")
    print(f"  http://{shown}:{port}")
    print(f"  API  http://{shown}:{port}/api/meta")
    print("  Ctrl+C 停止\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
