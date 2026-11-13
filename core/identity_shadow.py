"""Phase 4F-2B shadow event data model.

4F-2B only observes. This module owns the shadow event table, insertion and
bounded retention. It must never create identities or change authorization.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from . import store

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
