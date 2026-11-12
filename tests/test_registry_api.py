"""Phase 0 Registry API contract tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import registry, store  # noqa: E402
from core.registry import RegistryError  # noqa: E402
from core.registry_api import registry_error_handler, router, service  # noqa: E402


class RegistryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_backend = store.DB_BACKEND
        self.old_path = store.DB_PATH
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = Path(self.tmp.name) / "registry_api.db"
        registry.init_schema()
        app = FastAPI()
        app.add_exception_handler(RegistryError, registry_error_handler)
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        store.DB_BACKEND = self.old_backend
        store.DB_PATH = self.old_path
        self.tmp.cleanup()

    def create(self, rid="bronya", payload=None, headers=None):
        return self.client.post(
            "/api/registry/character",
            json={"id": rid, "payload": payload or {"name": "Bronya"}},
            headers=headers or {},
        )

    def test_create_update_publish_rollback(self):
        r = self.create(payload={"name": "v1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["current_revision"], 1)

        r = self.client.put("/api/registry/character/bronya",
                            json={"payload": {"name": "v2"}, "expected_revision": 1})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["current_revision"], 2)

        r = self.client.post("/api/registry/character/bronya/publish",
                             json={"revision": 2, "expected_current_revision": 2,
                                   "expected_published_revision": None})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["published_revision"], 2)

        r = self.client.post("/api/registry/character/bronya/rollback",
                             json={"revision": 1, "expected_current_revision": 2,
                                   "expected_published_revision": 2})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["published_revision"], 1)

    def test_optimistic_lock_conflict(self):
        self.create()
        r = self.client.put("/api/registry/character/bronya",
                            json={"payload": {"name": "v2"}, "expected_revision": 99})
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["error"]["code"], "revision_conflict")

    def test_structured_errors(self):
        r = self.client.get("/api/registry/character/missing")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["error"]["code"], "resource_not_found")
        r = self.client.get("/api/registry/not_a_type")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "invalid_resource_type")

    def test_idempotency(self):
        headers = {"Idempotency-Key": "api-key-1"}
        a = self.create(rid="idem-api", headers=headers)
        b = self.create(rid="idem-api", headers=headers)
        self.assertEqual(a.status_code, 200, a.text)
        self.assertEqual(b.status_code, 200, b.text)
        c = self.create(rid="idem-api", payload={"name": "other"}, headers=headers)
        self.assertEqual(c.status_code, 409, c.text)

    def test_reference_delete_conflict(self):
        self.create(rid="char", payload={"name": "Char"})
        self.client.post("/api/registry/voice_profile",
                         json={"id": "voice", "payload": {"name": "Voice"}})
        r = self.client.post("/api/registry/character/char/references",
                             json={"target_type": "voice_profile", "target_id": "voice"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.delete("/api/registry/voice_profile/voice")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertEqual(r.json()["error"]["code"], "reference_exists")
        self.client.delete("/api/registry/character/char/references",
                           params={"target_type": "voice_profile", "target_id": "voice"})
        r = self.client.delete("/api/registry/voice_profile/voice")
        self.assertEqual(r.status_code, 200, r.text)

    def test_state_endpoints(self):
        self.create()
        self.client.post("/api/registry/character/bronya/publish",
                         json={"revision": 1, "expected_current_revision": 1,
                               "expected_published_revision": None})
        r = self.client.post("/api/registry/character/bronya/disable")
        self.assertEqual(r.json()["status"], "disabled")
        r = self.client.post("/api/registry/character/bronya/enable")
        self.assertEqual(r.json()["status"], "active")
        r = self.client.post("/api/registry/character/bronya/archive")
        self.assertEqual(r.json()["status"], "archived")

    def test_list_pagination(self):
        for i in range(3):
            self.create(rid=f"c{i}", payload={"name": str(i)})
        r = self.client.get("/api/registry/character", params={"limit": 2, "offset": 0})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["total"], 3)
        self.assertEqual(len(body["items"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
