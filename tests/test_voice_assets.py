"""Phase 2C VoiceAsset lifecycle tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import registry, store, voice_assets  # noqa: E402
from core.registry import RegistryError  # noqa: E402
from core.registry_api import registry_error_handler  # noqa: E402
from core.registry_manage_api import router  # noqa: E402


class VoiceAssetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_backend = store.DB_BACKEND
        self.old_path = store.DB_PATH
        self.old_cfg = store.CFG.get("registry")
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = root / "voice_assets.db"
        store.CFG["registry"] = {"voice_assets_dir": str(root / "assets")}
        registry.init_schema()

    def tearDown(self) -> None:
        store.close_anchor()
        store.DB_BACKEND = self.old_backend
        store.DB_PATH = self.old_path
        if self.old_cfg is None:
            store.CFG.pop("registry", None)
        else:
            store.CFG["registry"] = self.old_cfg
        self.tmp.cleanup()

    def test_create_list_get_delete(self):
        row = voice_assets.create_asset(
            kind="reference_audio", filename="sample.wav",
            data=b"RIFF....WAVE", source="test", consent="test-consent",
            reuse_policy="this_profile",
        )
        self.assertTrue(row["id"].startswith("va_"))
        self.assertEqual(row["checksum"], __import__("hashlib").sha256(b"RIFF....WAVE").hexdigest())
        self.assertTrue(Path(row["stored_path"]).is_file())
        self.assertEqual(len(voice_assets.list_assets()), 1)
        self.assertEqual(voice_assets.get_asset(row["id"])["kind"], "reference_audio")
        self.assertEqual(voice_assets.read_asset_bytes(row["id"]), b"RIFF....WAVE")
        deleted = voice_assets.delete_asset(row["id"])
        self.assertEqual(deleted["deleted"], 1)
        self.assertEqual(voice_assets.list_assets(), [])
        self.assertIsNone(voice_assets.get_asset(row["id"]))

    def test_invalid_assets(self):
        with self.assertRaises(voice_assets.AssetError) as cm:
            voice_assets.create_asset(kind="unknown", filename="x.wav", data=b"x")
        self.assertEqual(cm.exception.code, "invalid_asset_kind")
        with self.assertRaises(voice_assets.AssetError) as cm:
            voice_assets.create_asset(kind="reference_audio", filename="x.exe", data=b"x")
        self.assertEqual(cm.exception.code, "unsupported_asset_type")
        with self.assertRaises(voice_assets.AssetError) as cm:
            voice_assets.create_asset(kind="reference_audio", filename="x.wav", data=b"")
        self.assertEqual(cm.exception.code, "empty_asset")

    def test_missing_asset(self):
        with self.assertRaises(voice_assets.AssetError) as cm:
            voice_assets.delete_asset("missing")
        self.assertEqual(cm.exception.code, "asset_not_found")

    def test_management_api_upload_and_delete(self):
        app = FastAPI()
        app.add_exception_handler(RegistryError, registry_error_handler)
        app.include_router(router)
        c = TestClient(app)
        r = c.post("/api/registry/manage/voice-assets",
                   files={"file": ("sample.wav", b"RIFF....WAVE", "audio/wav")},
                   data={"kind": "reference_audio", "reuse_policy": "this_profile"})
        self.assertEqual(r.status_code, 200, r.text)
        item = r.json()["item"]
        self.assertEqual(item["kind"], "reference_audio")
        r = c.get("/api/registry/manage/voice-assets")
        self.assertEqual(len(r.json()["items"]), 1)
        r = c.delete(f"/api/registry/manage/voice-assets/{item['id']}")
        self.assertEqual(r.status_code, 200, r.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
