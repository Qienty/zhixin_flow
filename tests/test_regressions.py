"""Run with python -m unittest discover -s tests -v. No real database or model calls."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import ai, api, auth, content, db, insights


class Regressions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tether-test-")
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        for name, value in (("DATA_DIR", directory), ("DB_PATH", directory / "test.db")):
            patcher = patch.object(db, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        config = patch.object(ai, "config", return_value={"enabled": False, "model": "test"})
        config.start()
        self.addCleanup(config.stop)
        db.init_db()
        self.users = [auth.create_user(f"test_user_{i}", "Test-only!SeCrEt42") for i in range(2)]
        self.tokens = [auth.create_session(user["id"])[0] for user in self.users]
        auth._login_fails.clear()

    def request(self, method, path, body=None, user=0, query=None):
        req = api.Request(method, path, query or {"tz_offset": ["480"]}, json.dumps(body).encode() if body is not None else b"",
                          {"cookie": f"{auth.COOKIE_NAME}={self.tokens[user]}"})
        req.client_ip = "test-client"
        return api.dispatch(req)

    def entry(self, **overrides):
        return {"emotion": content.EMOTIONS[0]["key"], "severity": 3, "cause": "test fixture",
                "tz_offset": 480, "occurred_at": db.now_iso(), **overrides}

    def scalar(self, sql, params=()):
        conn = db.connect()
        try:
            return conn.execute(sql, params).fetchone()[0]
        finally:
            conn.close()

    def cache(self, owner=0, key="fixture"):
        uid = self.users[owner]["id"]
        self.assertTrue(ai.cache_put(key, {"summary": "private test fixture"}, "test", uid, ai.data_revision(uid)))
        return key

    def test_concurrent_single_use_redemption(self):
        auth.create_code("ONE-USE", max_uses=1)
        original = db.connect
        ready = threading.Barrier(2)

        class SlowCursor:
            def __init__(self, cursor): self.cursor = cursor
            def fetchone(self):
                row = self.cursor.fetchone()
                # Expose the former check/write race; the transaction must hold the lock here.
                time.sleep(0.1)
                return row

        class Connection:
            def __init__(self): self.conn = original()
            def execute(self, sql, params=()):
                cur = self.conn.execute(sql, params)
                return SlowCursor(cur) if sql.startswith("SELECT * FROM redeem_codes WHERE") else cur
            def __getattr__(self, key): return getattr(self.conn, key)

        def redeem(user):
            ready.wait(timeout=5)
            try:
                auth.redeem(user["id"], "ONE-USE")
                return 200
            except auth.AuthError as exc:
                return exc.status

        with patch.object(db, "connect", Connection), ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(redeem, self.users))
        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(self.scalar("SELECT used FROM redeem_codes"), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM redemptions"), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM users WHERE plan = 'pro'"), 1)

    def test_failed_redemption_rolls_back_quota_and_plan(self):
        auth.create_code("ROLLBACK")
        conn = db.connect()
        conn.execute("CREATE TRIGGER reject_redemption BEFORE INSERT ON redemptions BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        conn.close()
        with self.assertRaises(sqlite3.IntegrityError):
            auth.redeem(self.users[0]["id"], "ROLLBACK")
        self.assertEqual(self.scalar("SELECT used FROM redeem_codes"), 0)
        self.assertEqual(auth.get_user(self.users[0]["id"])["plan"], "free")

    def test_delete_clears_only_owners_cache(self):
        self.request("POST", "/api/entries", self.entry())
        self.cache()
        self.cache(1, "other")
        self.assertEqual(self.request("DELETE", "/api/entries").status, 200)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM entries"), 0)
        self.assertIsNone(ai.cache_get("fixture", self.users[0]["id"]))
        self.assertIsNotNone(ai.cache_get("other", self.users[1]["id"]))
        self.assertIsNone(ai.cache_get("other", self.users[0]["id"]))

    def test_edit_and_single_delete_clear_cache(self):
        entry_id = self.request("POST", "/api/entries", self.entry()).payload["entry"]["id"]
        self.cache()
        self.assertEqual(self.request("PATCH", f"/api/entries/{entry_id}", {"cause": "changed"}).status, 200)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ai_cache"), 0)
        self.cache()
        self.assertEqual(self.request("DELETE", f"/api/entries/{entry_id}").status, 200)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ai_cache"), 0)

    def test_expired_and_malformed_cache_rows_are_deleted(self):
        self.cache(key="old")
        self.cache(key="invalid")
        self.cache(key="fresh")
        conn = db.connect()
        conn.execute("UPDATE ai_cache SET created_at = '2020-01-01T00:00:00+00:00' WHERE key = 'old'")
        conn.execute("UPDATE ai_cache SET created_at = 'invalid' WHERE key = 'invalid'")
        conn.commit()
        conn.close()
        ai.cache_prune()
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ai_cache"), 1)
        self.assertIsNotNone(ai.cache_get("fresh", self.users[0]["id"]))

    def test_inflight_analysis_cannot_restore_deleted_summary(self):
        uid = self.users[0]["id"]
        auth.update_user(uid, plan="pro")
        for n in range(insights.MIN_SAMPLE):
            self.request("POST", "/api/entries", self.entry(cause=f"fixture {n}"))
        def analyze(*args):
            self.assertEqual(self.request("DELETE", "/api/entries").status, 200)
            return {"summary": "obsolete summary"}
        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}), patch.object(ai, "analyze", side_effect=analyze):
            response = self.request("POST", "/api/insights/analyze", {"force": True})
        self.assertEqual(response.status, 409)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ai_cache"), 0)

    def test_legacy_cache_migration_preserves_records(self):
        self.request("POST", "/api/entries", self.entry())
        conn = db.connect()
        conn.execute("DROP TABLE ai_cache")
        conn.execute("CREATE TABLE ai_cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL, model TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("INSERT INTO ai_cache VALUES ('legacy', '{}', 'test', ?)", (db.now_iso(),))
        conn.commit()
        conn.close()
        db.init_db()
        db.init_db()
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM entries"), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ai_cache"), 0)
        self.cache()

    def test_analysis_is_cached_and_reused_for_its_owner(self):
        auth.update_user(self.users[0]["id"], plan="pro")
        for n in range(insights.MIN_SAMPLE):
            self.request("POST", "/api/entries", self.entry(cause=f"fixture {n}"))
        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}), patch.object(ai, "analyze", return_value={"summary": "test summary"}) as analyze:
            first = self.request("POST", "/api/insights/analyze", {"force": True})
            second = self.request("POST", "/api/insights/analyze", {"force": False})
            insights_response = self.request("GET", "/api/insights")
        self.assertEqual(first.status, 200)
        self.assertFalse(first.payload["ai"]["cached"])
        self.assertTrue(second.payload["ai"]["cached"])
        self.assertEqual(insights_response.payload["ai"]["summary"], "test summary")
        analyze.assert_called_once()

    def test_server_maintenance_removes_expired_cache_without_reads(self):
        import server
        self.cache()
        conn = db.connect()
        conn.execute("UPDATE ai_cache SET created_at = '2020-01-01T00:00:00+00:00'")
        conn.commit()
        conn.close()
        service = object.__new__(server.Server)
        service.service_actions()
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ai_cache"), 0)

    def test_users_without_revision_are_migrated_without_losing_sessions(self):
        conn = db.connect()
        for event in ("insert", "update", "delete"):
            conn.execute(f"DROP TRIGGER entries_invalidate_ai_{event}")
        conn.execute("CREATE TABLE legacy_users AS SELECT id, username, pass_hash, pass_salt, plan, region, lang, created_at, updated_at FROM users")
        conn.execute("DROP TABLE users")
        conn.execute("ALTER TABLE legacy_users RENAME TO users")
        conn.commit()
        conn.close()
        db.init_db()
        self.assertEqual(ai.data_revision(self.users[0]["id"]), 0)
        self.assertEqual(self.request("POST", "/api/entries", self.entry()).status, 200)
        self.assertGreater(ai.data_revision(self.users[0]["id"]), 0)

    def test_save_retry_is_idempotent_and_accounts_are_isolated(self):
        first = self.request("POST", "/api/entries", self.entry(client_id="retry-1"))
        second = self.request("POST", "/api/entries", self.entry(client_id="retry-1"))
        self.assertEqual(first.payload["entry"]["id"], second.payload["entry"]["id"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM entries"), 1)
        entry_id = first.payload["entry"]["id"]
        self.assertEqual(self.request("GET", f"/api/entries/{entry_id}", user=1).status, 404)
        self.assertEqual(self.request("DELETE", f"/api/entries/{entry_id}", user=1).status, 404)

    def test_json_round_trip_deduplication_and_atomic_validation(self):
        self.request("POST", "/api/entries", self.entry())
        exported = self.request("GET", "/api/export").payload
        imported = self.request("POST", "/api/import", exported, user=1)
        self.assertEqual(imported.payload["imported"], 1)
        self.assertEqual(self.request("POST", "/api/import", exported, user=1).payload["skipped"], 1)
        invalid = {"entries": [self.entry(cause="new"), self.entry(severity=99)]}
        self.assertEqual(self.request("POST", "/api/import", invalid).status, 422)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM entries"), 2)

    def test_session_logout_and_login_throttle(self):
        self.assertEqual(self.request("POST", "/api/auth/logout").status, 200)
        self.assertEqual(self.request("GET", "/api/entries").status, 401)
        for _ in range(5):
            response = self.request("POST", "/api/auth/login", {"username": self.users[0]["username"], "password": "incorrect"})
            self.assertEqual(response.status, 401)
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": self.users[0]["username"], "password": "incorrect"}).status, 429)
        auth._login_fails.clear()
        self.assertEqual(self.request("POST", "/api/auth/login", {"username": self.users[0]["username"], "password": "Test-only!SeCrEt42"}).status, 200)


if __name__ == "__main__":
    unittest.main()
