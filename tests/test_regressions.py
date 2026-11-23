"""Run with python -m unittest discover -s tests -v. No real database or model calls."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import ai, api, auth, content, db, insights, limits

# setUp patches ai.config for the whole suite; keep the real parser for the config test.
_real_ai_config = ai.config


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
        limits.reset()

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

    def test_expression_translation_requires_login_and_validates_payload(self):
        body = {"text": "I feel a little unsure.", "target_lang": "zh", "mode": "space"}
        unauthenticated = api.Request("POST", "/api/expression/translate", {}, json.dumps(body).encode(), {})
        self.assertEqual(api.dispatch(unauthenticated).status, 401)

        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}):
            invalid_target = self.request("POST", "/api/expression/translate", {**body, "target_lang": "fr"})
            too_long = self.request("POST", "/api/expression/translate", {**body, "text": "x" * 501})
            invalid_mode = self.request("POST", "/api/expression/translate", {**body, "mode": "diagnose"})
        self.assertEqual(invalid_target.status, 400)
        self.assertEqual(too_long.status, 422)
        self.assertEqual(invalid_mode.status, 400)

    def test_expression_translation_is_explicit_and_never_persisted(self):
        body = {"text": "I feel a little unsure.", "target_lang": "zh", "mode": "clarify", "emotion": "anxiety"}
        result = {"text": "我有点拿不准。", "target_lang": "zh", "intent": "share", "cultural_notes": [], "safety": "ok"}
        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}), \
                patch.object(ai, "rewrite_expression", return_value=result) as rewrite:
            response = self.request("POST", "/api/expression/translate", body)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.payload, result)
        rewrite.assert_called_once_with(
            body["text"], "zh", emotion="anxiety", mode="clarify",
            scene="", tone="polite", relation="peer", details={},
        )
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM entries"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ai_cache"), 0)

    def test_expression_translation_returns_service_unavailable_when_ai_is_disabled(self):
        body = {"text": "I feel a little unsure.", "target_lang": "zh"}
        with patch.object(ai, "rewrite_expression") as rewrite:
            response = self.request("POST", "/api/expression/translate", body)
        self.assertEqual(response.status, 503)
        rewrite.assert_not_called()

    def test_expression_translation_model_failure_is_a_soft_api_error(self):
        body = {"text": "I feel a little unsure.", "target_lang": "zh"}
        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}), \
                patch.object(ai, "rewrite_expression", side_effect=ai.AIError("provider timeout")):
            response = self.request("POST", "/api/expression/translate", body)
        self.assertEqual(response.status, 502)
        self.assertIn("provider timeout", response.payload["error"])

    def test_expression_scene_validates_fields_and_builds_a_sendable_message(self):
        base = {"target_lang": "en", "scene": "deadline", "tone": "polite", "relation": "professor",
                "details": {"what": "初稿", "when": "周五", "want": "下周三", "extra": "分析还要时间"}}
        result = {"text": "Dear Prof. Lee, could I move the draft?", "target_lang": "en", "intent": "ask_advice",
                  "cultural_notes": ["先给结论"], "alternatives": [{"tone": "direct", "text": "Hi Prof. Lee — could I move it?"}],
                  "safety": "ok"}
        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}), \
                patch.object(ai, "rewrite_expression", return_value=result) as rewrite:
            response = self.request("POST", "/api/expression/translate", base)
            # A scene may send details with no free text at all. Another account
            # keeps the per-account model cooldown out of this assertion.
            blank_text = self.request("POST", "/api/expression/translate", {**base, "text": ""}, user=1)
        self.assertEqual(response.status, 200, response.payload)
        self.assertEqual(response.payload["alternatives"][0]["tone"], "direct")
        self.assertEqual(blank_text.status, 200, blank_text.payload)
        rewrite.assert_called_with(
            "", "en", emotion="", mode="space", scene="deadline", tone="polite",
            relation="professor", details={"what": "初稿", "when": "周五", "want": "下周三", "extra": "分析还要时间"},
        )

    def test_expression_scene_rejects_unknown_values_and_empty_requests(self):
        base = {"target_lang": "en", "scene": "deadline", "details": {"what": "x"}}
        cases = [
            {**base, "scene": "promotion"},
            {**base, "tone": "rude"},
            {**base, "relation": "boss"},
            {**base, "details": {"budget": "100"}},
            {**base, "details": {"what": "x" * 201}},
            {"target_lang": "en"},
            {"target_lang": "en", "scene": "deadline", "details": {}},
        ]
        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}), \
                patch.object(ai, "rewrite_expression") as rewrite:
            for body in cases:
                self.assertEqual(self.request("POST", "/api/expression/translate", body).status, 400, body)
        rewrite.assert_not_called()

    def test_expression_scene_alternatives_are_validated_and_bounded(self):
        raw = {
            "text": "Could I move the draft to next Wednesday?",
            "intent": "ask_advice",
            "cultural_notes": ["a", "b", "c"],
            "alternatives": [
                {"tone": "direct", "text": "Hi — move the draft to Wednesday?"},
                {"tone": "polite", "text": "Could I move the draft to next Wednesday?"},
                {"tone": "rude", "text": "Move it."},
                {"tone": "close", "text": "Could I move the draft to next Wednesday?"},
                {"tone": "close", "text": "Any chance the draft could slip to Wednesday?"},
                {"tone": "close", "text": "I think this is a diagnosis of my anxiety."},
            ],
            "safety": "ok",
        }
        validated = ai._validate_expression(raw, "en")
        self.assertEqual(len(validated["alternatives"]), 2)
        self.assertEqual(validated["alternatives"][0]["tone"], "direct")
        self.assertEqual(validated["alternatives"][1]["text"], "Any chance the draft could slip to Wednesday?")
        self.assertEqual(len(validated["cultural_notes"]), 2)
        self.assertEqual(validated["target_lang"], "en")

    def test_expression_interpret_requires_login_and_validates_payload(self):
        body = {"text": "Could you be more careful next time?", "source_lang": "en", "target_lang": "zh"}
        unauthenticated = api.Request("POST", "/api/expression/interpret", {}, json.dumps(body).encode(), {})
        self.assertEqual(api.dispatch(unauthenticated).status, 401)
        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}):
            cases = [
                {"text": ""},
                {"text": "x" * 501},
                {**body, "source_lang": "fr"},
                {**body, "target_lang": "de"},
                {**body, "relation": "boss"},
            ]
            for item in cases:
                result = self.request("POST", "/api/expression/interpret", item)
                self.assertIn(result.status, (400, 422), item)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM entries"), 0)

    def test_expression_interpret_is_a_reading_and_never_persisted(self):
        body = {"text": "Could you be more careful next time?", "source_lang": "en", "target_lang": "zh", "relation": "manager"}
        result = {"meaning": "对方希望你下次更注意", "tone": "tense", "subtext": "可能对上次的事还没放下（不确定）",
                  "replies": [{"tone": "polite", "text": "明白，下次我会先确认。"}], "cultural_notes": ["这句话在英语里偏直接"],
                  "target_lang": "zh", "safety": "ok"}
        with patch.object(ai, "config", return_value={"enabled": True, "model": "test"}), \
                patch.object(ai, "interpret_message", return_value=result) as interpret:
            response = self.request("POST", "/api/expression/interpret", body)
        self.assertEqual(response.status, 200, response.payload)
        self.assertEqual(response.payload, result)
        interpret.assert_called_once_with(body["text"], source_lang="en", target_lang="zh", relation="manager")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM entries"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM ai_cache"), 0)

    def test_expression_interpret_returns_service_unavailable_when_ai_is_disabled(self):
        body = {"text": "Could you be more careful next time?", "source_lang": "en", "target_lang": "zh"}
        with patch.object(ai, "interpret_message") as interpret:
            response = self.request("POST", "/api/expression/interpret", body)
        self.assertEqual(response.status, 503)
        interpret.assert_not_called()

    def test_interpretation_output_separates_guesswork_and_rejects_clinical_terms(self):
        raw = {
            "meaning": "对方在提一个具体的请求",
            "tone": "weird",
            "subtext": "可能有点不满（不确定）",
            "replies": [
                {"tone": "polite", "text": "明白，下次我先确认。"},
                {"tone": "rude", "text": "drop this"},
                {"tone": "direct", "text": "x" * 601},
                {"tone": "close", "text": "收到，下次注意。"},
                {"tone": "polite", "text": "我明白。"},
            ],
            "cultural_notes": ["a", "b", "c"],
        }
        validated = ai._validate_interpretation(raw, "zh")
        self.assertEqual(validated["tone"], "ambiguous")
        self.assertEqual([reply["tone"] for reply in validated["replies"]], ["polite", "close"])
        self.assertEqual(len(validated["cultural_notes"]), 2)
        self.assertEqual(validated["target_lang"], "zh")
        for broken in (
            {**raw, "subtext": "这可能是抑郁症的前兆"},
            {**raw, "replies": [{"tone": "polite", "text": "建议你去做个诊断"}]},
            {**raw, "meaning": ""},
        ):
            with self.assertRaises(ai.AIError):
                ai._validate_interpretation(broken, "zh")

    def test_ai_config_exposes_provider_and_fallback_without_secrets(self):
        env = {
            "TETHER_AI_API_KEY": "sk-test-value", "TETHER_AI_BASE_URL": "https://api.deepseek.com/v1",
            "TETHER_AI_MODEL": "deepseek-flash", "TETHER_AI_FALLBACK_BASE_URL": "http://127.0.0.1:11434/v1",
            "TETHER_AI_FALLBACK_MODEL": "qwen3",
        }
        with patch.dict(os.environ, env, clear=False), patch.object(ai, "_env_loaded", True):
            cfg = _real_ai_config()
            with patch.object(ai, "config", side_effect=lambda: cfg):
                public = ai.public_config()
            chain = ai.endpoints(cfg)
        self.assertEqual(public["provider"], "deepseek")
        self.assertTrue(public["fallback"])
        self.assertNotIn("api_key", public)
        self.assertNotIn("base_url", public)
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[1]["provider"], "local")
        self.assertEqual(chain[1]["model"], "qwen3")

    def test_ai_call_falls_back_to_the_secondary_endpoint(self):
        cfg = {
            "enabled": True, "api_key": "primary-key", "base_url": "https://primary.invalid/v1",
            "model": "primary-model", "provider": "custom", "effort": "high", "timeout": 5,
            "fallback": {"base_url": "https://backup.invalid/v1", "model": "backup-model", "api_key": "backup-key"},
        }
        seen = []

        class Reply:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self):
                return b'{"choices":[{"message":{"content":"{\\"ok\\": true}"},"finish_reason":"stop"}]}'

        def fake_open(request, timeout=None):
            seen.append((request.full_url, request.headers.get("Authorization")))
            if "primary.invalid" in request.full_url:
                raise urllib.error.URLError("primary unreachable")
            return Reply()

        with patch.object(ai.urllib.request, "urlopen", side_effect=fake_open), \
                patch.object(ai.time, "sleep", lambda *_: None):
            content, finish = ai._post(cfg, [{"role": "user", "content": "hi"}], 64)
        self.assertEqual(finish, "stop")
        self.assertIn("ok", content)
        self.assertIn("backup.invalid", seen[-1][0])
        self.assertEqual(seen[-1][1], "Bearer backup-key")

    def test_ai_selftest_is_gated_and_rate_limited(self):
        with patch.object(ai, "config", return_value={"enabled": False}):
            self.assertEqual(self.request("POST", "/api/ai/selftest").status, 503)
        result = {"ok": True, "ms": 12, "model": "test-model", "provider": "custom", "fallback": False}
        with patch.object(ai, "config", return_value={"enabled": True, "model": "test-model"}), \
                patch.object(ai, "selftest", return_value=result) as probe:
            response = self.request("POST", "/api/ai/selftest")
            throttled = self.request("POST", "/api/ai/selftest")
        self.assertEqual(response.status, 200, response.payload)
        self.assertEqual(response.payload, result)
        probe.assert_called_once_with()
        self.assertEqual(throttled.status, 429)

    def test_rate_limit_messages_follow_the_interface_language(self):
        result = {"ok": True, "ms": 1, "model": "test-model", "provider": "custom", "fallback": False}
        with patch.object(ai, "config", return_value={"enabled": True, "model": "test-model"}), \
                patch.object(ai, "selftest", return_value=result):
            first = self.request("POST", "/api/ai/selftest")
            english = self.request("POST", "/api/ai/selftest", query={"tz_offset": ["480"], "lang": ["en"]})
            chinese = self.request("POST", "/api/ai/selftest", query={"tz_offset": ["480"], "lang": ["zh"]})
        self.assertEqual(first.status, 200)
        self.assertEqual(english.status, 429)
        self.assertIn("model was just called", english.payload["error"])
        self.assertIn("秒", chinese.payload["error"])
        self.assertIn("Retry-After", english.headers)

    def test_interface_language_preference_is_stored_separately_from_the_default(self):
        meta = self.request("GET", "/api/meta").payload
        self.assertFalse(meta["lang_set"])  # 新账号只是默认值，不该覆盖浏览器语言
        self.assertEqual(meta["lang"], "zh")
        updated = self.request("PATCH", "/api/account/settings", {"lang": "en"})
        self.assertEqual(updated.status, 200)
        self.assertTrue(updated.payload["lang_set"])
        self.assertEqual(updated.payload["lang"], "en")
        meta = self.request("GET", "/api/meta").payload
        self.assertTrue(meta["lang_set"])
        self.assertEqual(meta["lang"], "en")

    def test_legacy_users_gain_lang_set_without_losing_sessions(self):
        conn = db.connect()
        conn.execute("ALTER TABLE users DROP COLUMN lang_set")
        conn.commit()
        conn.close()
        db.init_db()
        db.init_db()
        user = auth.get_user(self.users[0]["id"])
        self.assertFalse(user["lang_set"])
        self.assertEqual(self.request("GET", "/api/bootstrap").payload["account"]["lang_set"], False)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM sessions"), 2)

    def test_crisis_resources_carry_provenance_and_every_region_is_actionable(self):
        payload = self.request("GET", "/api/crisis").payload
        self.assertEqual(payload["verified_at"], "2026-11-22")
        self.assertTrue(payload["source"].startswith("https://"))
        self.assertEqual(set(content.CRISIS_SOURCES), set(content.REGION_KEYS))
        for region, data in content.CRISIS.items():
            self.assertTrue(data["lines"], region)
            for line in data["lines"]:
                self.assertTrue(line["name"], region)
                self.assertTrue(line["contact"]["zh"] and line["contact"]["en"], region)
                # 每条资源要么能拨号，要么有链接，否则等于没给
                self.assertTrue(line.get("number") or line.get("url"), f"{region}:{line['name']}")

    def test_bearer_token_login_works_where_cookies_cannot(self):
        """壳内无法可靠携带跨站 Cookie，用 Authorization: Bearer 走同一张会话表。"""
        response = self.request(
            "POST", "/api/auth/login",
            {"username": self.users[0]["username"], "password": "Test-only!SeCrEt42"},
        )
        # 网页端：只有 Cookie，响应体里不出现 token
        self.assertNotIn("token", response.payload)
        self.assertIn("Set-Cookie", response.headers)

        native = api.Request(
            "POST", "/api/auth/login", {},
            json.dumps({"username": self.users[0]["username"], "password": "Test-only!SeCrEt42"}).encode(),
            {"x-tether-token-auth": "bearer"},
        )
        native.client_ip = "test-client"
        session = api.dispatch(native)
        self.assertEqual(session.status, 200, session.payload)
        token = session.payload["token"]
        self.assertEqual(session.payload["token_type"], "Bearer")
        self.assertGreater(session.payload["expires_in"], 0)
        auth._login_fails.clear()

        # 只带 Bearer、不带 Cookie，也能拿到自己的数据
        req = api.Request("GET", "/api/account", {"tz_offset": ["480"]}, b"", {"authorization": f"Bearer {token}"})
        req.client_ip = "test-client"
        result = api.dispatch(req)
        self.assertEqual(result.status, 200, result.payload)
        self.assertEqual(result.payload["user"]["username"], self.users[0]["username"])

    def test_bearer_and_cookie_are_both_revoked_by_logout(self):
        token = auth.create_session(self.users[0]["id"])[0]
        req = api.Request("POST", "/api/auth/logout", {}, b"", {"authorization": f"Bearer {token}"})
        req.client_ip = "test-client"
        self.assertEqual(api.dispatch(req).status, 200)
        after = api.Request("GET", "/api/account", {"tz_offset": ["480"]}, b"", {"authorization": f"Bearer {token}"})
        after.client_ip = "test-client"
        self.assertEqual(api.dispatch(after).status, 401)
        # 网页那条 Cookie 会话不受影响
        self.assertEqual(self.request("GET", "/api/account").status, 200)

    def test_invalid_or_malformed_bearer_is_treated_as_anonymous(self):
        cases = ["", "Bearer", "Bearer ", "Basic abcdefghijklmnopqrstuvwx", "Bearer short", f"Bearer {'x' * 200}"]
        for header in cases:
            request = api.Request("GET", "/api/account", {"tz_offset": ["480"]}, b"", {"authorization": header})
            request.client_ip = "test-client"
            self.assertEqual(api.dispatch(request).status, 401, header)
        # 合法格式但会话不存在
        request = api.Request("GET", "/api/account", {}, b"", {"authorization": "Bearer " + "a" * 43})
        request.client_ip = "test-client"
        self.assertEqual(api.dispatch(request).status, 401)

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
