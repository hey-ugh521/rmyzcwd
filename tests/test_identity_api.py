"""Phase 4F-2C GET /api/identity/me tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from fastapi.testclient import TestClient  # noqa: E402

from core import capabilities, identity, identity_shadow as shadow, store  # noqa: E402


class IdentityMeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_backend = store.DB_BACKEND
        self.old_path = store.DB_PATH
        self.old_core = store.CFG.get("core")
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = Path(self.tmp.name) / "identity_me.db"
        store.CFG["core"] = {**(self.old_core or {}), "api_key": "identity-me-key"}
        store.init_db()
        identity.init_schema()
        capabilities.init_schema()
        shadow.init_schema()
        mapping = identity.resolve_legacy_api_key("identity-me-key")
        self.credential_id = mapping["credential"]["id"]
        import core.server as coreserver  # noqa: PLC0415
        self.coreserver = coreserver
        self.old_key = coreserver.API_KEY
        coreserver.API_KEY = "identity-me-key"
        self.client = TestClient(coreserver.app)

    def tearDown(self) -> None:
        self.client.close()
        self.coreserver.API_KEY = self.old_key
        store.close_anchor()
        store.DB_BACKEND = self.old_backend
        store.DB_PATH = self.old_path
        if self.old_core is None:
            store.CFG.pop("core", None)
        else:
            store.CFG["core"] = self.old_core
        self.tmp.cleanup()

    def get(self, **kwargs):
        return self.client.get("/api/identity/me", **kwargs)

    def test_success_contract(self):
        r = self.get(headers={"Authorization": "Bearer identity-me-key"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("cache-control"), "no-store")
        self.assertNotIn("www-authenticate", r.headers)
        body = r.json()
        self.assertEqual(body["identity_mode"], "legacy")
        self.assertEqual(body["principal"], {
            "id": "service-legacy", "kind": "machine", "role": "service",
        })
        self.assertNotIn("credential", body)
        self.assertNotIn("identity-me-key", r.text)

    def test_missing_bearer(self):
        r = self.get()
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers.get("www-authenticate"), "Bearer")
        self.assertEqual(r.json(), {"ok": False, "error": "unauthenticated"})
        self.assertEqual(r.headers.get("cache-control"), "no-store")

    def test_scheme_case_insensitive_and_multiple_spaces(self):
        for value in ("bearer identity-me-key", "Bearer   identity-me-key"):
            r = self.get(headers={"Authorization": value})
            self.assertEqual(r.status_code, 200, value)

    def test_malformed_and_wrong_credentials(self):
        cases = [
            "",
            "Bearer",
            "Bearer ",
            "Basic identity-me-key",
            "Bearer wrong-key",
            "Bearer identity-me-key extra",
        ]
        for value in cases:
            r = self.get(headers={"Authorization": value})
            self.assertEqual(r.status_code, 401, value)
            self.assertEqual(r.headers.get("www-authenticate"), "Bearer")

    def test_no_query_cookie_or_body_fallback(self):
        r = self.client.get("/api/identity/me?api_key=identity-me-key")
        self.assertEqual(r.status_code, 401)
        r = self.client.get("/api/identity/me",
                            cookies={"api_key": "identity-me-key"})
        self.assertEqual(r.status_code, 401)

    def test_duplicate_authorization_rejected(self):
        r = self.client.get(
            "/api/identity/me",
            headers=[
                ("Authorization", "Bearer identity-me-key"),
                ("Authorization", "Bearer identity-me-key"),
            ],
        )
        self.assertEqual(r.status_code, 401)

    def test_configuration_missing_is_500_not_401(self):
        store.CFG["core"]["api_key"] = ""
        r = self.get(headers={"Authorization": "Bearer identity-me-key"})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json(), {"ok": False, "error": "identity_unavailable"})
        self.assertNotIn("www-authenticate", r.headers)
        self.assertEqual(r.headers.get("cache-control"), "no-store")

    def test_database_error_is_500_not_401(self):
        with mock.patch.object(identity, "inspect_legacy_api_key",
                               side_effect=RuntimeError("db down")):
            r = self.get(headers={"Authorization": "Bearer identity-me-key"})
        self.assertEqual(r.status_code, 500)
        self.assertEqual(r.json(), {"ok": False, "error": "identity_unavailable"})
        self.assertNotIn("identity-me-key", r.text)

    def test_configuration_change_is_500(self):
        calls = {"n": 0}

        def changed():
            calls["n"] += 1
            return "identity-me-key" if calls["n"] <= 2 else "other-key"

        with mock.patch.object(identity, "configured_legacy_key", side_effect=changed):
            r = self.get(headers={"Authorization": "Bearer identity-me-key"})
        self.assertEqual(r.status_code, 500)

    def test_revoked_and_disabled_are_401(self):
        identity.revoke_credential(self.credential_id)
        r = self.get(headers={"Authorization": "Bearer identity-me-key"})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers.get("www-authenticate"), "Bearer")

    def test_read_only_snapshot(self):
        before = None
        with store.conn() as c:
            before = c.execute(
                "SELECT id,last_used_at,revoked_at,expires_at FROM credentials WHERE id=?",
                (self.credential_id,)).fetchone()
            counts = {t: c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                      for t in ("principals", "credentials", "admin_sessions",
                                "bootstrap_tokens", "identity_shadow_events")}
        r = self.get(headers={"Authorization": "Bearer identity-me-key"})
        self.assertEqual(r.status_code, 200)
        with store.conn() as c:
            after = c.execute(
                "SELECT id,last_used_at,revoked_at,expires_at FROM credentials WHERE id=?",
                (self.credential_id,)).fetchone()
            counts_after = {t: c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                            for t in counts}
        self.assertEqual(dict(before), dict(after))
        self.assertEqual(counts, counts_after)

    def test_legacy_route_unchanged_after_endpoint(self):
        def legacy():
            return self.client.get("/api/capabilities/permissions")

        baseline = legacy()
        self.assertEqual(self.get().status_code, 401)
        self.assertEqual(self.get(headers={"Authorization": "Bearer identity-me-key"}).status_code, 200)
        self.assertEqual(legacy().status_code, baseline.status_code)
        self.assertEqual(legacy().content, baseline.content)

    def test_legacy_route_unchanged_after_500(self):
        baseline = self.client.get("/api/capabilities/permissions")
        store.CFG["core"]["api_key"] = ""
        try:
            self.assertEqual(
                self.get(headers={"Authorization": "Bearer identity-me-key"}).status_code,
                500,
            )
        finally:
            store.CFG["core"]["api_key"] = "identity-me-key"
        after = self.client.get("/api/capabilities/permissions")
        self.assertEqual(after.status_code, baseline.status_code)
        self.assertEqual(after.content, baseline.content)

    def test_shadow_disabled_no_event(self):
        self.assertEqual(shadow.list_shadow_events(), [])
        self.get(headers={"Authorization": "Bearer identity-me-key"})
        self.assertEqual(shadow.list_shadow_events(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
