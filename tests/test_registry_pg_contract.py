"""PostgreSQL contract test for Phase 0 Registry.

Run only when MOBIUS_TEST_PG_DSN points at a disposable database:
  set MOBIUS_TEST_PG_DSN=postgresql://.../mobius_registry_test
  python tests/test_registry_pg_contract.py
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

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

    def test_reference_delete(self):
        self.svc.create("voice_profile", "voice", {"name": "Voice"})
        self.svc.create("character", "char", {"name": "Char"})
        self.svc.add_reference("character", "char", "voice_profile", "voice")
        with self.assertRaises(registry.RegistryError) as cm:
            self.svc.delete("voice_profile", "voice")
        self.assertEqual(cm.exception.code, "reference_exists")
        self.svc.remove_reference("character", "char", "voice_profile", "voice")
        self.svc.delete("voice_profile", "voice")


if __name__ == "__main__":
    unittest.main(verbosity=2)
