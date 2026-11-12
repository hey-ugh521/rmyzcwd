"""Phase 0 Registry lifecycle invariant tests (SQLite)."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import registry, store  # noqa: E402


class RegistryLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_backend = store.DB_BACKEND
        self.old_path = store.DB_PATH
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = Path(self.tmp.name) / "registry.db"
        registry.init_schema()
        self.svc = registry.RegistryService()

    def tearDown(self) -> None:
        store.DB_BACKEND = self.old_backend
        store.DB_PATH = self.old_path
        self.tmp.cleanup()

    def create(self, resource_type="character", resource_id="bronya", payload=None):
        return self.svc.create(resource_type, resource_id, payload or {"name": "Bronya"})

    def test_revision_is_immutable_and_update_creates_new_revision(self):
        self.create(payload={"name": "v1"})
        self.svc.update("character", "bronya", {"name": "v2"}, expected_revision=1)
        got = self.svc.get("character", "bronya", include_snapshot=True)
        self.assertEqual(got["current_revision"], 2)
        self.assertEqual(got["current"]["payload"]["name"], "v2")
        revs = self.svc.revisions("character", "bronya")
        self.assertEqual([r["revision"] for r in revs], [2, 1])
        self.assertEqual(self.svc.get("character", "bronya")["current_revision"], 2)

        with self.assertRaises(sqlite3.DatabaseError):
            c = store.conn()
            try:
                c.execute("UPDATE registry_revisions SET snapshot=? "
                          "WHERE type='character' AND id='bronya' AND revision=1",
                          ('{"name":"hacked"}',))
                c.commit()
            finally:
                c.close()

        # the failed write must not disturb the existing revision
        old = self.svc.get("character", "bronya", include_snapshot=True)
        self.assertEqual(old["current"]["payload"]["name"], "v2")

    def test_published_revision_stays_fixed_while_editing(self):
        self.create(payload={"name": "v1"})
        self.svc.publish("character", "bronya", 1,
                         expected_current_revision=1,
                         expected_published_revision=None)
        self.svc.update("character", "bronya", {"name": "v2"}, expected_revision=1)
        got = self.svc.get("character", "bronya", include_snapshot=True)
        self.assertEqual(got["current_revision"], 2)
        self.assertEqual(got["published_revision"], 1)
        resolved = self.svc.resolve("character", "bronya")
        self.assertEqual(resolved.revision, 1)
        self.assertEqual(resolved.snapshot["name"], "v1")

        self.svc.publish("character", "bronya", 2,
                         expected_current_revision=2,
                         expected_published_revision=1)
        self.assertEqual(self.svc.resolve("character", "bronya").revision, 2)

    def test_rollback_does_not_rewrite_later_revision(self):
        self.create(payload={"name": "v1"})
        self.svc.update("character", "bronya", {"name": "v2"}, expected_revision=1)
        self.svc.publish("character", "bronya", 2,
                         expected_current_revision=2,
                         expected_published_revision=None)
        self.svc.rollback("character", "bronya", 1,
                          expected_current_revision=2,
                          expected_published_revision=2)
        self.assertEqual(self.svc.resolve("character", "bronya").revision, 1)
        later = self.svc.get("character", "bronya", include_snapshot=True)
        self.assertEqual(later["current_revision"], 2)
        self.assertEqual(later["current"]["payload"]["name"], "v2")

    def test_concurrent_edit_has_one_winner(self):
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
            t.join(timeout=10)
        self.assertEqual(sorted(results), ["ok", "revision_conflict"])

    def test_concurrent_publish_has_one_winner(self):
        self.create(payload={"name": "v1"})
        self.svc.update("character", "bronya", {"name": "v2"}, expected_revision=1)
        self.svc.update("character", "bronya", {"name": "v3"}, expected_revision=2)
        self.svc.publish("character", "bronya", 3,
                         expected_current_revision=3,
                         expected_published_revision=None)
        # publish the two older revisions concurrently; both see current=3,
        # published=3; exactly one should win.
        barrier = threading.Barrier(2)
        results: list[str] = []

        def work(revision: int) -> None:
            try:
                barrier.wait(timeout=10)
                self.svc.publish("character", "bronya", revision,
                                 expected_current_revision=3,
                                 expected_published_revision=3)
                results.append("ok")
            except registry.RegistryError as exc:
                results.append(exc.code)

        threads = [threading.Thread(target=work, args=(r,)) for r in (1, 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(sorted(results), ["ok", "publish_conflict"])

    def test_disable_enable_archive(self):
        self.create()
        self.svc.publish("character", "bronya", 1,
                         expected_current_revision=1, expected_published_revision=None)
        self.svc.disable("character", "bronya")
        with self.assertRaises(registry.RegistryError) as cm:
            self.svc.resolve("character", "bronya")
        self.assertEqual(cm.exception.code, "resource_unavailable")
        self.svc.enable("character", "bronya")
        self.assertEqual(self.svc.resolve("character", "bronya").revision, 1)
        self.svc.archive("character", "bronya")
        with self.assertRaises(registry.RegistryError):
            self.svc.resolve("character", "bronya")
        # history is preserved
        self.assertEqual(len(self.svc.revisions("character", "bronya")), 1)

    def test_reference_blocks_delete_and_removal_unblocks(self):
        self.create("voice_profile", "bronya_voice", {"name": "bronya_default"})
        self.create("character", "bronya_ref", {"name": "Bronya"})
        self.svc.add_reference("character", "bronya_ref", "voice_profile", "bronya_voice")
        with self.assertRaises(registry.RegistryError) as cm:
            self.svc.delete("voice_profile", "bronya_voice")
        self.assertEqual(cm.exception.code, "reference_exists")
        self.svc.remove_reference("character", "bronya_ref", "voice_profile", "bronya_voice")
        self.svc.delete("voice_profile", "bronya_voice")
        self.assertEqual(self.svc.get("voice_profile", "bronya_voice")["status"], "deleted")

    def test_idempotent_create_and_conflict(self):
        first = self.svc.create("character", "idem", {"name": "A"},
                                idempotency_key="key-1")
        second = self.svc.create("character", "idem", {"name": "A"},
                                 idempotency_key="key-1")
        self.assertEqual(first["id"], second["id"])
        with self.assertRaises(registry.RegistryError) as cm:
            self.svc.create("character", "idem", {"name": "B"},
                            idempotency_key="key-1")
        self.assertEqual(cm.exception.code, "idempotency_conflict")

    def test_request_context_locks_revision(self):
        self.create(payload={"name": "v1"})
        self.svc.publish("character", "bronya", 1,
                         expected_current_revision=1, expected_published_revision=None)
        ctx = registry.RequestContext("req-test")
        pinned = ctx.resolve(self.svc, "character", "bronya")
        self.svc.update("character", "bronya", {"name": "v2"}, expected_revision=1)
        self.svc.publish("character", "bronya", 2,
                         expected_current_revision=2, expected_published_revision=1)
        self.assertEqual(pinned.revision, 1)
        self.assertEqual(ctx.resolve(self.svc, "character", "bronya").revision, 1)
        new_ctx = registry.RequestContext("req-test-2")
        self.assertEqual(new_ctx.resolve(self.svc, "character", "bronya").revision, 2)

    def test_schema_and_state_survive_restart(self):
        self.create(payload={"name": "v1"})
        self.svc.publish("character", "bronya", 1,
                         expected_current_revision=1, expected_published_revision=None)
        registry.init_schema()  # idempotent restart
        fresh = registry.RegistryService()
        self.assertEqual(fresh.resolve("character", "bronya").revision, 1)
        self.assertEqual(len(fresh.revisions("character", "bronya")), 1)

    def test_schema_failure_rolls_back_and_recovers(self):
        # Drop the registry objects created by setUp, then force a failure after
        # DDL inside init_schema's transaction. The whole migration must roll back.
        c = store.conn()
        try:
            for table in ("registry_references", "registry_idempotency", "registry_audit",
                          "registry_revisions", "registry_resources"):
                c.execute(f"DROP TABLE IF EXISTS {table}")
            c.execute("DELETE FROM schema_meta WHERE k='registry_schema_version'")
            c.commit()
        finally:
            c.close()

        def boom(_c):
            raise RuntimeError("migration failure")

        with self.assertRaises(RuntimeError):
            registry.init_schema(_after_ddl=boom)
        c = store.conn()
        try:
            names = {r["name"] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertNotIn("registry_resources", names)
        finally:
            c.close()
        registry.init_schema()
        svc = registry.RegistryService()
        self.assertEqual(svc.create("character", "recovered", {"name": "ok"})["current_revision"], 1)

    def test_reference_delete_race_has_no_dangling_reference(self):
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
                self.svc.delete("voice_profile", "voice")
                results.append("delete_ok")
            except registry.RegistryError as exc:
                results.append(exc.code)

        threads = [threading.Thread(target=add_ref), threading.Thread(target=delete_target)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        # Exactly one side may win; the losing side must not leave a dangling ref.
        self.assertEqual(len([r for r in results if r in {"ref_ok", "delete_ok"}]), 1, str(results))
        got = self.svc.get("voice_profile", "voice")
        if got["status"] == "deleted":
            with self.assertRaises(registry.RegistryError):
                self.svc.add_reference("character", "char", "voice_profile", "voice")

    def test_list_pagination(self):
        for i in range(3):
            self.create("character", f"c{i}", {"name": str(i)})
        page = self.svc.list("character", limit=2, offset=0)
        self.assertEqual(page["total"], 3)
        self.assertEqual(len(page["items"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
