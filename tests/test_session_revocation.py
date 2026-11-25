"""Server-side session revocation, the 24 hour TTL and the trimmed public health payload."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from warp3c import auth
from warp3c.httpapi import dispatch
from warp3c.operator import HEALTH


def _headers(token=None, bearer=None):
    headers = {}
    if token is not None:
        headers["Cookie"] = f"warp_session={token}"
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


class SessionRevocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.operator_token = secrets.token_urlsafe(32)
        patcher = patch.dict(os.environ, {
            "WARP_SESSION_SECRET": secrets.token_urlsafe(32),
            "WARP_OPERATOR_TOKEN": self.operator_token,
            "WARP_DB_PATH": os.path.join(self.tmp.name, "warp.db"),
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        auth._reset_state()
        self.addCleanup(auth._reset_state)
        self.store = Path(self.tmp.name) / auth.REVOKED_FILE

    def authenticated(self, headers):
        status, _h, body = dispatch("GET", "/api/session", headers, b"")
        self.assertEqual(status, 200)
        return json.loads(body)["authenticated"]

    def entries(self):
        return json.loads(self.store.read_text())["revoked"]

    def test_token_is_valid_before_logout_and_invalid_after(self):
        token = auth.issue_session()
        self.assertTrue(auth.session_valid(token))
        self.assertTrue(self.authenticated(_headers(token)))
        status, headers, body = dispatch("POST", "/api/logout", _headers(token), b"")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True})
        self.assertTrue(any(name == "Set-Cookie" and "Max-Age=0" in value for name, value in headers))
        self.assertFalse(auth.session_valid(token))
        self.assertFalse(self.authenticated(_headers(token)))
        self.assertEqual(dispatch("GET", "/api/status", _headers(token), b"")[0], 401)

    def test_revocation_survives_a_restart(self):
        token = auth.issue_session()
        dispatch("POST", "/api/logout", _headers(token), b"")
        auth._reset_state()
        self.assertTrue(self.store.is_file())
        self.assertFalse(auth.session_valid(token))
        self.assertFalse(self.authenticated(_headers(token)))

    def test_store_holds_hashes_not_tokens(self):
        token = auth.issue_session()
        dispatch("POST", "/api/logout", _headers(token), b"")
        raw = self.store.read_text()
        self.assertNotIn(token, raw)
        self.assertNotIn(token.split(".")[1], raw)
        self.assertEqual(len(self.entries()), 1)

    def test_other_sessions_stay_valid(self):
        first, second = auth.issue_session(), auth.issue_session()
        dispatch("POST", "/api/logout", _headers(first), b"")
        self.assertFalse(auth.session_valid(first))
        self.assertTrue(auth.session_valid(second))
        auth._reset_state()
        self.assertTrue(auth.session_valid(second))

    def test_forged_malformed_or_expired_tokens_add_no_entries(self):
        valid = auth.issue_session()
        expires, nonce, _sig = valid.split(".")
        forged = f"{expires}.{nonce}.{'0' * 64}"
        past = int(time.time()) - 10
        body = f"{past}.{nonce}"
        import hashlib
        import hmac
        signature = hmac.new(auth._secret(), body.encode(), hashlib.sha256).hexdigest()
        expired = f"{body}.{signature}"
        for token in (forged, expired, "garbage", "a.b", "", None):
            headers = _headers(token) if token is not None else {}
            self.assertEqual(dispatch("POST", "/api/logout", headers, b"")[0], 200)
        self.assertFalse(self.store.exists())
        self.assertTrue(auth.session_valid(valid))
        self.assertEqual(dispatch("POST", "/api/logout", {}, b"")[0], 200)

    def test_logout_with_a_valid_token_only_revokes_that_token(self):
        valid = auth.issue_session()
        other = auth.issue_session()
        dispatch("POST", "/api/logout", _headers(other), b"")
        self.assertEqual(len(self.entries()), 1)
        self.assertTrue(auth.session_valid(valid))

    def test_corrupt_store_fails_closed_but_operator_bearer_still_works(self):
        token = auth.issue_session()
        self.assertTrue(auth.session_valid(token))
        for garbage in ("not json", "[]", '{"version": 1, "revoked": []}', '{"version": 2, "revoked": {}}',
                        '{"version": 1, "revoked": {"short": 1}}'):
            self.store.write_text(garbage)
            auth._reset_state()
            self.assertFalse(auth.session_valid(token), garbage)
            self.assertFalse(self.authenticated(_headers(token)), garbage)
            self.assertEqual(dispatch("GET", "/api/status", _headers(token), b"")[0], 401)
            self.assertTrue(self.authenticated(_headers(bearer=self.operator_token)))
            self.assertTrue(self.authenticated(_headers(token, bearer=self.operator_token)))
            self.assertEqual(self.store.read_text(), garbage, "a corrupt store must not be overwritten")
            dispatch("POST", "/api/logout", _headers(token), b"")
            self.assertEqual(self.store.read_text(), garbage)

    def test_unreadable_store_fails_closed(self):
        token = auth.issue_session()
        self.store.mkdir()
        auth._reset_state()
        self.assertFalse(auth.session_valid(token))
        self.assertTrue(self.authenticated(_headers(bearer=self.operator_token)))

    def test_expired_entries_are_pruned_on_write(self):
        stale = "a" * 64
        self.store.write_text(json.dumps({"version": 1, "revoked": {stale: int(time.time()) - 5}}))
        auth._reset_state()
        token = auth.issue_session()
        dispatch("POST", "/api/logout", _headers(token), b"")
        entries = self.entries()
        self.assertNotIn(stale, entries)
        self.assertEqual(len(entries), 1)

    def test_store_is_bounded(self):
        far = int(time.time()) + 3600
        seed = {f"{index:064x}": far + index for index in range(auth.MAX_REVOKED)}
        self.store.write_text(json.dumps({"version": 1, "revoked": seed}))
        auth._reset_state()
        token = auth.issue_session()
        dispatch("POST", "/api/logout", _headers(token), b"")
        self.assertLessEqual(len(self.entries()), auth.MAX_REVOKED)
        self.assertFalse(auth.session_valid(token))

    def test_write_is_atomic_and_leaves_no_temp_file(self):
        dispatch("POST", "/api/logout", _headers(auth.issue_session()), b"")
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), [auth.REVOKED_FILE])

    def test_failed_write_still_revokes_in_this_process(self):
        token = auth.issue_session()
        with patch.object(auth, "_write", side_effect=OSError("disk full")):
            dispatch("POST", "/api/logout", _headers(token), b"")
        self.assertFalse(auth.session_valid(token))

    def test_store_lives_next_to_the_database(self):
        self.assertEqual(auth.revocation_path(), Path(self.tmp.name) / "sessions_revoked.json")

    def test_session_lasts_24_hours(self):
        self.assertEqual(auth.MAX_AGE, 86400)
        before = int(time.time())
        token = auth.issue_session()
        expires = int(token.split(".")[0])
        self.assertTrue(before + 86400 <= expires <= int(time.time()) + 86400)
        self.assertIn("Max-Age=86400", auth.cookie_header(token))
        self.assertIn("Max-Age=0", auth.cookie_header("", clear=True))


class PublicHealthTests(unittest.TestCase):
    def test_public_health_hides_mode_and_halt_path(self):
        values = {"mode": "observe", "halt_path": "/data/HALT", "execution_permitted": False,
                  "alive": True, "dependencies_healthy": True, "last_cycle_ok": True}
        with patch.dict(HEALTH, values):
            for path in ("/health", "/live", "/ready"):
                status, _h, body = dispatch("GET", path, {}, b"")
                payload = json.loads(body)
                self.assertEqual(status, 200, path)
                self.assertNotIn("mode", payload)
                self.assertNotIn("halt_path", payload)
                self.assertIs(payload["execution_permitted"], False)
                self.assertTrue(payload["alive"])
            self.assertEqual(HEALTH["halt_path"], "/data/HALT")
            self.assertEqual(HEALTH["mode"], "observe")

    def test_ready_keeps_its_status_codes(self):
        with patch.dict(HEALTH, {"dependencies_healthy": False, "last_cycle_ok": False}):
            self.assertEqual(dispatch("GET", "/ready", {}, b"")[0], 503)
            self.assertEqual(dispatch("GET", "/health", {}, b"")[0], 200)

    def test_authenticated_status_still_reports_mode(self):
        from warp3c.operator import SNAPSHOT

        token = secrets.token_urlsafe(32)
        with patch.dict(os.environ, {"WARP_OPERATOR_TOKEN": token}), \
                patch.dict(SNAPSHOT, {"/api/status": {"mode": "observe", "halt": False}}), \
                patch.dict(HEALTH, {"halt_path": ""}):
            status, _h, body = dispatch("GET", "/api/status", {"Authorization": f"Bearer {token}"}, b"")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["mode"], "observe")


if __name__ == "__main__":
    unittest.main()
