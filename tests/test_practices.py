"""Practice lifecycle regressions. Temporary databases; no real user data."""
import json
from datetime import timedelta
import unittest
from unittest.mock import patch
import test_regressions as fixtures
from app import api, db, practices


class PracticeTests(unittest.TestCase):
    setUp = fixtures.Regressions.setUp
    request = fixtures.Regressions.request
    entry = fixtures.Regressions.entry
    scalar = fixtures.Regressions.scalar

    def start(self, **overrides):
        body = {"practice_id": "grounding", "client_id": "start-key-123", "tz_offset": 480, **overrides}
        result = self.request("POST", "/api/practice-sessions", body)
        self.assertIn(result.status, (200, 201), result.payload)
        return result.payload

    def advance(self, session, **overrides):
        body = {"revision": session["revision"], "request_id": f'patch-key-{session["id"]}-{session["revision"]}', **overrides}
        result = self.request("PATCH", f'/api/practice-sessions/{session["id"]}', body)
        self.assertEqual(result.status, 200, result.payload)
        return result.payload

    def complete(self, session, **feedback):
        for index in range(session["step_index"] + 1, len(session["practice"]["steps"]) + 1):
            session = self.advance(session, step_index=index)
        return self.advance(session, status="completed", **feedback)

    def test_full_lifecycle_and_optional_ratings(self):
        session = self.start()
        self.assertIsNone(session["before_score"])
        session = self.complete(session, note="Saved reflection")
        self.assertEqual(session["status"], "completed")
        self.assertIsNotNone(session["finished_at"])
        self.assertIsNone(session["after_score"])
        saved = self.request("GET", f'/api/practice-sessions/{session["id"]}').payload
        self.assertEqual(saved["note"], "Saved reflection")
        self.assertEqual(saved["revision"], 4)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM entries"), 0)

    def test_lost_responses_are_idempotent_and_conflicts_do_not_overwrite(self):
        first = self.start(before_score=4)
        self.assertEqual(self.start(before_score=4)["id"], first["id"])
        conflict = self.request("POST", "/api/practice-sessions", {"practice_id": "grounding", "client_id": "start-key-123", "tz_offset": 480, "before_score": 2})
        self.assertEqual(conflict.status, 409)
        updated = self.advance(first, step_index=1)
        replay = self.advance(first, step_index=1)
        self.assertEqual(replay["revision"], updated["revision"])
        stale = self.request("PATCH", f'/api/practice-sessions/{first["id"]}', {"request_id": "other-tab-key", "revision": 0, "step_index": 1})
        self.assertEqual(stale.status, 409)
        terminal = self.complete(updated)
        terminal_replay = self.request("PATCH", f'/api/practice-sessions/{first["id"]}', {"request_id": f'patch-key-{first["id"]}-3', "revision": 3, "status": "completed"})
        self.assertEqual(terminal_replay.payload["revision"], terminal["revision"])
        invalid = self.request("PATCH", f'/api/practice-sessions/{first["id"]}', {"request_id": "after-terminal", "revision": terminal["revision"], "note": "overwrite"})
        self.assertEqual(invalid.status, 409)

    def test_user_isolation_and_authentication(self):
        first = self.start()
        path = f'/api/practice-sessions/{first["id"]}'
        for method, body in [("GET", None), ("DELETE", None), ("PATCH", {"request_id": "other-owner", "revision": 0, "note": "no"})]:
            self.assertEqual(self.request(method, path, body, user=1).status, 404)
        for path in ("/api/practices", "/api/practice-sessions", "/api/practice-summary", "/api/practice-export"):
            result = api.dispatch(api.Request("GET", path, {}, b"", {}))
            self.assertEqual(result.status, 401)
        self.assertEqual(self.request("GET", "/api/practice-sessions", user=1).payload["total"], 0)
        self.assertEqual(self.request("GET", "/api/practice-export", user=1).payload["count"], 0)
        self.assertEqual(self.request("GET", "/api/practice-summary", user=1).payload["completed"], 0)

    def test_validation_rejects_bad_scores_progress_and_text(self):
        for value in (True, 0, 6, 2.5, "3"):
            result = self.request("POST", "/api/practice-sessions", {"practice_id": "grounding", "client_id": "validation-key", "tz_offset": 480, "before_score": value})
            self.assertEqual(result.status, 400)
        session = self.start()
        cases = [{"step_index": 2}, {"step_index": True}, {"status": "completed"}, {"after_score": 6}, {"relief": "cured"}, {"note": "x" * 501}, {"responses": {"unknown": "test"}}, {"revision": True}, {"user_id": 2}]
        for change in cases:
            result = self.request("PATCH", f'/api/practice-sessions/{session["id"]}', {"request_id": "validation-key", "revision": 0, **change})
            self.assertEqual(result.status, 400, change)
        self.assertEqual(self.request("GET", '/api/practice-sessions/' + '9' * 40).status, 404)
        self.assertEqual(self.scalar("SELECT revision FROM practice_sessions"), 0)

    def test_entry_links_and_delete_keep_independent_records(self):
        entry = self.request("POST", "/api/entries", self.entry()).payload["entry"]
        foreign = self.request("POST", "/api/practice-sessions", {"practice_id": "grounding", "client_id": "foreign-entry", "tz_offset": 480, "entry_id": entry["id"]}, user=1)
        self.assertEqual(foreign.status, 404)
        session = self.start(entry_id=entry["id"])
        self.assertEqual(self.request("GET", "/api/practice-sessions", query={"entry_id": [str(entry["id"])]}).payload["total"], 1)
        self.request("DELETE", f'/api/practice-sessions/{session["id"]}')
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM entries"), 1)
        session = self.start(entry_id=entry["id"])
        self.request("DELETE", f'/api/entries/{entry["id"]}')
        self.assertIsNone(self.request("GET", f'/api/practice-sessions/{session["id"]}').payload["entry_id"])

    def test_snapshots_localization_and_text_survive_restart(self):
        session = self.start(practice_id="notice-thoughts")
        session = self.advance(session, responses={"thought": "A private thought"}, step_index=1)
        db.init_db()
        with patch.dict(practices.PRACTICES["notice-thoughts"]["zh"], {"title": "changed later"}):
            saved = self.request("GET", f'/api/practice-sessions/{session["id"]}').payload
            self.assertNotEqual(saved["practice"]["title"], "changed later")
        en = self.request("GET", f'/api/practice-sessions/{session["id"]}', query={"lang": ["en"]}).payload
        self.assertEqual(en["practice"]["title"], "Notice a thought")
        self.assertEqual(en["responses"]["thought"], "A private thought")
        exported = self.request("GET", "/api/practice-export").payload
        self.assertEqual(exported["practice_sessions"][0]["responses"], en["responses"])
        self.assertNotIn("user_id", exported["practice_sessions"][0])

    def test_summary_uses_only_completed_paired_ratings_and_current_window(self):
        self.complete(self.start(client_id="paired-session", before_score=5), after_score=3, relief="better")
        self.complete(self.start(client_id="unrated-session"))
        self.advance(self.start(client_id="stopped-session", before_score=1), status="stopped", after_score=5)
        old = self.complete(self.start(client_id="expired-session", before_score=1), after_score=5)
        conn = db.connect()
        conn.execute("UPDATE practice_sessions SET local_date = ? WHERE id = ?", ((db.now_utc() - timedelta(days=30)).date().isoformat(), old["id"]))
        conn.commit(); conn.close()
        summary = self.request("GET", "/api/practice-summary").payload
        self.assertEqual((summary["completed"], summary["paired"], summary["stopped"]), (2, 1, 1))
        self.assertEqual((summary["before_average"], summary["after_average"]), (5, 3))
        self.assertEqual(summary["feedback"], {"better": 1, "same": 0, "worse": 0})

    def test_cursor_pagination_and_stopped_session(self):
        ids = [self.start(client_id=f'pagination-{i}')["id"] for i in range(5)]
        first = self.request("GET", "/api/practice-sessions", query={"limit": ["2"]}).payload
        second = self.request("GET", "/api/practice-sessions", query={"limit": ["2"], "cursor": [str(first["next_cursor"])]}).payload
        self.assertEqual([x["id"] for x in first["items"] + second["items"]], list(reversed(ids))[0:4])
        self.assertEqual(first["total"], 5)
        session = self.request("GET", f'/api/practice-sessions/{ids[0]}').payload
        stopped = self.advance(session, status="stopped", note="enough for now")
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["step_index"], 0)


if __name__ == "__main__":
    unittest.main()
