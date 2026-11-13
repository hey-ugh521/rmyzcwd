"""Phase 4F-2B-3 middleware shadow tests."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from fastapi.testclient import TestClient  # noqa: E402

from core import capabilities, identity, identity_shadow as shadow, store  # noqa: E402


class ShadowMiddlewareTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_backend = store.DB_BACKEND
        cls.old_path = store.DB_PATH
        cls.old_core = store.CFG.get("core")
        cls.old_identity = store.CFG.get("identity")
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = Path(cls.tmp.name) / "shadow_mw.db"
        store.CFG["core"] = {**(cls.old_core or {}), "api_key": "shadow-mw-key"}
        store.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        store.init_db()
        identity.init_schema()
        capabilities.init_schema()
        shadow.init_schema()
        identity.resolve_legacy_api_key("shadow-mw-key")
        import core.server as coreserver  # noqa: PLC0415
        cls.coreserver = coreserver
        cls.old_key = coreserver.API_KEY
        coreserver.API_KEY = "shadow-mw-key"
        cls.client = TestClient(coreserver.app)
        cls.remote = TestClient(coreserver.app, client=("10.0.0.9", 50000))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()
        cls.remote.close()
        cls.coreserver.API_KEY = cls.old_key
        store.close_anchor()
        store.DB_BACKEND = cls.old_backend
        store.DB_PATH = cls.old_path
        if cls.old_core is None:
            store.CFG.pop("core", None)
        else:
            store.CFG["core"] = cls.old_core
        if cls.old_identity is None:
            store.CFG.pop("identity", None)
        else:
            store.CFG["identity"] = cls.old_identity
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self._old_identity = store.CFG.get("identity")
        store.CFG["identity"] = {"shadow": {"enabled": False}}
        self.client.cookies.clear()
        with store.conn() as c:
            c.execute("DELETE FROM identity_shadow_events")
            c.commit()

    def tearDown(self) -> None:
        if self._old_identity is None:
            store.CFG.pop("identity", None)
        else:
            store.CFG["identity"] = self._old_identity

    def enable(self) -> None:
        store.CFG["identity"] = {"shadow": {"enabled": True}}

    def events(self):
        return shadow.list_shadow_events(limit=50)

    def baseline(self):
        return self.remote.get("/api/capabilities/permissions",
                               headers={"Authorization": "Bearer shadow-mw-key"})

    def assert_same_response(self, expected, actual):
        self.assertEqual(expected.status_code, actual.status_code)
        self.assertEqual(expected.content, actual.content)
        for key in ("content-type", "content-length"):
            self.assertEqual(expected.headers.get(key), actual.headers.get(key))

    def test_shadow_disabled_writes_nothing(self):
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.events(), [])

    def test_legacy_denied_is_observed_but_does_not_map_identity(self):
        before = len(identity.list_principals())
        base = self.remote.post("/api/capabilities/permissions",
                                headers={"Authorization": "Bearer wrong-key"}, json={})
        self.enable()
        r = self.remote.post("/api/capabilities/permissions",
                             headers={"Authorization": "Bearer wrong-key"}, json={})
        self.assert_same_response(base, r)
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["identity_state"], "legacy_denied")
        self.assertEqual(events[0]["evaluation_state"], "insufficient_context")
        self.assertEqual(len(identity.list_principals()), before)

    def test_authenticated_management_route_is_evaluated(self):
        self.enable()
        r = self.remote.get("/api/capabilities/permissions",
                            headers={"Authorization": "Bearer shadow-mw-key"})
        self.assertEqual(r.status_code, 200)
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["evaluation_state"], "evaluated")
        self.assertEqual(events[0]["policy_domain"], "route")
        self.assertEqual(events[0]["role"], "service")
        self.assertEqual(events[0]["policy_result"], "deny")

    def test_response_is_identical_with_shadow_off_and_on(self):
        off = self.remote.get("/api/capabilities/permissions",
                             headers={"Authorization": "Bearer shadow-mw-key"})
        self.enable()
        on = self.remote.get("/api/capabilities/permissions",
                             headers={"Authorization": "Bearer shadow-mw-key"})
        self.assertEqual(off.status_code, on.status_code)
        self.assertEqual(off.content, on.content)
        for key in ("content-type", "content-length"):
            self.assertEqual(off.headers.get(key), on.headers.get(key))
        self.assertEqual([e["evaluation_state"] for e in self.events()],
                         ["evaluated"])

    def test_shadow_exception_does_not_change_response(self):
        base = self.baseline()
        self.enable()
        with mock.patch.object(shadow, "evaluate_shadow", side_effect=RuntimeError("boom")):
            r = self.remote.get("/api/capabilities/permissions",
                                headers={"Authorization": "Bearer shadow-mw-key"})
        self.assert_same_response(base, r)

    def test_shadow_write_failure_does_not_change_response(self):
        base = self.baseline()
        self.enable()
        with mock.patch.object(shadow, "record_shadow_event", side_effect=RuntimeError("write")):
            r = self.remote.get("/api/capabilities/permissions",
                                headers={"Authorization": "Bearer shadow-mw-key"})
        self.assert_same_response(base, r)

    def test_shadow_timeout_does_not_change_response(self):
        base = self.baseline()
        self.enable()

        def slow(*args, **kwargs):
            time.sleep(0.6)

        with mock.patch.object(self.coreserver, "_shadow_observe", side_effect=slow):
            started = time.monotonic()
            r = self.remote.get("/api/capabilities/permissions",
                                headers={"Authorization": "Bearer shadow-mw-key"})
            elapsed = time.monotonic() - started
        self.assert_same_response(base, r)
        self.assertLess(elapsed, 0.55)

    def test_shadow_deny_does_not_block_legacy_allowed_request(self):
        base = self.baseline()
        self.enable()
        r = self.remote.get("/api/capabilities/permissions",
                            headers={"Authorization": "Bearer shadow-mw-key"})
        self.assert_same_response(base, r)
        self.assertTrue(any(e["policy_result"] == "deny" for e in self.events()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
