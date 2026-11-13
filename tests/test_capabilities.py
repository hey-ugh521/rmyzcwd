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


class _HealthSupervisor:
    def __init__(self, states):
        self.states = states

    async def probe(self, service):
        return {"state": self.states.get(service, "ready")}


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
        self.old_caps_cfg = store.CFG.get("capabilities")
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
        if self.old_caps_cfg is None:
            store.CFG.pop("capabilities", None)
        else:
            store.CFG["capabilities"] = self.old_caps_cfg
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

    def test_permission_scope_characters(self):
        capabilities.set_permission(subject_type="source", subject="scope-test",
                                    capability="image.generate", effect="deny",
                                    scope={"characters": ["bronya"]})
        denied = capabilities.decide_permission(
            capability="image.generate", actor="scope-test", source="scope-test",
            character_id="bronya")
        allowed = capabilities.decide_permission(
            capability="image.generate", actor="scope-test", source="scope-test",
            character_id="vita")
        self.assertFalse(denied["allow"])
        self.assertTrue(allowed["allow"])

    def test_provider_policy_routes_voice(self):
        capabilities.set_provider(capability="voice.synthesize", provider="kokoro",
                                  priority=20, enabled=False)
        ch = registry_runtime.character("bronya")
        svc = capabilities.CapabilityService(self.gateway)
        import asyncio
        out = asyncio.run(svc.voice(text="hi", voice="zf_xiaobei", engine="kokoro",
                                     character=ch, source="test", actor="test"))
        self.assertTrue(out["ok"])
        self.assertIn("mobius", self.gateway.tts.call_args.kwargs["engine"])

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

    def test_default_and_disabled_permission(self):
        self.assertTrue(capabilities.decide_permission(
            capability="image.generate", actor="u", source="qq")["allow"])
        capabilities.set_permission(subject_type="source", subject="disabled",
                                    capability="image.generate", effect="deny",
                                    enabled=False)
        self.assertTrue(capabilities.decide_permission(
            capability="image.generate", actor="disabled", source="disabled")["allow"])
        store.CFG["capabilities"] = {"default_allow": False}
        self.assertFalse(capabilities.decide_permission(
            capability="image.generate", actor="u", source="none")["allow"])

    def test_specificity_and_deny_wins(self):
        # source wildcard allow vs source exact deny: deny-wins wins at equal rank
        capabilities.set_permission(subject_type="source", subject="*",
                                    capability="image.generate", effect="allow")
        capabilities.set_permission(subject_type="source", subject="qq",
                                    capability="image.generate", effect="deny")
        self.assertFalse(capabilities.decide_permission(
            capability="image.generate", actor="qq", source="qq")["allow"])
        # user specificity outranks a higher-priority source allow
        capabilities.set_permission(subject_type="source", subject="qq",
                                    capability="voice.synthesize", effect="allow", priority=999)
        capabilities.set_permission(subject_type="user", subject="u1",
                                    capability="voice.synthesize", effect="deny", priority=0)
        self.assertFalse(capabilities.decide_permission(
            capability="voice.synthesize", actor="qq", source="qq", user_id="u1")["allow"])

    def test_permission_version_conflict_and_audit(self):
        row = capabilities.set_permission(subject_type="source", subject="v",
                                          capability="image.generate", effect="allow",
                                          actor="admin-a")
        self.assertEqual(row["version"], 1)
        updated = capabilities.set_permission(subject_type="source", subject="v",
                                              capability="image.generate", effect="deny",
                                              expected_version=1, actor="admin-b")
        self.assertEqual(updated["version"], 2)
        with self.assertRaises(capabilities.CapabilityError) as cm:
            capabilities.set_permission(subject_type="source", subject="v",
                                        capability="image.generate", effect="allow",
                                        expected_version=1, actor="admin-c")
        self.assertEqual(cm.exception.code, "permission_conflict")
        with __import__("contextlib").closing(store.conn()) as c:
            rows = c.execute(
                "SELECT actor,result,action,detail FROM audit WHERE action='capability.permission.set' "
                "ORDER BY ts").fetchall()
        self.assertEqual([r["actor"] for r in rows], ["admin-a", "admin-b", "admin-c"])
        self.assertEqual([r["result"] for r in rows], ["ok", "ok", "fail"])
        import json as _json
        final_detail = _json.loads(rows[-1]["detail"])
        self.assertIn("before", final_detail)
        self.assertEqual(final_detail["before"]["effect"], "deny")
        self.assertEqual(final_detail["before"]["version"], 2)

    def test_policy_audit_failure_rolls_back(self):
        from unittest import mock
        with mock.patch.object(capabilities, "_audit_tx", side_effect=RuntimeError("audit down")):
            with self.assertRaises(capabilities.CapabilityError):
                capabilities.set_permission(subject_type="source", subject="rollback",
                                            capability="image.generate", effect="deny")
        self.assertFalse(any(p["subject"] == "rollback" for p in capabilities.permission_rows()))

    def test_concurrent_permission_edit_one_winner(self):
        import threading
        base = capabilities.set_permission(subject_type="source", subject="race",
                                           capability="image.generate", effect="allow")
        results = []
        barrier = threading.Barrier(2)

        def worker(effect, actor):
            barrier.wait()
            try:
                row = capabilities.set_permission(
                    subject_type="source", subject="race", capability="image.generate",
                    effect=effect, expected_version=base["version"], actor=actor)
                results.append(("ok", row["version"]))
            except capabilities.CapabilityError as exc:
                results.append((exc.code, None))

        threads = [threading.Thread(target=worker, args=("deny", "admin-1")),
                   threading.Thread(target=worker, args=("allow", "admin-2"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ok = [r for r in results if r[0] == "ok"]
        conflicts = [r for r in results if r[0] == "permission_conflict"]
        self.assertEqual(len(ok), 1, results)
        self.assertEqual(len(conflicts), 1, results)
        self.assertEqual(ok[0][1], 2)

    def test_dynamic_health_fails_image(self):
        ch = registry_runtime.character("bronya")
        sup = _HealthSupervisor({"comfyui": "down"})
        svc = capabilities.CapabilityService(self.gateway, supervisor=sup)
        import asyncio
        with self.assertRaises(capabilities.CapabilityError) as cm:
            asyncio.run(svc.image(prompt="x", character=ch, source="test"))
        self.assertEqual(cm.exception.code, "provider_unavailable")

    def test_dynamic_health_degrades_voice(self):
        ch = registry_runtime.character("bronya")
        sup = _HealthSupervisor({"kokoro": "down", "gsv": "ready"})
        svc = capabilities.CapabilityService(self.gateway, supervisor=sup)
        import asyncio
        out = asyncio.run(svc.voice(text="hi", voice="zf_xiaobei", engine="kokoro",
                                     character=ch, source="test", actor="test"))
        self.assertTrue(out["ok"])
        self.assertEqual(self.gateway.tts.call_args.kwargs["engine"], "mobius")


if __name__ == "__main__":
    unittest.main(verbosity=2)
