"""Phase 4A capability invocation tests."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from core import capabilities, registry, registry_manage, registry_migration, registry_runtime, store  # noqa: E402


class CapabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        chars = root / "characters"
        chars.mkdir()
        (chars / "bronya.json").write_text(json.dumps({
            "id": "bronya", "name": "Bronya", "workflow": "portrait",
            "style_prompt": "x", "voice": {"engine": "kokoro", "voice": "zf_xiaobei"},
        }), encoding="utf-8")
        self.old = (store.DB_BACKEND, store.DB_PATH, store.CHARS_DIR, store.DATA_DIR,
                    os.environ.get("MOBIUS_REGISTRY_RUNTIME"))
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = root / "cap.db"
        store.CHARS_DIR = chars
        store.DATA_DIR = root / "data"
        store.init_db()
        registry.init_schema()
        capabilities.init_schema()
        os.environ["MOBIUS_REGISTRY_RUNTIME"] = "enforce"
        self.svc = registry.RegistryService()
        self.gateway = AsyncMock()
        self.gateway.generate.return_value = {"ok": True, "task_id": "t_cap", "status": "pending"}
        self.gateway.tts.return_value = {"ok": True, "audio_url": "/a.wav", "task_id": "v_cap"}
        self.svc.create("character", "bronya", registry_manage.normalize_character_card(
            {"id": "bronya", "name": "Bronya", "workflow": "portrait",
             "style_prompt": "x", "voice": {"engine": "kokoro", "voice": "zf_xiaobei"}}, "bronya"))
        self.svc.publish("character", "bronya", 1, expected_current_revision=1,
                         expected_published_revision=None)
        self.svc.create("workflow", "comic-portrait", {
            "graph": {"1": {"class_type": "SaveImage", "inputs": {}}},
        })
        self.svc.publish("workflow", "comic-portrait", 1, expected_current_revision=1,
                         expected_published_revision=None)

    def tearDown(self) -> None:
        (store.DB_BACKEND, store.DB_PATH, store.CHARS_DIR, store.DATA_DIR, old_mode) = self.old
        if old_mode is None:
            os.environ.pop("MOBIUS_REGISTRY_RUNTIME", None)
        else:
            os.environ["MOBIUS_REGISTRY_RUNTIME"] = old_mode
        self.tmp.cleanup()

    def test_image_invocation_records_revision(self):
        ch = registry_runtime.character("bronya")
        svc = capabilities.CapabilityService(self.gateway)
        resp = svc._character("bronya", ch)
        self.assertEqual(resp["_registry"]["revision"], 1)

    def test_image_calls_gateway_with_graph_and_records(self):
        ch = registry_runtime.character("bronya")
        wf = registry_runtime.workflow("portrait")
        svc = capabilities.CapabilityService(self.gateway)

        import asyncio
        out = asyncio.run(svc.image(
            prompt="x", character=ch, workflow_graph=wf["graph"],
            user_id="u", session_id="s", source="test", actor="test",
            idempotency_key="idem",
        ))
        self.assertTrue(out["ok"])
        self.assertIn("capability_invocation_id", out)
        kwargs = self.gateway.generate.call_args.kwargs
        self.assertEqual(kwargs["workflow_graph"], wf["graph"])
        rows = capabilities.list_invocations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "accepted")
        self.assertEqual(rows[0]["character_revision"], 1)
        self.assertEqual(rows[0]["task_id"], "t_cap")

    def test_media_policy_denies(self):
        ch = registry_runtime.character("bronya")
        ch["media_policy"] = {"allow_image": False}
        svc = capabilities.CapabilityService(self.gateway)
        import asyncio
        with self.assertRaises(capabilities.CapabilityError) as cm:
            asyncio.run(svc.image(prompt="x", character=ch, source="test"))
        self.assertEqual(cm.exception.code, "media_policy_denied")

    def test_fail_closed_without_published(self):
        registry_migration._write("LEGACY_READ_DISABLED", "test", "test")
        svc = capabilities.CapabilityService(self.gateway)
        import asyncio
        with self.assertRaises(capabilities.CapabilityError) as cm:
            asyncio.run(svc.image(prompt="x", character_key="not_exist", source="test"))
        self.assertEqual(cm.exception.code, "character_not_available")

    def test_voice_invocation(self):
        ch = registry_runtime.character("bronya")
        svc = capabilities.CapabilityService(self.gateway)
        import asyncio
        out = asyncio.run(svc.voice(text="hi", voice="zf_xiaobei", character=ch,
                                     source="test", actor="test"))
        self.assertTrue(out["ok"])
        self.assertEqual(capabilities.list_invocations()[0]["capability"], "voice.synthesize")

    def test_permission_table_denies(self):
        capabilities.set_permission(subject_type="source", subject="blocked",
                                    capability="image.generate", effect="deny")
        svc = capabilities.CapabilityService(self.gateway)
        import asyncio
        with self.assertRaises(capabilities.CapabilityError) as cm:
            asyncio.run(svc.image(prompt="x", character_key="bronya", source="blocked"))
        self.assertEqual(cm.exception.code, "capability_permission_denied")

    def test_provider_policy_routes_voice(self):
        capabilities.set_provider(capability="voice.synthesize", provider="kokoro",
                                  priority=20, enabled=False)
        ch = registry_runtime.character("bronya")
        svc = capabilities.CapabilityService(self.gateway)
        import asyncio
        out = asyncio.run(svc.voice(text="hi", voice="zf_xiaobei", engine="kokoro",
                                     character=ch, source="test", actor="test"))
        self.assertTrue(out["ok"])
        self.assertIn("gpt-sovits", self.gateway.tts.call_args.kwargs["engine"])

    def test_retention_prune(self):
        import time
        with __import__("contextlib").closing(store.conn()) as c:
            c.execute(
                "INSERT INTO capability_invocations(id,capability,actor,user_id,session_id,source,"
                "character_id,character_revision,workflow_id,workflow_revision,provider,task_id,"
                "status,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("old", "image.generate", "t", None, None, "t", "bronya", 1, None, None,
                 "comfyui", None, "accepted", "", time.time() - 40 * 86400, time.time()),
            )
            c.commit()
        result = capabilities.prune_invocations(retention_days=30, max_records=10)
        self.assertGreaterEqual(result["deleted_by_age"], 1)
        self.assertFalse(any(x["id"] == "old" for x in capabilities.list_invocations()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
