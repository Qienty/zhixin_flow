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
import socket
import threading
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from email.utils import formatdate, parsedate_to_datetime

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT.parent / "frontend"
sys.path.insert(0, str(ROOT))

from app import ai, db, __version__  # noqa: E402
from app.api import Request, Response, dispatch, parse_query  # noqa: E402
from app.assets import StaticAssets, accepts_gzip  # noqa: E402
from app.limits import setting  # noqa: E402

MAX_BODY = 8 * 1024 * 1024

# 本地调试与壳内 WebView 的默认来源。真正的跨源部署必须显式配置。
LOCAL_ORIGIN_HOSTS = ("127.0.0.1", "localhost", "::1")


def allowed_origins() -> list[str]:
    """TETHER_ALLOWED_ORIGINS：逗号分隔的完整来源，逐字匹配（不做通配）。"""
    raw = os.environ.get("TETHER_ALLOWED_ORIGINS") or ""
    return [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]


def _origin_allowed(origin: str) -> bool:
    normalized = (origin or "").strip().rstrip("/")
    if not normalized:
        return False
    if normalized in allowed_origins():
        return True
    try:
        parts = urllib.parse.urlsplit(normalized)
    except ValueError:
        return False
    # 只有 http(s) 的本机来源才算"本地调试"。capacitor://localhost 之类必须显式配置，
    # 否则任何自定义 scheme 只要 host 叫 localhost 就能读到数据。
    if (parts.scheme or "").lower() not in ("http", "https"):
        return False
    return parts.hostname in LOCAL_ORIGIN_HOSTS

mimetypes.add_type("application/javascript; charset=utf-8", ".js")
mimetypes.add_type("text/css; charset=utf-8", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("application/json; charset=utf-8", ".json")


class Handler(BaseHTTPRequestHandler):
    server_version = f"ZhiXinYuanLiu/{__version__}"
    protocol_version = "HTTP/1.1"
    timeout = 65  # 仅作默认占位；setup() 会按 TETHER_HTTP_TIMEOUT 重新设置

    def setup(self):
        # 65 秒：必须长于客户端（浏览器 / Node）复用空闲连接的窗口。
        # 原来 30 秒会在客户端正要复用连接时把它踢掉，发出中的请求直接丢失。
        self.timeout = setting("TETHER_HTTP_TIMEOUT", 65, high=300)
        super().setup()

    def finish(self):
        """关连接前先把已经到达的字节读掉。

        接收缓冲里还有未读数据时 close() 会发 RST，客户端看到的是
        ConnectionAborted/WinError 10053；读干净之后再关才是正常 FIN。
        """
        if self.close_connection:
            try:
                self.connection.settimeout(0.2)
                for _ in range(4):
                    if not self.rfile.read1(65536):
                        break
            except (OSError, ValueError):
                pass
        super().finish()

    def _content_length(self):
        values = self.headers.get_all("Content-Length", [])
        if len(values) > 1 or self.headers.get("Transfer-Encoding"):
            raise ValueError("unsupported request framing")
        value = values[0].strip() if values else "0"
        if not value.isascii() or not value.isdigit():
            raise ValueError("invalid Content-Length")
        return int(value)

    def _reject_body(self, status, message, remaining=0):
        self.close_connection = True
        self._send(status, json.dumps({"error": message}).encode(), "application/json", {"Connection": "close", "Cache-Control": "no-store"})
        # A bounded drain lets eager uploaders read the error instead of a TCP reset.
        # Never wait for an unbounded or indefinitely slow rejected request body.
        deadline = time.monotonic() + 1
        remaining = min(remaining, MAX_BODY + 65536)
        try:
            while remaining and time.monotonic() < deadline:
                self.connection.settimeout(max(0.001, deadline - time.monotonic()))
                chunk = self.rfile.read1(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass

    def handle_expect_100(self):
        self._dev_page = False
        try:
            length = self._content_length()
        except ValueError:
            self._reject_body(400, "invalid request framing")
            return False
        if length > MAX_BODY:
            self._reject_body(413, "body must be at most 8 MiB")
            return False
        return super().handle_expect_100()

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
            length = self._content_length()
        except ValueError:
            self._reject_body(400, "invalid request framing")
            return
        if length > MAX_BODY:
            self._reject_body(413, "body must be at most 8 MiB", length)
            return
        try:
            body = self.rfile.read(length) if length else b""
        except TimeoutError:
            self._reject_body(408, "request body timed out")
            return
        if len(body) != length:
            self._reject_body(400, "incomplete request body")
            return
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
            self._send(404, "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>404 · 智心元流</title><h1>页面不存在 / Page not found</h1><p>请检查地址，或使用浏览器返回上一页。</p></html>".encode("utf-8"), "text/html; charset=utf-8")
            return
        self._dev_page = target.name.startswith("__") and os.environ.get("TETHER_DEV_PAGES") == "1"
        if target.name.startswith("__") and not self._dev_page:
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        try:
            data, etag, modified, encoded = self.server.assets.representation(target, accepts_gzip(self.headers.get("Accept-Encoding", "")))
        except OSError:
            self._send(500, b"read error", "text/plain; charset=utf-8")
            return
        headers = {"Cache-Control": "no-cache", "ETag": etag, "Last-Modified": formatdate(modified, usegmt=True), "Vary": "Accept-Encoding"}
        if encoded:
            headers["Content-Encoding"] = "gzip"
        validator = self.headers.get("If-None-Match")
        unchanged = validator is not None and any(value.strip().removeprefix("W/") in (etag, "*") for value in validator.split(","))
        # Deleting an asset can change the worker version without increasing
        # the maximum asset mtime. Only its content ETag is a safe validator.
        if validator is None and target.name != "sw.js" and self.headers.get("If-Modified-Since"):
            try:
                unchanged = int(modified) <= parsedate_to_datetime(self.headers["If-Modified-Since"]).timestamp()
            except (ValueError, TypeError, OverflowError):
                pass
        self._send(304 if unchanged else 200, b"" if unchanged else data, ctype, headers)

    # -- 输出 --------------------------------------------------------------
    def _cors(self) -> None:
        """跨源白名单。默认行为不变：只回本机来源。

        同源部署（浏览器直接访问站点、或 Nginx 反代同域）根本用不到 CORS，
        所以不设 TETHER_ALLOWED_ORIGINS 时，行为与以前完全一致。
        需要跨源的是原生壳（capacitor://、自定义 scheme）或单独的调试前端，
        这些来源必须用 `TETHER_ALLOWED_ORIGINS` 显式列出，逐字匹配，不支持通配。

        原来回的是 `*`，于是任何网页里的 JS 都能读写 127.0.0.1:8000 上的情绪
        记录——只绑本地回环，不等于别人访问不到。
        """
        origin = self.headers.get("Origin")
        if origin and _origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, PUT, DELETE, OPTIONS")
            # 壳内用 Bearer token：要允许 Authorization 和握手头，否则预检直接失败。
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Tether-Token-Auth")
            self.send_header("Access-Control-Max-Age", "600")

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
            "form-action 'self'; frame-ancestors 'self'; object-src 'none'"
        )

    def _send(self, status: int, data: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        # 文本类型没写 charset 的补上 utf-8：只靠 <meta charset> 兜底，
        # 非浏览器客户端会按 latin-1 解出乱码
        if ctype.startswith("text/") and "charset" not in ctype:
            ctype += "; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        if status != 304:
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

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(setting("TETHER_HTTP_MAX_CONNECTIONS", 64, high=512))
        self.assets = StaticAssets(FRONTEND)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(0.2)
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\nRetry-After: 1\r\n\r\n")
                request.shutdown(socket.SHUT_WR)
                # Drain a small already-arriving request so closing the socket
                # does not reset the 503 response. Keep the accept loop bounded.
                deadline, remaining = time.monotonic() + 0.02, 65536
                while remaining and time.monotonic() < deadline:
                    request.settimeout(max(0.001, deadline - time.monotonic()))
                    chunk = request.recv(min(8192, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

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
    print(f"\n  智心元流 · 情绪记录与自我调节")
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
