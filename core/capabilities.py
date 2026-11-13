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
        c.execute("""
        CREATE TABLE IF NOT EXISTS capability_permissions(
          id TEXT PRIMARY KEY,
          subject_type TEXT NOT NULL,
          subject TEXT NOT NULL,
          capability TEXT NOT NULL,
          effect TEXT NOT NULL,
          scope_json TEXT,
          priority INTEGER NOT NULL DEFAULT 0,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          UNIQUE(subject_type,subject,capability)
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS capability_providers(
          capability TEXT NOT NULL,
          provider TEXT NOT NULL,
          priority INTEGER NOT NULL DEFAULT 100,
          enabled INTEGER NOT NULL DEFAULT 1,
          constraints_json TEXT,
          updated_at REAL NOT NULL,
          PRIMARY KEY(capability,provider)
        )
        """)
        for capability, provider, priority in (
            ("image.generate", "comfyui", 10),
            ("voice.synthesize", "kokoro", 20),
            ("voice.synthesize", "gpt-sovits", 30),
        ):
            exists = c.execute(
                "SELECT 1 FROM capability_providers WHERE capability=? AND provider=?",
                (capability, provider),
            ).fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO capability_providers(capability,provider,priority,enabled,"
                    "constraints_json,updated_at) VALUES(?,?,?,?,?,?)",
                    (capability, provider, priority, 1, "{}", time.time()),
                )
        c.commit()
    finally:
        c.close()


def _config() -> dict[str, Any]:
    return store.CFG.get("capabilities") or {}


def permission_rows() -> list[dict[str, Any]]:
    with closing(store.conn()) as c:
        rows = c.execute(
            "SELECT * FROM capability_permissions ORDER BY subject_type,subject,capability"
        ).fetchall()
    return [dict(r) for r in rows]


def set_permission(*, subject_type: str, subject: str, capability: str,
                   effect: str, priority: int = 0, enabled: bool = True,
                   scope: dict[str, Any] | None = None) -> dict[str, Any]:
    if subject_type not in {"source", "actor", "user", "*"}:
        raise CapabilityError("invalid_permission_subject", "subject_type 非法", 422)
    if not subject:
        raise CapabilityError("invalid_permission_subject", "subject 不能为空", 422)
    if effect not in {"allow", "deny"}:
        raise CapabilityError("invalid_permission_effect", "effect 必须是 allow/deny", 422)
    import json
    now = time.time()
    pid = "perm_" + uuid.uuid4().hex[:12]
    with closing(store.conn()) as c:
        exists = c.execute(
            "SELECT id FROM capability_permissions WHERE subject_type=? AND subject=? AND capability=?",
            (subject_type, subject, capability),
        ).fetchone()
        if exists:
            pid = exists["id"]
            c.execute(
                "UPDATE capability_permissions SET effect=?,scope_json=?,priority=?,enabled=?,updated_at=? "
                "WHERE id=?",
                (effect, json.dumps(scope or {}, ensure_ascii=False), priority,
                 1 if enabled else 0, now, pid),
            )
        else:
            c.execute(
                "INSERT INTO capability_permissions(id,subject_type,subject,capability,effect,"
                "scope_json,priority,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (pid, subject_type, subject, capability, effect,
                 json.dumps(scope or {}, ensure_ascii=False), priority,
                 1 if enabled else 0, now, now),
            )
        c.commit()
        row = c.execute("SELECT * FROM capability_permissions WHERE id=?", (pid,)).fetchone()
    return dict(row)


def delete_permission(permission_id: str) -> None:
    with closing(store.conn()) as c:
        c.execute("DELETE FROM capability_permissions WHERE id=?", (permission_id,))
        c.commit()


_SUBJECT_WEIGHT = {"*": 0, "source": 1, "actor": 2, "user": 3}


def decide_permission(*, capability: str, actor: str, source: str,
                      user_id: str | None = None) -> dict[str, Any]:
    values = {"source": source, "actor": actor, "user": user_id or "", "*": "*"}
    best: tuple[int, dict[str, Any]] | None = None
    for row in permission_rows():
        if not row.get("enabled"):
            continue
        if row.get("capability") not in (capability, "*"):
            continue
        st = str(row.get("subject_type") or "")
        if st not in _SUBJECT_WEIGHT:
            continue
        if row.get("subject") not in ("*", values.get(st, "")):
            continue
        score = _SUBJECT_WEIGHT[st] * 100
        if row.get("capability") == capability:
            score += 10
        score += int(row.get("priority") or 0)
        if best is None or score > best[0] or (
                score == best[0] and row.get("effect") == "deny"):
            best = (score, row)
    if best is None:
        default_allow = bool(_config().get("default_allow", True))
        return {"allow": default_allow, "source": "default", "permission": None}
    row = best[1]
    return {"allow": row.get("effect") == "allow", "source": "table", "permission": row}


def provider_rows() -> list[dict[str, Any]]:
    with closing(store.conn()) as c:
        rows = c.execute(
            "SELECT * FROM capability_providers ORDER BY capability,priority,provider"
        ).fetchall()
    return [dict(r) for r in rows]


def set_provider(*, capability: str, provider: str, priority: int = 100,
                 enabled: bool = True, constraints: dict[str, Any] | None = None) -> dict[str, Any]:
    import json
    with closing(store.conn()) as c:
        c.execute(
            "INSERT INTO capability_providers(capability,provider,priority,enabled,"
            "constraints_json,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(capability,provider) DO UPDATE SET priority=excluded.priority,"
            "enabled=excluded.enabled,constraints_json=excluded.constraints_json,"
            "updated_at=excluded.updated_at",
            (capability, provider, priority, 1 if enabled else 0,
             json.dumps(constraints or {}, ensure_ascii=False), time.time()),
        )
        c.commit()
        row = c.execute(
            "SELECT * FROM capability_providers WHERE capability=? AND provider=?",
            (capability, provider),
        ).fetchone()
    return dict(row)


def choose_provider(capability: str, requested: str | None = None) -> dict[str, Any]:
    rows = [r for r in provider_rows() if r.get("capability") == capability and r.get("enabled")]
    if not rows:
        raise CapabilityError("provider_unavailable", f"{capability} 没有启用的 Provider", 503)
    if requested:
        exact = next((r for r in rows if r.get("provider") == requested), None)
        if exact:
            return exact
    return min(rows, key=lambda r: (int(r.get("priority") or 100), str(r.get("provider"))))


def retention_policy() -> dict[str, Any]:
    cfg = _config()
    return {
        "retention_days": int(cfg.get("retention_days") or 30),
        "max_records": int(cfg.get("max_records") or 10000),
    }


def prune_invocations(*, retention_days: int | None = None,
                      max_records: int | None = None) -> dict[str, Any]:
    policy = retention_policy()
    days = policy["retention_days"] if retention_days is None else int(retention_days)
    limit = policy["max_records"] if max_records is None else int(max_records)
    cutoff = time.time() - max(1, days) * 86400
    deleted_age = 0
    deleted_excess = 0
    with closing(store.conn()) as c:
        cur = c.execute("DELETE FROM capability_invocations WHERE created_at < ?", (cutoff,))
        deleted_age = cur.rowcount or 0
        # keep the newest N rows (portable across sqlite/postgres)
        excess = c.execute(
            "SELECT id FROM capability_invocations ORDER BY created_at DESC"
        ).fetchall()
        ids = [r["id"] for r in excess][max(1, limit):]
        if ids:
            marks = ",".join("?" for _ in ids)
            cur = c.execute(f"DELETE FROM capability_invocations WHERE id IN ({marks})", tuple(ids))
            deleted_excess = cur.rowcount or 0
        c.commit()
    return {"deleted_by_age": deleted_age, "deleted_by_count": deleted_excess,
            "retention_days": days, "max_records": limit}



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

    def _check_permission(self, capability: str, *, actor: str, source: str,
                          user_id: str | None) -> None:
        decision = decide_permission(capability=capability, actor=actor,
                                     source=source, user_id=user_id)
        if not decision["allow"]:
            store.audit(actor, "capability.denied", {
                "capability": capability, "source": source, "user_id": user_id,
                "permission": (decision.get("permission") or {}).get("id"),
            }, result="deny", source=source)
            raise CapabilityError("capability_permission_denied",
                                  f"{capability} 被权限表拒绝", 403,
                                  {"permission": decision.get("permission")})

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
        self._check_permission("image.generate", actor=actor, source=source, user_id=user_id)
        provider = choose_provider("image.generate")
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
                      workflow=wf, provider=str(provider.get("provider")))
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
        self._check_permission("voice.synthesize", actor=actor, source=source, user_id=user_id)
        chosen = choose_provider("voice.synthesize", requested=engine)
        provider = str(chosen.get("provider"))
        if provider in {"kokoro", "gpt-sovits"}:
            engine = provider
        ch = self._character(character_key, character)
        if (ch.get("media_policy") or {}).get("allow_voice") is False:
            raise CapabilityError("media_policy_denied", "角色未开放语音", 403)
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
