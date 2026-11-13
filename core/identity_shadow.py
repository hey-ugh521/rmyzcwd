"""Phase 4F-2B shadow event data model.

4F-2B only observes. This module owns the shadow event table, insertion and
bounded retention. It must never create identities or change authorization.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from . import capabilities, identity, store

SHADOW_EVENT_FIELDS = {
    "ts",
    "request_id",
    "method",
    "path",
    "route_id",
    "route_action",
    "resource_type",
    "resource_id",
    "legacy_authenticated",
    "legacy_actor",
    "identity_state",
    "evaluation_state",
    "policy_domain",
    "principal_id",
    "credential_id",
    "role",
    "action",
    "policy_result",
    "policy_version",
    "reason",
    "error_class",
    "latency_ms",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS identity_shadow_events(
  id TEXT PRIMARY KEY,
  ts REAL NOT NULL,
  request_id TEXT,
  method TEXT,
  path TEXT,
  route_id TEXT,
  route_action TEXT,
  resource_type TEXT,
  resource_id TEXT,
  legacy_authenticated INTEGER,
  legacy_actor TEXT,
  identity_state TEXT,
  evaluation_state TEXT,
  policy_domain TEXT,
  principal_id TEXT,
  credential_id TEXT,
  role TEXT,
  action TEXT,
  policy_result TEXT,
  policy_version TEXT,
  reason TEXT,
  error_class TEXT,
  latency_ms REAL
);
CREATE INDEX IF NOT EXISTS ix_shadow_events_ts
  ON identity_shadow_events(ts);
CREATE INDEX IF NOT EXISTS ix_shadow_events_request
  ON identity_shadow_events(request_id);
CREATE INDEX IF NOT EXISTS ix_shadow_events_policy_version
  ON identity_shadow_events(policy_version);
CREATE INDEX IF NOT EXISTS ix_shadow_events_evaluation
  ON identity_shadow_events(evaluation_state, ts);
"""


@contextmanager
def _tx() -> Iterator[Any]:
    c = store.conn()
    try:
        if store.DB_BACKEND != "pg":
            c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        c.close()


def init_schema() -> None:
    with store.conn() as c:
        c.executescript(SCHEMA)


def _row(row) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def record_shadow_event(**fields: Any) -> dict[str, Any]:
    """Insert one observation.

    Unknown fields are rejected on purpose: callers must not pass Authorization
    headers, raw keys, token hashes, or arbitrary request dumps.
    """
    unknown = set(fields) - SHADOW_EVENT_FIELDS
    if unknown:
        raise ValueError(f"unknown shadow event fields: {sorted(unknown)}")
    event_id = "shadow_" + uuid.uuid4().hex[:16]
    ts = float(fields.pop("ts", time.time()))
    values = {
        "request_id": None,
        "method": None,
        "path": None,
        "route_id": None,
        "route_action": None,
        "resource_type": None,
        "resource_id": None,
        "legacy_authenticated": None,
        "legacy_actor": None,
        "identity_state": None,
        "evaluation_state": None,
        "policy_domain": None,
        "principal_id": None,
        "credential_id": None,
        "role": None,
        "action": None,
        "policy_result": None,
        "policy_version": None,
        "reason": None,
        "error_class": None,
        "latency_ms": None,
    }
    values.update(fields)
    if values["legacy_authenticated"] is not None:
        values["legacy_authenticated"] = 1 if values["legacy_authenticated"] else 0
    with _tx() as c:
        c.execute(
            "INSERT INTO identity_shadow_events(id,ts,request_id,method,path,"
            "route_id,route_action,resource_type,resource_id,legacy_authenticated,"
            "legacy_actor,identity_state,evaluation_state,policy_domain,principal_id,"
            "credential_id,role,action,policy_result,policy_version,reason,error_class,"
            "latency_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, ts, values["request_id"], values["method"], values["path"],
             values["route_id"], values["route_action"], values["resource_type"],
             values["resource_id"], values["legacy_authenticated"],
             values["legacy_actor"], values["identity_state"], values["evaluation_state"],
             values["policy_domain"], values["principal_id"], values["credential_id"],
             values["role"], values["action"], values["policy_result"],
             values["policy_version"], values["reason"], values["error_class"],
             values["latency_ms"]),
        )
        row = c.execute("SELECT * FROM identity_shadow_events WHERE id=?", (event_id,)).fetchone()
    return _row(row) or {}


def list_shadow_events(*, limit: int = 100, request_id: str | None = None) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 1000))
    with store.conn() as c:
        if request_id:
            rows = c.execute(
                "SELECT * FROM identity_shadow_events WHERE request_id=? ORDER BY ts DESC LIMIT ?",
                (request_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM identity_shadow_events ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def prune_shadow_events(*, max_age_days: int = 30, max_rows: int = 10000,
                        batch_size: int = 500, now: float | None = None) -> int:
    """Delete at most one batch: age first, then oldest excess rows.

    The caller may invoke this repeatedly; 4F-2B startup does one bounded batch.
    This function only touches identity_shadow_events.
    """
    if max_age_days < 0 or max_rows < 0:
        raise ValueError("max_age_days/max_rows must be >= 0")
    batch_size = max(1, min(int(batch_size), 500))
    now = float(now if now is not None else time.time())
    cutoff = now - max_age_days * 86400
    deleted = 0
    with _tx() as c:
        cur = c.execute(
            "DELETE FROM identity_shadow_events WHERE id IN ("
            "SELECT id FROM identity_shadow_events WHERE ts < ? ORDER BY ts ASC LIMIT ?)",
            (cutoff, batch_size),
        )
        deleted += int(cur.rowcount or 0)
        if deleted < batch_size:
            remaining = batch_size - deleted
            total = int(c.execute(
                "SELECT COUNT(*) AS n FROM identity_shadow_events").fetchone()["n"])
            excess = max(0, total - max_rows)
            if excess:
                cur = c.execute(
                    "DELETE FROM identity_shadow_events WHERE id IN ("
                    "SELECT id FROM identity_shadow_events ORDER BY ts ASC LIMIT ?)",
                    (min(remaining, excess),),
                )
                deleted += int(cur.rowcount or 0)
    return deleted

# ---------------------------------------------------------------- observation

ROUTE_EVALUATOR_VERSION = "route-policy-v1"
CAPABILITY_EVALUATOR_VERSION = "capability-policy-v1"
POLICY_SCHEMA_VERSION = 1

# Route authorization v1 is deliberately narrow. Capability invocation is not
# evaluated in 4F-2B; it records not_evaluated.
ROUTE_POLICY: dict[str, tuple[str, ...]] = {
    "admin": ("read", "write"),
    "operator": ("read",),
    "service": (),
}


@dataclass(frozen=True)
class RouteContext:
    covered: bool
    route_id: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class LegacyAuthObservation:
    authenticated: bool
    actor: str | None = None
    reason: str = "not_applicable"


def _method_action(method: str) -> str | None:
    m = method.upper()
    if m in ("GET", "HEAD", "OPTIONS"):
        return "read"
    if m in ("POST", "PUT", "PATCH", "DELETE"):
        return "write"
    return None


def resolve_route(method: str, path: str) -> RouteContext:
    action = _method_action(method)
    p = (path or "/").split("?", 1)[0].rstrip("/") or "/"
    if action is None:
        return RouteContext(False, reason="unsupported_method")
    if p == "/api/registry" or p.startswith("/api/registry/"):
        tail = p[len("/api/registry"):].strip("/")
        parts = [x for x in tail.split("/") if x]
        resource_type = parts[0] if parts else None
        resource_id = parts[1] if len(parts) > 1 else None
        return RouteContext(True, route_id=f"registry.{action}", action=action,
                            resource_type=resource_type, resource_id=resource_id)
    if p.startswith("/api/capabilities"):
        if p.startswith("/api/capabilities/invocations"):
            return RouteContext(False, reason="capability_invocation_not_in_scope")
        if p.startswith("/api/capabilities/permissions"):
            return RouteContext(True, route_id=f"policy.permissions.{action}",
                                action=action, resource_type="permission")
        if p.startswith("/api/capabilities/provider-health"):
            return RouteContext(True, route_id="provider.read", action="read",
                                resource_type="provider")
        if p.startswith("/api/capabilities/providers"):
            return RouteContext(True, route_id=f"provider.{action}", action=action,
                                resource_type="provider")
        if p.startswith("/api/capabilities/maintenance"):
            return RouteContext(True, route_id="policy.maintenance.write", action="write",
                                resource_type="policy")
        return RouteContext(False, reason="capability_route_not_in_scope")
    if p == "/api/policy" or p.startswith("/api/policy/"):
        return RouteContext(True, route_id=f"policy.{action}", action=action,
                            resource_type="policy")
    return RouteContext(False, reason="route_not_in_scope")


def _canonical_policy_snapshot() -> dict[str, Any]:
    try:
        rows = capabilities.permission_rows()
    except Exception:  # noqa: BLE001 - schema may not exist yet
        rows = []
    # Stable array ordering by permission id; all fields are explicit.
    permissions = sorted(
        [{k: row.get(k) for k in sorted(row.keys())} for row in rows],
        key=lambda r: str(r.get("id") or ""),
    )
    cap_cfg = store.CFG.get("capabilities") or {}
    return {
        "route_policy": {role: list(actions) for role, actions in sorted(ROUTE_POLICY.items())},
        "capability_policy": {
            "permissions": permissions,
            "default_allow": bool(cap_cfg.get("default_allow", True)),
            "subject_weights": dict(sorted(
                getattr(capabilities, "_SUBJECT_WEIGHT", {
                    "source": 1, "actor": 2, "user": 3, "*": 0,
                }).items()
            )),
            "scope_rule_version": "scope-v1",
        },
        "policy_schema_version": POLICY_SCHEMA_VERSION,
        "evaluator_versions": {
            "route": ROUTE_EVALUATOR_VERSION,
            "capability": CAPABILITY_EVALUATOR_VERSION,
        },
    }


def policy_snapshot() -> dict[str, Any]:
    return _canonical_policy_snapshot()


def policy_version() -> str:
    canonical = json.dumps(
        _canonical_policy_snapshot(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def route_decision(role: str, action: str) -> str:
    return "allow" if action in ROUTE_POLICY.get(role, ()) else "deny"


def _route_decision(role: str, action: str) -> str:
    return route_decision(role, action)


def evaluate_shadow(*, method: str, path: str, request_id: str | None = None,
                    bearer_token: str | None = None,
                    legacy_auth: LegacyAuthObservation | None = None) -> dict[str, Any]:
    """Compute one observation without writing identity or shadow rows."""
    route = resolve_route(method, path)
    event: dict[str, Any] = {
        "request_id": request_id,
        "method": method,
        "path": path,
        "route_id": route.route_id,
        "route_action": route.action,
        "resource_type": route.resource_type,
        "resource_id": route.resource_id,
        "policy_version": policy_version(),
        "policy_domain": "none",
        "legacy_authenticated": bool(legacy_auth and legacy_auth.authenticated),
        "legacy_actor": legacy_auth.actor if legacy_auth else None,
    }
    if not route.covered:
        event.update(identity_state=None, evaluation_state="not_evaluated",
                     policy_result="not_evaluated", reason=route.reason)
        return event
    if legacy_auth is None:
        event.update(identity_state=None, evaluation_state="insufficient_context",
                     policy_result="insufficient_context", reason="legacy_observation_missing")
        return event
    if not legacy_auth.authenticated:
        state = "legacy_denied" if legacy_auth.reason == "denied" else "legacy_anonymous"
        event.update(identity_state=state, evaluation_state="insufficient_context",
                     policy_result="insufficient_context",
                     reason=f"legacy_{legacy_auth.reason}")
        return event
    if not bearer_token:
        event.update(identity_state="unmapped", evaluation_state="insufficient_context",
                     policy_result="insufficient_context", reason="bearer_missing")
        return event

    inspected = identity.inspect_legacy_api_key(bearer_token)
    state = str(inspected.get("state") or "unmapped")
    principal = inspected.get("principal") or {}
    credential = inspected.get("credential") or {}
    event.update(identity_state=state, principal_id=principal.get("id"),
                 credential_id=credential.get("id"), role=principal.get("role"))
    if state != "resolved":
        event.update(evaluation_state="insufficient_context",
                     policy_result="insufficient_context", reason=state)
        return event

    role = str(principal.get("role") or "")
    action = str(route.action or "")
    result = _route_decision(role, action)
    event.update(evaluation_state="evaluated", policy_domain="route",
                 action=action, policy_result=result)
    return event
