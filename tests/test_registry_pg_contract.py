"""PostgreSQL contract test for Phase 0 Registry.

Run only when MOBIUS_TEST_PG_DSN points at a disposable database:
  set MOBIUS_TEST_PG_DSN=postgresql://.../mobius_registry_test
  python tests/test_registry_pg_contract.py
"""

from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import registry, store  # noqa: E402

DSN = os.environ.get("MOBIUS_TEST_PG_DSN", "").strip()


@unittest.skipUnless(DSN, "MOBIUS_TEST_PG_DSN not set")
class RegistryPgContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_backend = store.DB_BACKEND
        self.old_dsn = store.PG_DSN
        store.DB_BACKEND = "pg"
        store.PG_DSN = DSN
        registry.init_schema()
        # isolate each test; revisions are immutable, so drop tables instead of DELETE
        with store.conn() as c:
            for table in ("registry_references", "registry_idempotency", "registry_audit",
                          "registry_revisions", "registry_resources"):
                c.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        registry.init_schema()
        self.svc = registry.RegistryService()

    def tearDown(self) -> None:
        store.DB_BACKEND = self.old_backend
        store.PG_DSN = self.old_dsn

    def create(self):
        return self.svc.create("character", "bronya", {"name": "v1"})

    def test_lifecycle_and_immutability(self):
        self.create()
        self.svc.update("character", "bronya", {"name": "v2"}, expected_revision=1)
        self.svc.publish("character", "bronya", 2,
                         expected_current_revision=2,
                         expected_published_revision=None)
        self.assertEqual(self.svc.resolve("character", "bronya").revision, 2)
        self.svc.update("character", "bronya", {"name": "v3"}, expected_revision=2)
        self.assertEqual(self.svc.resolve("character", "bronya").snapshot["name"], "v2")
        self.svc.rollback("character", "bronya", 1,
                          expected_current_revision=3,
                          expected_published_revision=2)
        self.assertEqual(self.svc.resolve("character", "bronya").revision, 1)
        self.assertEqual(self.svc.get("character", "bronya", include_snapshot=True)["current"]["payload"]["name"], "v3")

        with self.assertRaises(Exception):
            c = store.conn()
            try:
                c.execute("UPDATE registry_revisions SET snapshot='{}' "
                          "WHERE type='character' AND id='bronya' AND revision=1")
                c.commit()
            finally:
                c.close()

        with self.assertRaises(Exception):
            c = store.conn()
            try:
                c.execute("UPDATE registry_resources SET published_revision=999 "
                          "WHERE type='character' AND id='bronya'")
                c.commit()
            finally:
                c.close()

    def test_reference_delete(self):
        self.svc.create("voice_profile", "voice", {"name": "Voice"})
        self.svc.create("character", "char", {"name": "Char"})
        self.svc.add_reference("character", "char", "voice_profile", "voice")
        with self.assertRaises(registry.RegistryError) as cm:
            self.svc.delete("voice_profile", "voice", expected_revision=1)
        self.assertEqual(cm.exception.code, "reference_exists")
        self.svc.remove_reference("character", "char", "voice_profile", "voice")
        self.svc.delete("voice_profile", "voice", expected_revision=1)

    def test_two_connections_concurrent_update(self):
        self.create()
        barrier = threading.Barrier(2)
        results: list[str] = []

        def work(name: str) -> None:
            try:
                barrier.wait(timeout=10)
                self.svc.update("character", "bronya", {"name": name}, expected_revision=1)
                results.append("ok")
            except registry.RegistryError as exc:
                results.append(exc.code)

        threads = [threading.Thread(target=work, args=(f"t{i}",)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(sorted(results), ["ok", "revision_conflict"])

    def test_two_connections_concurrent_publish(self):
        self.create()
        self.svc.update("character", "bronya", {"name": "v2"}, expected_revision=1)
        self.svc.update("character", "bronya", {"name": "v3"}, expected_revision=2)
        self.svc.publish("character", "bronya", 1,
                         expected_current_revision=3,
                         expected_published_revision=None)
        barrier = threading.Barrier(2)
        results: list[str] = []

        def work(revision: int) -> None:
            try:
                barrier.wait(timeout=10)
                self.svc.publish("character", "bronya", revision,
                                 expected_current_revision=3,
                                 expected_published_revision=1)
                results.append("ok")
            except registry.RegistryError as exc:
                results.append(exc.code)

        threads = [threading.Thread(target=work, args=(r,)) for r in (2, 3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(sorted(results), ["ok", "publish_conflict"])

    def test_reference_create_delete_race(self):
        self.svc.create("voice_profile", "voice", {"name": "Voice"})
        self.svc.create("character", "char", {"name": "Char"})
        barrier = threading.Barrier(2)
        results: list[str] = []

        def add_ref() -> None:
            try:
                barrier.wait(timeout=10)
                self.svc.add_reference("character", "char", "voice_profile", "voice")
                results.append("ref_ok")
            except registry.RegistryError as exc:
                results.append(exc.code)

        def delete_target() -> None:
            try:
                barrier.wait(timeout=10)
                self.svc.delete("voice_profile", "voice", expected_revision=1)
                results.append("delete_ok")
            except registry.RegistryError as exc:
                results.append(exc.code)

        threads = [threading.Thread(target=add_ref), threading.Thread(target=delete_target)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(len([r for r in results if r in {"ref_ok", "delete_ok"}]), 1, str(results))

    def test_duplicate_reference_rejected(self):
        self.svc.create("voice_profile", "voice", {"name": "Voice"})
        self.svc.create("character", "char", {"name": "Char"})
        self.svc.add_reference("character", "char", "voice_profile", "voice")
        with self.assertRaises(registry.RegistryError) as cm:
            self.svc.add_reference("character", "char", "voice_profile", "voice")
        self.assertEqual(cm.exception.code, "reference_exists")

    def test_publish_failure_rolls_back_pointer_and_revision_state(self):
        self.create()
        self.svc.publish("character", "bronya", 1,
                         expected_current_revision=1,
                         expected_published_revision=None)
        self.svc.update("character", "bronya", {"name": "v2"}, expected_revision=1)
        before = self.svc.get("character", "bronya", include_snapshot=True)
        with mock.patch.object(registry, "_audit", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self.svc.publish("character", "bronya", 2,
                                 expected_current_revision=2,
                                 expected_published_revision=1)
        after = self.svc.get("character", "bronya", include_snapshot=True)
        self.assertEqual(after["published_revision"], before["published_revision"])
        revs = self.svc.revisions("character", "bronya")
        self.assertEqual(sum(1 for r in revs if r["state"] == "published"), 1)
        self.assertEqual(next(r["state"] for r in revs if r["revision"] == 2), "draft")

    def test_reference_failure_rolls_back_insert(self):
        self.svc.create("voice_profile", "voice2", {"name": "Voice 2"})
        self.svc.create("character", "char2", {"name": "Char 2"})
        with mock.patch.object(registry, "_audit", side_effect=RuntimeError("audit failed")):
            with self.assertRaises(RuntimeError):
                self.svc.add_reference("character", "char2", "voice_profile", "voice2")
        # If the reference insert had committed, delete would be blocked.
        self.svc.delete("voice_profile", "voice2", expected_revision=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
