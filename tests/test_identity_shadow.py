"""Phase 4F-2B-1 shadow event data model and retention tests."""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from core import identity_shadow as shadow, store  # noqa: E402


class IdentityShadowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_backend = store.DB_BACKEND
        self.old_path = store.DB_PATH
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = Path(self.tmp.name) / "shadow.db"
        store.init_db()
        shadow.init_schema()

    def tearDown(self) -> None:
        store.close_anchor()
        store.DB_BACKEND = self.old_backend
        store.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_schema_idempotent_and_no_sensitive_columns(self):
        shadow.init_schema()
        with store.conn() as c:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(identity_shadow_events)")}
        self.assertIn("request_id", cols)
        self.assertIn("policy_version", cols)
        for forbidden in ("authorization", "token", "secret", "headers"):
            self.assertNotIn(forbidden, cols)

    def test_record_and_read_event(self):
        event = shadow.record_shadow_event(
            request_id="req-1", method="GET", path="/api/registry/character",
            route_id="registry.read", legacy_authenticated=True, legacy_actor="api-key",
            identity_state="resolved", evaluation_state="evaluated",
            policy_domain="route", policy_result="allow", policy_version="abc123",
        )
        self.assertTrue(event["id"].startswith("shadow_"))
        self.assertEqual(event["legacy_authenticated"], 1)
        rows = shadow.list_shadow_events(request_id="req-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["policy_version"], "abc123")

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            shadow.record_shadow_event(
                request_id="req-1", authorization="Bearer secret"
            )
        with self.assertRaises(ValueError):
            shadow.record_shadow_event(request_id="req-1", raw_api_key="secret")

    def test_prune_by_age_and_audit_table_is_untouched(self):
        old = shadow.record_shadow_event(request_id="old", ts=time.time() - 31 * 86400)
        fresh = shadow.record_shadow_event(request_id="fresh")
        store.audit("test", "shadow.test", {"x": 1})
        deleted = shadow.prune_shadow_events(max_age_days=30, max_rows=10000, batch_size=10)
        self.assertEqual(deleted, 1)
        self.assertEqual([r["id"] for r in shadow.list_shadow_events()], [fresh["id"]])
        events = shadow.list_shadow_events()
        self.assertNotIn(old["id"], [r["id"] for r in events])
        with store.conn() as c:
            n = c.execute("SELECT COUNT(*) AS n FROM audit WHERE action='shadow.test'").fetchone()["n"]
        self.assertEqual(n, 1)

    def test_prune_max_rows_is_batched(self):
        base = time.time() - 100
        ids = [shadow.record_shadow_event(request_id=f"r{i}", ts=base + i)["id"]
               for i in range(6)]
        first = shadow.prune_shadow_events(max_age_days=999, max_rows=3, batch_size=2)
        self.assertEqual(first, 2)
        second = shadow.prune_shadow_events(max_age_days=999, max_rows=3, batch_size=2)
        self.assertGreaterEqual(second, 1)
        with store.conn() as c:
            remaining = c.execute("SELECT COUNT(*) AS n FROM identity_shadow_events").fetchone()["n"]
        self.assertLessEqual(remaining, 3)

    def test_prune_batch_size_is_capped(self):
        base = time.time() - 31 * 86400
        for i in range(8):
            shadow.record_shadow_event(request_id=f"old{i}", ts=base + i)
        deleted = shadow.prune_shadow_events(max_age_days=30, batch_size=1000)
        self.assertLessEqual(deleted, 500)
        self.assertEqual(deleted, 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
