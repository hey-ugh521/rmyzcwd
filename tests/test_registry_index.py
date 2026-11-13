"""Phase 1 read-only Registry index tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Read-only index tests do not need PostgreSQL. Keep them independent of
# whichever production DB backend is configured on this machine.
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import registry_index, store  # noqa: E402
from core.registry import RegistryError  # noqa: E402
from core.registry_api import registry_error_handler, router as registry_router  # noqa: E402
from core.registry_index_api import router  # noqa: E402


class RegistryIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        chars = root / "characters"
        workflows = root / "workflows"
        chars.mkdir()
        workflows.mkdir()
        (chars / "bronya.json").write_text(json.dumps({
            "id": "bronya", "name": "布洛妮娅", "aliases": ["Bronya"],
            "workflow": "portrait", "checkpoint": "Illustrious-XL-v1.0.safetensors",
            "voice": {"engine": "kokoro", "voice": "zf_xiaobei", "speed": 1.0},
            "media_policy": {"allow_image": True, "allow_voice": True},
        }, ensure_ascii=False), encoding="utf-8")
        (chars / "rin.json").write_text(json.dumps({
            "id": "rin", "name": "凛",
            "voice": {"engine": "kokoro", "voice": "zf_xiaobei", "speed": 1.0},
            "workflow": "portrait",
        }, ensure_ascii=False), encoding="utf-8")
        (workflows / "comic-portrait.json").write_text(json.dumps({
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
            "2": {"class_type": "KSampler", "inputs": {}},
        }, ensure_ascii=False), encoding="utf-8")
        (workflows / "ui.json").write_text(json.dumps({
            "nodes": [{"type": "KSampler"}, {"type": "VAEDecode"}],
            "links": [],
        }, ensure_ascii=False), encoding="utf-8")
        (workflows / "models.json").write_text(json.dumps({
            "models": [{"id": "anime", "name": "Anime", "workflow": "portrait",
                        "default": True, "vram_gb": 6}]
        }, ensure_ascii=False), encoding="utf-8")

        self.old_chars = store.CHARS_DIR
        self.old_env = os.environ.get("MOBIUS_WORKFLOWS_DIR")
        store.CHARS_DIR = chars
        os.environ["MOBIUS_WORKFLOWS_DIR"] = str(workflows)

    def tearDown(self) -> None:
        store.CHARS_DIR = self.old_chars
        if self.old_env is None:
            os.environ.pop("MOBIUS_WORKFLOWS_DIR", None)
        else:
            os.environ["MOBIUS_WORKFLOWS_DIR"] = self.old_env
        self.tmp.cleanup()

    def test_index_projects_legacy_files(self):
        data = registry_index.index()
        self.assertEqual(data["summary"]["characters"], 2)
        self.assertEqual(data["summary"]["voices"], 1)
        self.assertEqual(data["summary"]["workflows"], 2)
        self.assertEqual(data["summary"]["image_models"], 1)
        self.assertTrue(data["summary"]["read_only"])
        character = next(c for c in data["characters"] if c["id"] == "bronya")
        self.assertEqual(character["source_kind"], "legacy_character_json")
        self.assertEqual(character["voice"]["voice"], "zf_xiaobei")
        voice = data["voices"][0]
        self.assertEqual(sorted(voice["used_by"]), ["bronya", "rin"])
        api = next(w for w in data["workflows"] if w["id"] == "comic-portrait")
        ui = next(w for w in data["workflows"] if w["id"] == "ui")
        self.assertEqual(api["format"], "api_prompt")
        self.assertEqual(ui["format"], "ui_workflow")
        self.assertEqual(api["used_by"], ["bronya", "rin"])

    def test_api(self):
        app = FastAPI()
        app.add_exception_handler(RegistryError, registry_error_handler)
        app.include_router(router)
        app.include_router(registry_router)
        c = TestClient(app)
        r = c.get("/api/registry/index")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["summary"]["characters"], 2)
        self.assertEqual(c.get("/api/registry/index/characters/bronya").status_code, 200)
        self.assertEqual(c.get("/api/registry/index/characters/missing").status_code, 404)
        self.assertEqual(c.get("/api/registry/index/workflows/comic-portrait").json()["item"]["format"], "api_prompt")

    def test_index_api_does_not_write_legacy_files(self):
        before = {p.name: p.stat().st_mtime_ns for p in Path(store.CHARS_DIR).glob("*.json")}
        registry_index.index()
        after = {p.name: p.stat().st_mtime_ns for p in Path(store.CHARS_DIR).glob("*.json")}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
