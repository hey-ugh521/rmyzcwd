"""Phase 4A: unified capability invocation, permission checks and routing.

Agent and other Core callers should enter media execution here instead of
calling GatewayClient directly. The service resolves Registry state, enforces
media policy, routes to the provider endpoint and records an execution row.
"""

from __future__ import annotations

import time
import uuid
from contextlib import closing
from typing import Any

from . import registry_migration, registry_runtime, store
from .services import GatewayClient

CAPABILITIES = {
    "image.generate": {"provider": "comfyui", "kind": "image"},
    "voice.synthesize": {"provider": "auto", "kind": "voice"},
}


class CapabilityError(Exception):
    def __init__(self, code: str, message: str, status: int = 400,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def init_schema() -> None:
    c = store.conn()
    try:
        c.execute("""
        CREATE TABLE IF NOT EXISTS capability_invocations(
          id TEXT PRIMARY KEY,
          capability TEXT NOT NULL,
          actor TEXT,
          user_id TEXT,
          session_id TEXT,
          source TEXT,
          character_id TEXT,
          character_revision INTEGER,
          workflow_id TEXT,
          workflow_revision INTEGER,
          provider TEXT,
          task_id TEXT,
          status TEXT NOT NULL,
          error TEXT,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_capability_invocations_created "
                  "ON capability_invocations(created_at DESC)")
        c.commit()
    finally:
        c.close()


def _record(*, capability: str, actor: str, user_id: str | None,
            session_id: str | None, source: str, character: dict | None,
            workflow: dict | None, provider: str, status: str = "running",
            error: str = "") -> str:
    iid = "cap_" + uuid.uuid4().hex[:12]
    reg = (character or {}).get("_registry") or {}
    wf = workflow or {}
    now = time.time()
    with closing(store.conn()) as c:
        c.execute(
            "INSERT INTO capability_invocations(id,capability,actor,user_id,session_id,source,"
            "character_id,character_revision,workflow_id,workflow_revision,provider,task_id,"
            "status,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, capability, actor, user_id, session_id, source,
             (character or {}).get("id"), reg.get("revision"),
             (wf or {}).get("id"), (wf or {}).get("revision"),
             provider, None, status, error, now, now),
        )
        c.commit()
    return iid


def _finish(iid: str, *, status: str, task_id: str | None = None,
            error: str = "") -> None:
    with closing(store.conn()) as c:
        c.execute(
            "UPDATE capability_invocations SET status=?,task_id=?,error=?,updated_at=? WHERE id=?",
            (status, task_id, error, time.time(), iid),
        )
        c.commit()


def list_invocations(limit: int = 50) -> list[dict[str, Any]]:
    with closing(store.conn()) as c:
        rows = c.execute(
            "SELECT * FROM capability_invocations ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),),
        ).fetchall()
    return [dict(r) for r in rows]


def _allowed_sources() -> set[str] | None:
    raw = (store.CFG.get("capabilities") or {}).get("allowed_sources")
    if not raw:
        return None
    return {str(x) for x in raw}


class CapabilityService:
    def __init__(self, gateway: GatewayClient | None = None) -> None:
        self.gateway = gateway or GatewayClient()

    def _check_source(self, source: str) -> None:
        allowed = _allowed_sources()
        if allowed is not None and source not in allowed:
            raise CapabilityError("capability_source_denied",
                                  f"来源 {source!r} 不允许调用能力", 403)

    def _character(self, character_key: str | None,
                   character: dict | None) -> dict:
        if isinstance(character, dict) and character.get("_registry"):
            return character
        resolved = registry_runtime.character(character_key or (character or {}).get("id"))
        if resolved is None:
            raise CapabilityError("character_not_available",
                                  "角色未解析到已发布 Registry revision", 404)
        return resolved

    def _workflow(self, character: dict, graph: dict | None) -> dict | None:
        resolved = registry_runtime.workflow(character.get("workflow"))
        if isinstance(graph, dict) and graph:
            if resolved is not None:
                return {**resolved, "graph": graph}
            return {"id": character.get("workflow"), "revision": None, "graph": graph}
        if resolved is None and not registry_migration.legacy_fallback_enabled():
            raise CapabilityError("workflow_not_available",
                                  "工作流未解析到已发布 Registry revision", 404)
        return resolved

    async def image(self, *, prompt: str, character_key: str | None = None,
                    character: dict | None = None, workflow_graph: dict | None = None,
                    user_id: str | None = None, session_id: str | None = None,
                    source: str = "core", actor: str = "agent",
                    idempotency_key: str = "", model: str | None = None,
                    **extra: Any) -> dict:
        self._check_source(source)
        ch = self._character(character_key, character)
        if (ch.get("media_policy") or {}).get("allow_image") is False:
            raise CapabilityError("media_policy_denied", "角色未开放生图", 403)
        wf = self._workflow(ch, workflow_graph)
        graph = (wf or {}).get("graph")
        payload: dict[str, Any] = {
            "prompt": prompt,
            "character": ch.get("id"),
            "user_id": user_id,
            "session_id": session_id,
            "source": source,
            "idempotency_key": idempotency_key or f"capability-{uuid.uuid4().hex}",
            "async": True,
        }
        if model:
            payload["model"] = model
        if graph:
            payload["workflow_graph"] = graph
            payload["workflow"] = f"registry:{wf.get('id') or ch.get('workflow')}"
        payload.update(extra)
        iid = _record(capability="image.generate", actor=actor, user_id=user_id,
                      session_id=session_id, source=source, character=ch,
                      workflow=wf, provider="comfyui")
        try:
            resp = await self.gateway.generate(**payload)
        except Exception as exc:  # noqa: BLE001
            _finish(iid, status="failed", error=f"{type(exc).__name__}: {exc}")
            raise CapabilityError("capability_execution_failed", str(exc), 502) from exc
        task_id = resp.get("task_id") or resp.get("id")
        _finish(iid, status="accepted", task_id=str(task_id) if task_id else None)
        store.audit(actor, "capability.invoke", {
            "capability": "image.generate", "invocation_id": iid,
            "character": ch.get("id"), "workflow": (wf or {}).get("id"),
            "character_revision": (ch.get("_registry") or {}).get("revision"),
        }, task_id=str(task_id) if task_id else None, character=ch.get("id"),
            source=source)
        return {**resp, "capability_invocation_id": iid}

    async def voice(self, *, text: str, voice: str, engine: str = "kokoro",
                    speed: float = 1.0, character: dict | None = None,
                    character_key: str | None = None,
                    user_id: str | None = None, session_id: str | None = None,
                    source: str = "core", actor: str = "agent", **extra: Any) -> dict:
        self._check_source(source)
        ch = self._character(character_key, character)
        if (ch.get("media_policy") or {}).get("allow_voice") is False:
            raise CapabilityError("media_policy_denied", "角色未开放语音", 403)
        provider = "gpt-sovits" if engine.lower().startswith("gpt") else "kokoro"
        iid = _record(capability="voice.synthesize", actor=actor, user_id=user_id,
                      session_id=session_id, source=source, character=ch,
                      workflow=None, provider=provider)
        try:
            resp = await self.gateway.tts(text, voice=voice, engine=engine, speed=speed)
        except Exception as exc:  # noqa: BLE001
            _finish(iid, status="failed", error=f"{type(exc).__name__}: {exc}")
            raise CapabilityError("capability_execution_failed", str(exc), 502) from exc
        task_id = resp.get("task_id")
        _finish(iid, status="completed", task_id=str(task_id) if task_id else None)
        store.audit(actor, "capability.invoke", {
            "capability": "voice.synthesize", "invocation_id": iid,
            "character": ch.get("id"), "engine": engine, "voice": voice,
            "character_revision": (ch.get("_registry") or {}).get("revision"),
        }, task_id=str(task_id) if task_id else None, character=ch.get("id"),
            source=source)
        return {**resp, "capability_invocation_id": iid}
