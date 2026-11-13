"""Phase 3A runtime resolver tests."""

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

from core import registry, registry_manage, registry_runtime, store  # noqa: E402


class RegistryRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        chars = root / "characters"
        chars.mkdir()
        (chars / "bronya.json").write_text(json.dumps({
            "id": "bronya", "name": "布洛妮娅", "workflow": "portrait",
            "style_prompt": "legacy style", "voice": {"engine": "kokoro", "voice": "zf_xiaobei"},
        }, ensure_ascii=False), encoding="utf-8")
        self.old_backend = store.DB_BACKEND
        self.old_path = store.DB_PATH
        self.old_chars = store.CHARS_DIR
        self.old_mode = os.environ.get("MOBIUS_REGISTRY_RUNTIME")
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = root / "runtime.db"
        store.CHARS_DIR = chars
        store.init_db()
        registry.init_schema()
        self.svc = registry.RegistryService()

    def tearDown(self) -> None:
        store.DB_BACKEND = self.old_backend
        store.DB_PATH = self.old_path
        store.CHARS_DIR = self.old_chars
        if self.old_mode is None:
            os.environ.pop("MOBIUS_REGISTRY_RUNTIME", None)
        else:
            os.environ["MOBIUS_REGISTRY_RUNTIME"] = self.old_mode
        self.tmp.cleanup()

    def _publish_character(self):
        card = {"id": "bronya", "name": "布洛妮娅", "workflow": "portrait",
                "style_prompt": "registry style",
                "voice": {"engine": "kokoro", "voice": "zf_xiaobei"}}
        payload = registry_manage.normalize_character_card(card, "bronya")
        self.svc.create("character", "bronya", payload)
        self.svc.publish("character", "bronya", 1,
                         expected_current_revision=1, expected_published_revision=None)
        return payload

    def test_off_preserves_legacy(self):
        self._publish_character()
        os.environ["MOBIUS_REGISTRY_RUNTIME"] = "off"
        ch = registry_runtime.character("bronya")
        self.assertEqual(ch["style_prompt"], "legacy style")

    def test_shadow_reads_but_returns_legacy(self):
        self._publish_character()
        os.environ["MOBIUS_REGISTRY_RUNTIME"] = "shadow"
        ch = registry_runtime.character("bronya")
        self.assertEqual(ch["style_prompt"], "legacy style")

    def test_enforce_uses_published_character(self):
        self._publish_character()
        os.environ["MOBIUS_REGISTRY_RUNTIME"] = "enforce"
        ch = registry_runtime.character("bronya")
        self.assertEqual(ch["style_prompt"], "registry style")
        self.assertEqual(ch["workflow"], "portrait")
        self.assertEqual(ch["name"], "布洛妮娅")
        self.assertEqual(ch["_registry"]["revision"], 1)
        self.assertEqual(ch["voice"]["voice"], "zf_xiaobei")

    def test_enforce_falls_back_without_published(self):
        os.environ["MOBIUS_REGISTRY_RUNTIME"] = "enforce"
        ch = registry_runtime.character("bronya")
        self.assertEqual(ch["style_prompt"], "legacy style")

    def test_workflow_alias_resolution(self):
        graph = {
            "1": {"class_type": "KSampler", "inputs": {"steps": 20}},
            "2": {"class_type": "SaveImage", "inputs": {}},
        }
        registry_manage.import_workflow(self.svc, graph, workflow_id="comic-portrait")
        self.svc.publish("workflow", "comic-portrait", 1,
                         expected_current_revision=1, expected_published_revision=None)
        os.environ["MOBIUS_REGISTRY_RUNTIME"] = "enforce"
        wf = registry_runtime.workflow("portrait")
        self.assertIsNotNone(wf)
        self.assertEqual(wf["id"], "comic-portrait")
        self.assertEqual(wf["revision"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
