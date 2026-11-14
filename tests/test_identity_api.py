"""Phase 4F-2C GET /api/identity/me tests."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import capabilities, identity, identity_api, identity_shadow as shadow, store  # noqa: E402
from types import SimpleNamespace  # noqa: E402


class IdentityMeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_backend = store.DB_BACKEND
        self.old_path = store.DB_PATH
        self.old_core = store.CFG.get("core")
        self.old_identity = store.CFG.get("identity")
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = Path(self.tmp.name) / "identity_me.db"
        store.CFG["core"] = {**(self.old_core or {}), "api_key": "identity-me-key"}
        store.CFG.pop("identity", None)
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
        self.restore_identity()
        store.DB_BACKEND = self.old_backend
        store.DB_PATH = self.old_path
        if self.old_core is None:
            store.CFG.pop("core", None)
        else:
            store.CFG["core"] = self.old_core
        self.tmp.cleanup()

    def restore_identity(self) -> None:
        if self.old_identity is None:
            store.CFG.pop("identity", None)
        else:
            store.CFG["identity"] = self.old_identity

    def wait_for_shadow_event(self):
        deadline = time.monotonic() + 0.5
        events = []
        while time.monotonic() < deadline:
            events = shadow.list_shadow_events()
            if events:
                return events
            time.sleep(0.02)
        return events

    def wait_for_call(self, mocked, timeout: float = 0.5) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and mocked.call_count == 0:
            time.sleep(0.02)

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

    def test_trailing_newline_rejected(self):
        for raw in (b"Bearer identity-me-key\n", b"Bearer identity-me-key\r\n"):
            request = SimpleNamespace(scope={"headers": [(b"authorization", raw)]})
            self.assertIsNone(identity_api._parse_bearer(request), raw)

    def test_slow_db_does_not_block_event_loop(self):
        original = identity.inspect_legacy_api_key

        def slow(*args, **kwargs):
            time.sleep(0.8)
            return original(*args, **kwargs)

        async def scenario():
            transport = httpx.ASGITransport(app=self.coreserver.app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://testserver") as client:
                slow_task = asyncio.create_task(client.get(
                    "/api/identity/me",
                    headers={"Authorization": "Bearer identity-me-key"},
                ))
                await asyncio.sleep(0.05)
                started = time.monotonic()
                fast = await client.get(
                    "/api/capabilities/permissions",
                    headers={"Authorization": "Bearer identity-me-key"},
                )
                elapsed = time.monotonic() - started
                slow_response = await slow_task
                return slow_response.status_code, fast.status_code, elapsed

        with mock.patch.object(identity, "inspect_legacy_api_key", side_effect=slow):
            slow_status, fast_status, elapsed = asyncio.run(scenario())
        self.assertEqual(slow_status, 200)
        self.assertEqual(fast_status, 200)
        self.assertLess(elapsed, 0.4)

    def test_timeout_returns_500(self):
        original = identity.inspect_legacy_api_key

        def slow(*args, **kwargs):
            time.sleep(0.3)
            return original(*args, **kwargs)

        with mock.patch.object(identity_api, "IDENTITY_ME_TIMEOUT_SECONDS", 0.05), \
                mock.patch.object(identity, "inspect_legacy_api_key", side_effect=slow):
            started = time.monotonic()
            r = self.get(headers={"Authorization": "Bearer identity-me-key"})
            elapsed = time.monotonic() - started
            time.sleep(0.35)
        self.assertEqual(r.status_code, 500)
        self.assertLess(elapsed, 0.2)
        self.assertNotIn("www-authenticate", r.headers)
        self.assertEqual(r.headers.get("cache-control"), "no-store")

    def test_timeout_path_is_bounded(self):
        original = identity.inspect_legacy_api_key
        started = 0
        lock = threading.Lock()

        def slow(*args, **kwargs):
            nonlocal started
            with lock:
                started += 1
            time.sleep(0.4)
            return original(*args, **kwargs)

        async def scenario():
            transport = httpx.ASGITransport(app=self.coreserver.app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://testserver") as client:
                tasks = []
                for _ in range(6):
                    tasks.append(asyncio.create_task(client.get(
                        "/api/identity/me",
                        headers={"Authorization": "Bearer identity-me-key"},
                    )))
                    await asyncio.sleep(0.01)
                responses = await asyncio.gather(*tasks)
                return [r.status_code for r in responses]

        with mock.patch.object(identity_api, "IDENTITY_ME_MAX_CONCURRENCY", 1), \
                mock.patch.object(identity_api, "IDENTITY_ME_TIMEOUT_SECONDS", 0.05), \
                mock.patch.object(identity, "inspect_legacy_api_key", side_effect=slow):
            statuses = asyncio.run(scenario())
            time.sleep(0.45)
        self.assertTrue(all(code == 500 for code in statuses), statuses)
        self.assertEqual(started, 1)

    def test_slot_recovers_after_timeouts(self):
        before = identity_api.stats_snapshot()
        original = identity.inspect_legacy_api_key
        calls = 0
        lock = threading.Lock()

        def slow(*args, **kwargs):
            nonlocal calls
            with lock:
                calls += 1
                current = calls
            if current == 1:
                time.sleep(1.0)
            return original(*args, **kwargs)

        async def scenario():
            transport = httpx.ASGITransport(app=self.coreserver.app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://testserver") as client:
                statuses = []
                for _ in range(5):
                    response = await client.get(
                        "/api/identity/me",
                        headers={"Authorization": "Bearer identity-me-key"},
                    )
                    statuses.append(response.status_code)
                    await asyncio.sleep(0.07)
                identity_api.IDENTITY_ME_TIMEOUT_SECONDS = 1.0
                ok = await client.get(
                    "/api/identity/me",
                    headers={"Authorization": "Bearer identity-me-key"},
                )
                return statuses, ok.status_code

        with mock.patch.object(identity_api, "IDENTITY_ME_MAX_CONCURRENCY", 1), \
                mock.patch.object(identity_api, "IDENTITY_ME_TIMEOUT_SECONDS", 0.05), \
                mock.patch.object(identity, "inspect_legacy_api_key", side_effect=slow):
            statuses, ok_status = asyncio.run(scenario())
        after = identity_api.stats_snapshot()
        self.assertTrue(all(code == 500 for code in statuses), statuses)
        self.assertEqual(ok_status, 200)
        self.assertEqual(calls, 2)
        self.assertGreaterEqual(
            after["db_timeouts"] - before["db_timeouts"], 1)
        self.assertGreaterEqual(
            after["slot_timeouts"] - before["slot_timeouts"], 4)
        self.assertGreaterEqual(after["max_active_workers"], 1)
        self.assertEqual(after["active_workers"], 0)

    def test_background_exception_is_counted(self):
        before = identity_api.stats_snapshot()

        def fail(*args, **kwargs):
            time.sleep(0.05)
            raise RuntimeError("background failure")

        async def scenario():
            transport = httpx.ASGITransport(app=self.coreserver.app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://testserver") as client:
                response = await client.get(
                    "/api/identity/me",
                    headers={"Authorization": "Bearer identity-me-key"},
                )
                await asyncio.sleep(0.12)
                return response.status_code

        with mock.patch.object(identity_api, "IDENTITY_ME_TIMEOUT_SECONDS", 0.01), \
                mock.patch.object(identity, "inspect_legacy_api_key", side_effect=fail):
            status = asyncio.run(scenario())
        after = identity_api.stats_snapshot()
        self.assertEqual(status, 500)
        self.assertGreaterEqual(
            after["background_errors"] - before["background_errors"], 1)
    def test_expired_mapping_is_401(self):
        with store.conn() as c:
            c.execute("UPDATE credentials SET expires_at=0 WHERE id=?", (self.credential_id,))
            c.commit()
        self.assertEqual(
            self.get(headers={"Authorization": "Bearer identity-me-key"}).status_code,
            401,
        )

    def test_disabled_principal_is_401(self):
        identity.disable_principal("service-legacy")
        self.assertEqual(
            self.get(headers={"Authorization": "Bearer identity-me-key"}).status_code,
            401,
        )

    def test_missing_mapping_is_401(self):
        with store.conn() as c:
            c.execute("DELETE FROM credentials WHERE id=?", (self.credential_id,))
            c.commit()
        self.assertEqual(
            self.get(headers={"Authorization": "Bearer identity-me-key"}).status_code,
            401,
        )

    def test_invalid_request_id_generates_server_id(self):
        request = SimpleNamespace(scope={"headers": [(b"x-request-id", b"bad id")]})
        self.assertRegex(identity_api._request_id(request), r"^[0-9a-f]{32}$")

    def test_shadow_enabled_does_not_change_endpoint(self):
        store.CFG["identity"] = {"shadow": {"enabled": True}}
        try:
            r = self.get(headers={"Authorization": "Bearer identity-me-key"})
            self.assertEqual(r.status_code, 200)
            events = self.wait_for_shadow_event()
            self.assertGreaterEqual(len(events), 1)
            self.assertEqual(events[0]["path"], "/api/identity/me")
            self.assertEqual(events[0]["evaluation_state"], "not_evaluated")
        finally:
            self.restore_identity()

    def test_shadow_write_failure_does_not_change_endpoint(self):
        store.CFG["identity"] = {"shadow": {"enabled": True}}
        try:
            with mock.patch.object(shadow, "record_shadow_event",
                                   side_effect=RuntimeError("write failed")) as record:
                r = self.get(headers={"Authorization": "Bearer identity-me-key"})
            self.assertEqual(r.status_code, 200)
            self.wait_for_call(record)
            record.assert_called_once()
        finally:
            self.restore_identity()

    def test_shadow_evaluation_failure_does_not_change_endpoint(self):
        store.CFG["identity"] = {"shadow": {"enabled": True}}
        try:
            with mock.patch.object(shadow, "evaluate_shadow",
                                   side_effect=RuntimeError("eval failed")) as evaluate:
                r = self.get(headers={"Authorization": "Bearer identity-me-key"})
            self.assertEqual(r.status_code, 200)
            self.wait_for_call(evaluate)
            evaluate.assert_called_once()
        finally:
            self.restore_identity()

    def test_shadow_failure_does_not_change_401_or_500(self):
        store.CFG["identity"] = {"shadow": {"enabled": True}}
        try:
            with mock.patch.object(shadow, "record_shadow_event",
                                   side_effect=RuntimeError("write failed")) as record:
                denied = self.get(headers={"Authorization": "Bearer wrong-key"})
                self.assertEqual(denied.status_code, 401)
                self.wait_for_call(record)
                self.assertGreaterEqual(record.call_count, 1)

            with mock.patch.object(shadow, "record_shadow_event",
                                   side_effect=RuntimeError("write failed")):
                store.CFG["core"]["api_key"] = ""
                try:
                    unavailable = self.get(
                        headers={"Authorization": "Bearer identity-me-key"})
                finally:
                    store.CFG["core"]["api_key"] = "identity-me-key"
            self.assertEqual(unavailable.status_code, 500)
        finally:
            self.restore_identity()

    def test_identity_concurrency_is_bounded(self):
        original = identity.inspect_legacy_api_key
        lock = threading.Lock()
        active = 0
        peak = 0

        def slow(*args, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                time.sleep(0.15)
                return original(*args, **kwargs)
            finally:
                with lock:
                    active -= 1

        async def scenario():
            transport = httpx.ASGITransport(app=self.coreserver.app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://testserver") as client:
                responses = await asyncio.gather(*[
                    client.get("/api/identity/me",
                               headers={"Authorization": "Bearer identity-me-key"})
                    for _ in range(6)
                ])
                return [r.status_code for r in responses]

        with mock.patch.object(identity_api, "IDENTITY_ME_MAX_CONCURRENCY", 2), \
                mock.patch.object(identity, "inspect_legacy_api_key", side_effect=slow):
            statuses = asyncio.run(scenario())
        self.assertTrue(all(code == 200 for code in statuses))
        self.assertLessEqual(peak, 2)
        self.assertGreaterEqual(peak, 1)

    def test_stats_log_emits_periodic_snapshot(self):
        with mock.patch.object(identity_api,
                               "IDENTITY_STATS_LOG_INTERVAL_SECONDS", 0), \
                mock.patch.object(identity_api.log, "info") as info:
            r = self.get(headers={"Authorization": "Bearer identity-me-key"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any("identity.me stats" in str(call.args[0])
                            for call in info.call_args_list))

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
