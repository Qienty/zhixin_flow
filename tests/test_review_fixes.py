"""Regression coverage for the 2026-11-17 review. Temporary data; no model calls."""
import gzip
import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import test_regressions as fixtures
from app import ai, api, auth, codes, db, insights, limits
from app.assets import StaticAssets
import server


class ReviewFixes(unittest.TestCase):
    setUp = fixtures.Regressions.setUp
    request = fixtures.Regressions.request
    entry = fixtures.Regressions.entry
    scalar = fixtures.Regressions.scalar
    cache = fixtures.Regressions.cache

    def test_code_generation_reserves_atomically_and_bounds_collision_retries(self):
        auth.create_code('QNTY-AAAA-AAAA')
        with patch.object(codes, '_block', side_effect=['AAAA', 'AAAA', 'BBBB', 'CCCC']), patch.object(auth, 'list_codes', side_effect=AssertionError('no full table scan')):
            self.assertEqual(codes.generate_code(max_uses=3, note='test'), 'QNTY-BBBB-CCCC')
        self.assertEqual(self.scalar("SELECT max_uses FROM redeem_codes WHERE code='QNTY-BBBB-CCCC'"), 3)
        with patch.object(codes, '_block', return_value='AAAA'), self.assertRaises(RuntimeError):
            codes.generate_code()
        with patch.object(auth, 'create_code', side_effect=auth.AuthError(400, 'unexpected')), self.assertRaises(auth.AuthError):
            codes.generate_code()

    def test_wheel_types_reject_atomically(self):
        baseline = self.request('PUT', '/api/wheel', {'items': ['walk', 'rest']}).payload['items']
        for item in (5, True, None, [], ['bad'], {'label': 5}, {}):
            with self.subTest(item=item):
                result = self.request('PUT', '/api/wheel', {'items': [item, 'valid']})
                self.assertEqual(result.status, 422)
                self.assertEqual(self.request('GET', '/api/wheel').payload['items'], baseline)
        self.assertEqual(self.request('PUT', '/api/wheel', {'items': [{'label': 'a'}, {'label': 'b'}]}).status, 200)

    def test_keyword_boundaries_and_chinese_phrases(self):
        for text in ('still', 'billion', 'homeworkless', 'applicationless'):
            self.assertNotIn('health', insights.tag_cause(text))
        self.assertNotIn('money', insights.tag_cause('billion'))
        self.assertNotIn('environment', insights.tag_cause('和朋友吵架'))
        self.assertIn('relationship', insights.tag_cause('和朋友吵架'))
        self.assertIn('environment', insights.tag_cause('楼上太吵，装修噪音'))
        self.assertIn('health', insights.tag_cause('I feel ill.'))
        self.assertIn('workload', insights.tag_cause('Extra work, again!'))

    def test_cache_read_is_read_only_and_rejects_expired_or_invalid_timestamps(self):
        uid = self.users[0]['id']
        self.cache(key='fresh'); self.cache(key='old'); self.cache(key='invalid')
        conn = db.connect()
        conn.execute("UPDATE ai_cache SET created_at='2020-01-01T00:00:00+00:00' WHERE key='old'")
        conn.execute("UPDATE ai_cache SET created_at='invalid' WHERE key='invalid'")
        conn.commit(); conn.close()
        original, statements = db.connect, []
        def traced():
            connection = original(); connection.set_trace_callback(statements.append); return connection
        with patch.object(db, 'connect', side_effect=traced):
            self.assertIsNotNone(ai.cache_get('fresh', uid))
            self.assertIsNone(ai.cache_get('old', uid))
            self.assertIsNone(ai.cache_get('invalid', uid))
            self.assertIsNone(ai.cache_get('fresh', self.users[1]['id']))
        self.assertFalse(any(s.lstrip().upper().startswith(('DELETE', 'UPDATE', 'BEGIN', 'COMMIT')) for s in statements))
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM ai_cache'), 3)
        ai.cache_prune()
        self.assertEqual(self.scalar('SELECT COUNT(*) FROM ai_cache'), 1)

    def test_cache_prune_uses_expiry_index(self):
        conn = db.connect()
        try:
            for where, args in [('julianday(created_at) IS NULL', ()), ('julianday(created_at) <= julianday(?)', (db.now_iso(),))]:
                plan = [r[3] for r in conn.execute('EXPLAIN QUERY PLAN DELETE FROM ai_cache WHERE ' + where, args)]
                self.assertTrue(any('idx_ai_cache_expiry' in row for row in plan), plan)
        finally: conn.close()

    def test_analysis_single_flight_cooldown_and_cached_reads(self):
        auth.update_user(self.users[0]['id'], plan='pro')
        for n in range(insights.MIN_SAMPLE): self.request('POST', '/api/entries', self.entry(cause=f'work {n}'))
        entered, release = threading.Event(), threading.Event()
        def analyze(*args):
            entered.set()
            if not release.wait(5): raise RuntimeError('test release timeout')
            return {'summary': 'safe summary', 'reminders': ['safe reminder']}
        with patch.object(ai, 'config', return_value={'enabled': True, 'model': 'test'}), patch.object(ai, 'analyze', side_effect=analyze) as model:
            with ThreadPoolExecutor(max_workers=1) as pool:
                first = pool.submit(self.request, 'POST', '/api/insights/analyze', {'force': True})
                try:
                    self.assertTrue(entered.wait(5))
                    denied = self.request('POST', '/api/insights/analyze', {'force': True})
                    self.assertEqual(denied.status, 429)
                    self.assertIn('Retry-After', denied.headers)
                finally: release.set()
                self.assertEqual(first.result(5).status, 200)
            self.assertEqual(self.request('POST', '/api/insights/analyze', {'force': True}).status, 429)
            cached = self.request('POST', '/api/insights/analyze', {})
            self.assertEqual(cached.status, 200)
            self.assertTrue(cached.payload['ai']['cached'])
            self.assertEqual(cached.payload['ai']['reminders'], ['safe reminder'])
            self.assertEqual(self.request('GET', '/api/insights').payload['ai']['reminders'], ['safe reminder'])
            self.assertEqual(model.call_count, 1)

    def test_analysis_global_capacity_and_exception_release(self):
        with patch.dict(os.environ, {'TETHER_AI_MAX_CONCURRENT': '1'}), patch.object(limits.time, 'monotonic', return_value=100):
            with limits.analysis_slot(1):
                with self.assertRaises(limits.LimitError):
                    with limits.analysis_slot(2): pass
            with self.assertRaises(RuntimeError):
                with limits.analysis_slot(2): raise RuntimeError('model failed')
            with limits.analysis_slot(3): pass
        with patch.object(limits.time, 'monotonic', return_value=161):
            with limits.analysis_slot(1): pass

    def test_registration_throttle_precedes_password_hashing(self):
        with patch.dict(os.environ, {'TETHER_REGISTER_LIMIT': '2'}), patch.object(auth, 'create_user', return_value=self.users[0]) as create:
            body = {'username': 'new_user', 'password': 'Strong.Test.2026!'}
            self.assertEqual(self.request('POST', '/api/auth/register', body).status, 200)
            self.assertEqual(self.request('POST', '/api/auth/register', body).status, 200)
            denied = self.request('POST', '/api/auth/register', body)
            self.assertEqual(denied.status, 429); self.assertIn('Retry-After', denied.headers)
            self.assertEqual(create.call_count, 2)

    def test_unknown_username_still_verifies_password(self):
        with patch.object(auth, 'verify_password', return_value=False) as verify:
            with self.assertRaises(auth.AuthError) as error: auth.authenticate('absent_user', 'incorrect')
            self.assertEqual(error.exception.status, 401)
            verify.assert_called_once()

    def test_import_deduplicates_batch_and_keeps_owner_cache_invalidation(self):
        self.cache(key='owner'); self.cache(owner=1, key='other')
        first = self.entry(cause='same', severity=None)
        second = self.entry(cause='different')
        before = ai.data_revision(self.users[0]['id'])
        result = self.request('POST', '/api/import', {'entries': [first, first, second]})
        self.assertEqual(result.payload, {'imported': 2, 'skipped': 1})
        self.assertEqual(ai.data_revision(self.users[0]['id']), before + 2)
        self.assertIsNone(ai.cache_get('owner', self.users[0]['id']))
        self.assertIsNotNone(ai.cache_get('other', self.users[1]['id']))
        self.assertEqual(self.request('POST', '/api/import', {'entries': [first, second]}).payload, {'imported': 0, 'skipped': 2})

    def test_entry_pagination_filters_and_limit_boundaries(self):
        ids = []
        for severity in (3, 5, None, 5, 1):
            row = self.request('POST', '/api/entries', self.entry(severity=severity, cause='find me' if severity == 5 else 'other', occurred_at='2026-11-15T00:00:00+00:00')).payload['entry']
            ids.append((row['id'], severity))
        seen, cursor = [], None
        for _ in range(4):
            query = {'limit': ['2'], 'sort': ['severity']}
            if cursor: query['cursor'] = [cursor]
            page = self.request('GET', '/api/entries', query=query).payload
            seen.extend(x['id'] for x in page['items']); cursor = page['next_cursor']
            if not cursor: break
        self.assertEqual(seen, [x[0] for x in sorted(ids, key=lambda x: (x[1] or 0, x[0]), reverse=True)])
        for value in ('0', '101', 'bad'):
            self.assertEqual(self.request('GET', '/api/entries', query={'limit': [value]}).status, 400)
        self.assertEqual(self.request('GET', '/api/entries', query={'q': ['find me']}).payload['total'], 2)
        self.assertEqual(self.request('GET', '/api/entries', query={'unfilled': ['true']}).payload['total'], 1)
        self.assertEqual(self.request('GET', '/api/entries', query={'cursor': ['not-json']}).status, 400)

    def test_date_window_offsets_and_csv_formula_escaping(self):
        fixed = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
        with patch.object(db, 'now_utc', return_value=fixed):
            for offset, expected in ((840, ('2026-11-04', '2026-11-17')), (-840, ('2026-11-02', '2026-11-15'))):
                req = api.Request('GET', '/api/home', {'tz_offset': [str(offset)]}, b'', {})
                self.assertEqual(api._dates(req), expected)
            self.assertEqual(api._normalize_time('2026-11-16T23:30:00Z', 330)[1:], ('2026-11-17', 5))
        for value in ('=1+1', ' +SUM(A1)', '-2+3', '@formula', '\tvalue', '\rvalue', '\nvalue'):
            self.assertEqual(api._csv_cell(value), "'" + value)
        self.request('POST', '/api/entries', self.entry(cause='=1+1', note='@formula'))
        output = self.request('GET', '/api/export', query={'format': ['csv']})
        self.assertIn("'=1+1", output.raw); self.assertIn("'@formula", output.raw)

    @contextmanager
    def http_server(self, **settings):
        class Quiet(server.Handler):
            def log_message(self, *args): pass
        class Isolated(server.Server):
            def service_actions(self): pass
        with patch.dict(os.environ, settings):
            httpd = Isolated(('127.0.0.1', 0), Quiet)
            worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
            try: yield httpd
            finally: httpd.shutdown(); httpd.server_close(); worker.join(5)

    def http_get(self, httpd, path, headers=None, method='GET'):
        conn = http.client.HTTPConnection(*httpd.server_address, timeout=5)
        try:
            conn.request(method, path, headers={'Connection': 'close', **(headers or {})})
            res = conn.getresponse(); return res.status, dict(res.getheaders()), res.read()
        finally: conn.close()

    def test_http_compression_conditional_cache_head_and_api_no_store(self):
        with self.http_server() as httpd:
            status, headers, body = self.http_get(httpd, '/js/app.js', {'Accept-Encoding': 'gzip'})
            self.assertEqual(status, 200); self.assertEqual(headers['Content-Encoding'], 'gzip')
            self.assertEqual(gzip.decompress(body), (server.FRONTEND / 'js/app.js').read_bytes())
            self.assertIn("object-src 'none'", headers['Content-Security-Policy'])
            self.assertIn('Accept-Encoding', headers['Vary'])
            status, _, body = self.http_get(httpd, '/js/app.js', {'Accept-Encoding': 'gzip', 'If-None-Match': 'W/' + headers['ETag']})
            self.assertEqual((status, body), (304, b''))
            status, plain, body = self.http_get(httpd, '/js/app.js', {'Accept-Encoding': 'gzip;q=0, *;q=1'})
            self.assertNotIn('Content-Encoding', plain); self.assertNotEqual(plain['ETag'], headers['ETag'])
            status, _, body = self.http_get(httpd, '/js/app.js', {'If-Modified-Since': plain['Last-Modified']})
            self.assertEqual((status, body), (304, b''))
            status, head, body = self.http_get(httpd, '/js/app.js', method='HEAD')
            self.assertEqual(body, b''); self.assertGreater(int(head['Content-Length']), 0)
            status, headers, body = self.http_get(httpd, '/api/health')
            self.assertEqual(status, 200); self.assertEqual(headers['Cache-Control'], 'no-store')
            _, worker_headers, _ = self.http_get(httpd, '/sw.js')
            self.assertEqual(self.http_get(httpd, '/sw.js', {'If-Modified-Since': worker_headers['Last-Modified']})[0], 200)
            self.assertEqual(self.http_get(httpd, '/sw.js', {'If-None-Match': worker_headers['ETag']})[0], 304)

    def test_http_large_upload_returns_413_for_eager_client_and_expect_continue(self):
        with self.http_server() as httpd:
            conn = http.client.HTTPConnection(*httpd.server_address, timeout=5)
            try:
                conn.request('POST', '/api/import', body=b'x' * (server.MAX_BODY + 1), headers={'Connection': 'close'})
                res = conn.getresponse()
                self.assertEqual(res.status, 413); self.assertIn('8 MiB', res.read().decode()); self.assertEqual(res.getheader('Connection'), 'close')
            finally: conn.close()
            with socket.create_connection(httpd.server_address, timeout=5) as client:
                client.sendall(b'POST /api/import HTTP/1.1\r\nHost: localhost\r\nExpect: 100-continue\r\nContent-Length: 8388609\r\n\r\n')
                raw = client.recv(4096)
                self.assertTrue(raw.startswith(b'HTTP/1.1 413'), raw)

    def test_http_invalid_framing_paths_and_development_pages(self):
        with self.http_server(TETHER_DEV_PAGES='0') as httpd:
            for path in ('/../backend/server.py', '/..%5cbackend%5cserver.py', '/__harness.html', '/__smoke.html'):
                self.assertEqual(self.http_get(httpd, path)[0], 404)
            status, _, body = self.http_get(httpd, '/sw.js')
            self.assertEqual(status, 200); self.assertNotIn(b'__ASSET_HASH__', body)
            with socket.create_connection(httpd.server_address, timeout=5) as client:
                client.sendall(b'POST /api/import HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\nContent-Length: 3\r\n\r\n')
                self.assertTrue(client.recv(4096).startswith(b'HTTP/1.1 400'))

    def test_http_connection_capacity_and_idle_timeout(self):
        with self.http_server(TETHER_HTTP_MAX_CONNECTIONS='1', TETHER_HTTP_TIMEOUT='1') as httpd:
            def wait_for_slot():
                deadline = time.monotonic() + 3
                while not httpd._slots.acquire(blocking=False):
                    if time.monotonic() >= deadline: self.fail('connection slot was not released')
                    time.sleep(.01)
                httpd._slots.release()
            with socket.create_connection(httpd.server_address, timeout=5) as first:
                first.sendall(b'GET /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n')
                response = http.client.HTTPResponse(first); response.begin(); response.read()
                self.assertEqual(self.http_get(httpd, '/api/health')[0], 503)
                self.assertEqual(first.recv(1), b'')
            wait_for_slot()
            self.assertEqual(self.http_get(httpd, '/api/health')[0], 200)
            wait_for_slot()
            with socket.create_connection(httpd.server_address, timeout=5) as slow:
                slow.sendall(b'POST /api/import HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\n\r\nx')
                response = http.client.HTTPResponse(slow); response.begin()
                self.assertEqual(response.status, 408); response.read()

    def test_asset_version_tracks_equal_size_edits_deletions_and_caches_reads(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); script = root / 'sw.js'; asset = root / 'a.js'
            script.write_text('const version="__ASSET_HASH__";', encoding='utf8'); asset.write_text('AAAA', encoding='utf8')
            store = StaticAssets(root); original = store.representation(script)[0]
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('cached bytes should be reused')):
                self.assertEqual(store.representation(script)[0], original)
            stamp = asset.stat().st_mtime_ns
            asset.write_text('BBBB', encoding='utf8'); os.utime(asset, ns=(stamp + 1000000, stamp + 1000000))
            changed = store.representation(script)[0]; self.assertNotEqual(changed, original)
            asset.unlink(); self.assertNotEqual(store.representation(script)[0], changed)


if __name__ == '__main__': unittest.main()
