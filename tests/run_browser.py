"""Run Playwright checks against an isolated temporary database, with AI disabled."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix='tether-browser-') as folder:
        os.environ['TETHER_DATA_DIR'] = folder
        os.environ['TETHER_AI_API_KEY'] = ''
        sys.path.insert(0, str(ROOT / 'backend'))
        import server
        from app import ai, db
        # A local .env must never activate a real provider during browser tests.
        ai._env_loaded = True
        def no_model(*args, **kwargs):
            raise AssertionError('Browser tests must not call a real AI provider')
        ai.analyze = no_model
        db.init_db()
        class QuietHandler(server.Handler):
            def log_message(self, *args):
                pass
        httpd = server.Server(('127.0.0.1', 0), QuietHandler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        env = dict(os.environ, TETHER_TEST_URL=f'http://127.0.0.1:{httpd.server_port}')
        try:
            scripts = sys.argv[1:] or ['expression_browser.cjs', 'practice_browser.cjs', 'review_browser.cjs']
            for script in scripts:
                subprocess.run(['node', str(ROOT / 'tests' / script)], env=env, cwd=ROOT, check=True)
        finally:
            httpd.shutdown(); httpd.server_close(); worker.join(5)


if __name__ == '__main__':
    main()
