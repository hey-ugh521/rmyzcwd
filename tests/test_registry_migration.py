"""Phase 3B migration state machine and comparison tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from core import registry, registry_index, registry_manage, registry_migration, registry_runtime, store  # noqa: E402


class RegistryMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        chars = root / "characters"
        workflows = root / "workflows"
        chars.mkdir()
        workflows.mkdir()
        (chars / "bronya.json").write_text(json.dumps({
            "id": "bronya", "name": "Bronya", "workflow": "portrait",
            "style_prompt": "silver hair", "negative_prompt": "bad",
            "voice": {"engine": "kokoro", "voice": "zf_xiaobei", "speed": 1.0},
        }), encoding="utf-8")
        (workflows / "comic-portrait.json").write_text(json.dumps({
            "last_node_id": 2,
            "nodes": {
                "1": {"class_type": "KSampler", "inputs": {"steps": 20}},
                "2": {"class_type": "SaveImage", "inputs": {}},
            },
        }), encoding="utf-8")
        self.old = (store.DB_BACKEND, store.DB_PATH, store.CHARS_DIR, store.DATA_DIR,
                    os.environ.get("MOBIUS_WORKFLOWS_DIR"), os.environ.get("MOBIUS_REGISTRY_RUNTIME"))
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = root / "migration.db"
        store.CHARS_DIR = chars
        store.DATA_DIR = root / "data"
        os.environ["MOBIUS_WORKFLOWS_DIR"] = str(workflows)
        store.init_db()
        registry.init_schema()
        self.svc = registry.RegistryService()
        self.workflow_id = "comic-portrait"
        self.voice_id = registry_index.index()["voices"][0]["id"]

    def tearDown(self) -> None:
        (store.DB_BACKEND, store.DB_PATH, store.CHARS_DIR, store.DATA_DIR,
         old_wf, old_mode) = self.old
        if old_wf is None:
            os.environ.pop("MOBIUS_WORKFLOWS_DIR", None)
        else:
            os.environ["MOBIUS_WORKFLOWS_DIR"] = old_wf
        if old_mode is None:
            os.environ.pop("MOBIUS_REGISTRY_RUNTIME", None)
        else:
            os.environ["MOBIUS_REGISTRY_RUNTIME"] = old_mode
        self.tmp.cleanup()

    def _seed_all(self):
        card = {"id": "bronya", "name": "Bronya", "workflow": "portrait",
                "style_prompt": "silver hair", "negative_prompt": "bad",
                "voice": {"engine": "kokoro", "voice": "zf_xiaobei", "speed": 1.0}}
        payload = registry_manage.normalize_character_card(card, "bronya")
        self.svc.create("character", "bronya", payload)
        self.svc.publish("character", "bronya", 1, expected_current_revision=1,
                         expected_published_revision=None)
        wf = {"1": {"class_type": "KSampler", "inputs": {"steps": 20}},
              "2": {"class_type": "SaveImage", "inputs": {}}}
        registry_manage.import_workflow(self.svc, wf, workflow_id=self.workflow_id)
        self.svc.publish("workflow", self.workflow_id, 1, expected_current_revision=1,
                         expected_published_revision=None)
        voice = {"id": self.voice_id, "name": "voice", "engine": "kokoro",
                 "voice": "zf_xiaobei", "speed": 1.0,
                 "used_by": registry_index.index()["voices"][0]["used_by"]}
        self.svc.create("voice_profile", self.voice_id, voice)
        self.svc.publish("voice_profile", self.voice_id, 1, expected_current_revision=1,
                         expected_published_revision=None)

    def test_compare_ok_and_state_advances(self):
        self._seed_all()
        self.assertTrue(registry_migration.compare()["ok"], registry_migration.compare())
        for state in ("LEGACY_INDEXED", "REGISTRY_IMPORTED", "REGISTRY_VERIFIED",
                      "RUNTIME_SHADOW", "RUNTIME_SWITCHED", "LEGACY_READ_DISABLED"):
            registry_migration.advance(state)
        self.assertEqual(registry_migration.current_state(), "LEGACY_READ_DISABLED")
        self.assertFalse(registry_migration.legacy_fallback_enabled())
        self.assertIsNone(registry_migration.status()["next"])

    def test_advance_rejects_skip(self):
        registry_migration.advance("LEGACY_INDEXED")
        with self.assertRaises(ValueError):
            registry_migration.advance("REGISTRY_VERIFIED")

    def test_compare_detects_mismatch(self):
        self._seed_all()
        self.svc.update("character", "bronya", {"identity": {"name": "Changed"}},
                        expected_revision=1)
        self.svc.publish("character", "bronya", 2, expected_current_revision=2,
                         expected_published_revision=1)
        cmp = registry_migration.compare()
        self.assertFalse(cmp["ok"])
        self.assertTrue(any(m["field"] == "name" for m in cmp["mismatches"]))

    def test_disable_rejects_incomplete_coverage(self):
        self._seed_all()
        for state in ("LEGACY_INDEXED", "REGISTRY_IMPORTED", "REGISTRY_VERIFIED",
                      "RUNTIME_SHADOW", "RUNTIME_SWITCHED"):
            registry_migration.advance(state)
        # remove voice coverage by archiving the voice profile
        self.svc.archive("voice_profile", self.voice_id, expected_revision=1)
        with self.assertRaises(ValueError):
            registry_migration.advance("LEGACY_READ_DISABLED")

    def test_runtime_fails_closed_when_disabled(self):
        os.environ["MOBIUS_REGISTRY_RUNTIME"] = "enforce"
        registry_migration._write("LEGACY_READ_DISABLED", "test", "test")
        self.assertIsNone(registry_runtime.character("bronya"))

    def test_runtime_closed_state_reads_only_registry(self):
        self._seed_all()
        os.environ["MOBIUS_REGISTRY_RUNTIME"] = "enforce"
        registry_migration._write("LEGACY_READ_DISABLED", "test", "test")
        (store.CHARS_DIR / "bronya.json").unlink()
        ch = registry_runtime.character("bronya")
        self.assertIsNotNone(ch)
        self.assertEqual(ch["_registry"]["revision"], 1)
        self.assertEqual(ch["style_prompt"], "silver hair")


if __name__ == "__main__":
    unittest.main(verbosity=2)
