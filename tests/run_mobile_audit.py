"""Run the mobile audit against an isolated server (temporary database, AI disabled).

    python -B tests/run_mobile_audit.py

Requires playwright-core (see docs/mobile.md). The project itself keeps no
third-party runtime dependency; this is a test-only tool, same as run_browser.py.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="tether-mobile-") as folder:
        os.environ["TETHER_DATA_DIR"] = folder
        os.environ["TETHER_AI_API_KEY"] = ""
        # 每个设备注册一个新账号：把注册限流调高，避免连跑两次就被 429 拦住。
        os.environ["TETHER_REGISTER_LIMIT"] = "100"
        sys.path.insert(0, str(ROOT / "backend"))
        import server
        from app import ai, db

        # A local .env must never activate a real provider during the audit.
        ai._env_loaded = True

        def no_model(*args, **kwargs):
            raise AssertionError("Mobile audit must not call a real AI provider")

        ai.analyze = no_model
        db.init_db()

        class QuietHandler(server.Handler):
            def log_message(self, *args):
                pass

        httpd = server.Server(("127.0.0.1", 0), QuietHandler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        env = dict(os.environ, TETHER_TEST_URL=f"http://127.0.0.1:{httpd.server_port}")
        try:
            subprocess.run(["node", str(ROOT / "tests" / "mobile_audit.mjs")], env=env, cwd=ROOT, check=True)
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(5)


if __name__ == "__main__":
    main()
