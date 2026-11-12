"""Core auth integration tests for /api/registry (real Core app middleware)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from core import registry, store  # noqa: E402


class RegistryCoreAuthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_backend = store.DB_BACKEND
        cls.old_path = store.DB_PATH
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = Path(cls.tmp.name) / "core_auth.db"
        registry.init_schema()

        # Import the real Core app after the test DB is selected.
        import core.server as coreserver  # noqa: PLC0415
        cls.coreserver = coreserver
        cls.old_key = coreserver.API_KEY
        coreserver.API_KEY = "test-api-key"
        cls.client = TestClient(coreserver.app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.coreserver.API_KEY = cls.old_key
        store.DB_BACKEND = cls.old_backend
        store.DB_PATH = cls.old_path
        cls.tmp.cleanup()

    def setUp(self) -> None:
        self.client.cookies.clear()
        import core.server as coreserver  # noqa: PLC0415
        coreserver.API_KEY = "test-api-key"

    def body(self, rid: str, actor: str = "spoofed") -> dict:
        return {"id": rid, "payload": {"name": rid}, "actor": actor}

    def test_unauthenticated_mutation_rejected(self):
        r = self.client.post("/api/registry/character", json=self.body("unauth"))
        self.assertEqual(r.status_code, 401, r.text)

    def test_bearer_auth_and_audit_actor(self):
        r = self.client.post("/api/registry/character",
                             json=self.body("bearer"),
                             headers={"Authorization": "Bearer test-api-key",
                                      "X-Request-ID": "req-bearer"})
        self.assertEqual(r.status_code, 200, r.text)
        c = store.conn()
        try:
            row = c.execute(
                "SELECT actor,request_id FROM registry_audit "
                "WHERE resource_type='character' AND resource_id='bearer' "
                "AND action='create' ORDER BY ts DESC LIMIT 1").fetchone()
        finally:
            c.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["actor"], "api-key")
        self.assertNotEqual(row["actor"], "spoofed")
        self.assertEqual(row["request_id"], "req-bearer")

    def test_cookie_auth(self):
        self.client.cookies.set(self.coreserver.LOCAL_TOKEN_COOKIE, "test-api-key")
        r = self.client.post("/api/registry/character", json=self.body("cookie"))
        self.assertEqual(r.status_code, 200, r.text)
        c = store.conn()
        try:
            actor = c.execute(
                "SELECT actor FROM registry_audit WHERE resource_type='character' "
                "AND resource_id='cookie' AND action='create' "
                "ORDER BY ts DESC LIMIT 1").fetchone()["actor"]
        finally:
            c.close()
        self.assertEqual(actor, "local-session")

    def test_read_loopback_is_read_only(self):
        r = self.client.get("/api/registry/character")
        self.assertEqual(r.status_code, 200, r.text)

    def test_error_does_not_leak_internal_details(self):
        r = self.client.post("/api/registry/character",
                             json={"id": "bad id with spaces", "payload": {}},
                             headers={"Authorization": "Bearer test-api-key"})
        self.assertIn(r.status_code, {422, 400})
        text = r.text
        self.assertNotIn("Traceback", text)
        self.assertNotIn("sqlite", text.lower())
        self.assertNotIn("D:\\", text)
        self.assertNotIn("postgresql://", text)
        self.assertNotIn("test-api-key", text)

    def test_route_is_not_mounted_outside_api_middleware(self):
        paths = {getattr(route, "path", "") for route in self.coreserver.app.routes}
        self.assertIn("/api/registry/{resource_type}", paths)
        self.assertNotIn("/registry/{resource_type}", paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)
