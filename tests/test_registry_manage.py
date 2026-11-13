"""Phase 2A management tests: legacy import + workflow validation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import registry, registry_index, registry_manage, store  # noqa: E402
from core.registry import RegistryError  # noqa: E402
from core.registry_api import registry_error_handler  # noqa: E402
from core.registry_manage_api import router  # noqa: E402


class RegistryManageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        chars = root / "characters"
        workflows = root / "workflows"
        chars.mkdir()
        workflows.mkdir()
        (chars / "bronya.json").write_text(json.dumps({
            "id": "bronya", "name": "布洛妮娅", "workflow": "portrait",
            "voice": {"engine": "kokoro", "voice": "zf_xiaobei", "speed": 1.0},
            "style_prompt": "silver hair", "media_policy": {"allow_image": True, "allow_voice": True},
        }, ensure_ascii=False), encoding="utf-8")
        (workflows / "portrait.json").write_text(json.dumps({
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x"}},
            "2": {"class_type": "KSampler", "inputs": {"steps": 20, "cfg": 5.0}},
            "3": {"class_type": "EmptyLatentImage", "inputs": {"width": 832, "height": 1216, "batch_size": 1}},
            "4": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
        }), encoding="utf-8")
        (workflows / "ui.json").write_text(json.dumps({"nodes": [{"type": "KSampler"}], "links": []}), encoding="utf-8")
        (workflows / "models.json").write_text(json.dumps({"models": [{"id": "anime", "name": "Anime", "workflow": "portrait"}]}), encoding="utf-8")

        self.old_chars = store.CHARS_DIR
        self.old_env = os.environ.get("MOBIUS_WORKFLOWS_DIR")
        self.old_backend = store.DB_BACKEND
        self.old_path = store.DB_PATH
        store.CHARS_DIR = chars
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = root / "registry_manage.db"
        os.environ["MOBIUS_WORKFLOWS_DIR"] = str(workflows)
        registry.init_schema()
        self.svc = registry.RegistryService()

    def tearDown(self) -> None:
        store.CHARS_DIR = self.old_chars
        store.DB_BACKEND = self.old_backend
        store.DB_PATH = self.old_path
        if self.old_env is None:
            os.environ.pop("MOBIUS_WORKFLOWS_DIR", None)
        else:
            os.environ["MOBIUS_WORKFLOWS_DIR"] = self.old_env
        self.tmp.cleanup()

    def test_validate_api_prompt_and_bounds(self):
        valid = {
            "1": {"class_type": "KSampler", "inputs": {"steps": 20, "cfg": 5.0}},
            "2": {"class_type": "SaveImage", "inputs": {}},
        }
        report = registry_manage.validate_workflow(valid)
        self.assertEqual(report["status"], "valid", report)
        self.assertEqual(report["dependencies"]["status"], "unchecked")
        bad = {
            "1": {"class_type": "KSampler", "inputs": {"steps": 999}},
            "2": {"class_type": "SaveImage", "inputs": {}},
        }
        report = registry_manage.validate_workflow(bad)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("steps" in e for e in report["errors"]))
        ui = registry_manage.validate_workflow({"nodes": [], "links": []})
        self.assertEqual(ui["format"], "ui_workflow")
        self.assertEqual(ui["status"], "invalid")

    def test_dependency_validation(self):
        payload = {
            "1": {"class_type": "KSampler", "inputs": {"steps": 20}},
            "2": {"class_type": "SaveImage", "inputs": {}},
        }
        ok = registry_manage.validate_workflow(
            payload, object_info={"KSampler": {}, "SaveImage": {}})
        self.assertEqual(ok["status"], "valid")
        self.assertEqual(ok["dependencies"]["status"], "ok")
        missing = registry_manage.validate_workflow(
            payload, object_info={"SaveImage": {}})
        self.assertEqual(missing["status"], "invalid")
        self.assertEqual(missing["dependencies"]["missing_nodes"], ["KSampler"])

    def test_legacy_import_is_draft_and_idempotent(self):
        result = registry_manage.import_legacy(self.svc)
        self.assertEqual(result["characters"]["imported"], 1)
        self.assertEqual(result["voices"]["imported"], 1)
        self.assertEqual(result["workflows"]["imported"], 1)
        char = self.svc.get("character", "bronya", include_snapshot=True)
        self.assertEqual(char["published_revision"], None)
        self.assertEqual(char["current"]["state"], "draft")
        second = registry_manage.import_legacy(self.svc)
        self.assertEqual(second["characters"]["imported"], 0)
        self.assertEqual(second["characters"]["skipped"], 1)
        self.assertEqual(second["workflows"]["imported"], 0)
        self.assertGreaterEqual(second["workflows"]["skipped"], 1)

    def test_import_character_card_v2(self):
        card = {
            "spec": "chara_card_v2",
            "data": {"name": "凛", "description": "silver hair", "first_mes": "你好"},
        }
        result = registry_manage.import_character_card(self.svc, card, character_id="rin-card")
        self.assertEqual(result["resource"]["id"], "rin-card")
        self.assertEqual(result["resource"]["current_revision"], 1)
        self.assertEqual(result["payload"]["source"]["format"], "chara_card_v2")
        self.assertTrue(result["payload"]["prompt"]["blocks"])

    def test_import_workflow_requires_valid_api_prompt(self):
        with self.assertRaises(RegistryError) as cm:
            registry_manage.import_workflow(self.svc, {"nodes": []}, name="bad")
        self.assertEqual(cm.exception.code, "workflow_invalid")
        good = {
            "1": {"class_type": "KSampler", "inputs": {"steps": 20}},
            "2": {"class_type": "SaveImage", "inputs": {}},
        }
        result = registry_manage.import_workflow(self.svc, good, workflow_id="wf-test")
        self.assertEqual(result["resource"]["current_revision"], 1)
        self.assertEqual(result["resource"]["published_revision"], None)

    def test_management_api(self):
        app = FastAPI()
        app.add_exception_handler(RegistryError, registry_error_handler)
        app.include_router(router)
        c = TestClient(app)
        r = c.post("/api/registry/manage/import/legacy")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["characters"]["imported"], 1)
        r = c.post("/api/registry/manage/workflows/validate",
                   json={"payload": {"1": {"class_type": "SaveImage", "inputs": {}}}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["validation"]["status"], "valid")
        with mock.patch.object(registry_manage, "object_info",
                               return_value={"SaveImage": {}}):
            r = c.post("/api/registry/manage/workflows/validate",
                       json={"payload": {"1": {"class_type": "KSampler", "inputs": {}},
                                         "2": {"class_type": "SaveImage", "inputs": {}}},
                             "live_check": True})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["validation"]["dependencies"]["missing_nodes"], ["KSampler"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
