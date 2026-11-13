"""Capability Registry infrastructure (Phase 0).

This module owns the generic resource/revision lifecycle only. It does not
mount UI, call Agent/Gateway, or manage media tasks. Existing resource payloads
(character/voice/workflow) stay opaque JSON in a revision snapshot.

Design contract:
  docs/aegis/plans/2026-11-13-capability-registry-contract.md
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from . import store

REGISTRY_SCHEMA_VERSION = 1
RESOURCE_TYPES = {"character", "voice_profile", "workflow"}
RESOURCE_STATUSES = {"active", "disabled", "archived", "deleted"}
REVISION_STATES = {"draft", "published", "superseded"}


class RegistryError(Exception):
    """Structured registry error; API maps this to a stable JSON body."""

    def __init__(self, code: str, message: str, status_code: int = 400,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message,
                          "details": self.details}}


def _now() -> float:
    return time.time()


def _uid() -> str:
    return uuid.uuid4().hex


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _checksum(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _loads(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise RegistryError("invalid_snapshot", f"revision snapshot 不是合法 JSON: {exc}") from exc


def _freeze(value: Any) -> Any:
    """Deeply freeze a snapshot so RequestContext callers cannot mutate it."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, tuple):
        return tuple(_freeze(v) for v in value)
    return value


def _assert_type(resource_type: str) -> None:
    if resource_type not in RESOURCE_TYPES:
        raise RegistryError("invalid_resource_type",
                            f"未知资源类型 {resource_type!r}",
                            422, {"allowed": sorted(RESOURCE_TYPES)})


def _assert_id(resource_id: str) -> None:
    if not resource_id or len(resource_id) > 128 or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
        for c in resource_id
    ):
        raise RegistryError("invalid_resource_id", "资源 id 只能包含字母、数字、_、-，长度 1..128", 422)


def _assert_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RegistryError("invalid_payload", "payload 必须是 JSON object", 422)
    return payload


def _pg_ddl(sql: str) -> str:
    """Keep the DDL portable: sqlite REAL is float8, PG REAL is float4."""
    return sql.replace("REAL", "DOUBLE PRECISION")


@contextmanager
def _tx() -> Iterator[Any]:
    c = store.conn()
    try:
        if store.DB_BACKEND == "pg":
            # psycopg starts a transaction on first statement.
            pass
        else:
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("BEGIN IMMEDIATE")
        yield c
        c.commit()
    except Exception:
        with_context = getattr(c, "rollback", None)
        if with_context:
            try:
                c.rollback()
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        c.close()


def _row(c, sql: str, args: tuple) -> Any:
    return c.execute(sql, args).fetchone()


def _rows(c, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def _resource_row(c, resource_type: str, resource_id: str,
                  *, lock: bool = False) -> dict[str, Any] | None:
    sql = "SELECT * FROM registry_resources WHERE type=? AND id=?"
    if lock and store.DB_BACKEND == "pg":
        sql += " FOR UPDATE"
    row = _row(c, sql, (resource_type, resource_id))
    return dict(row) if row else None


def _revision_row(c, resource_type: str, resource_id: str,
                  revision: int) -> dict[str, Any] | None:
    row = _row(c,
               "SELECT * FROM registry_revisions WHERE type=? AND id=? AND revision=?",
               (resource_type, resource_id, int(revision)))
    return dict(row) if row else None


def _audit(c, *, action: str, resource_type: str, resource_id: str,
           revision: int | None, actor: str, result: str = "ok",
           detail: dict[str, Any] | None = None, error: str | None = None,
           request_id: str | None = None) -> None:
    c.execute(
        "INSERT INTO registry_audit(id,ts,action,resource_type,resource_id,"
        "revision,actor,result,detail,error,request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (_uid(), _now(), action, resource_type, resource_id, revision, actor,
         result, _canonical(detail or {}), error, request_id),
    )


def init_schema(*, _after_ddl: Any = None) -> None:
    """Create registry tables/triggers idempotently.

    The revision trigger is the DB-level immutability boundary: application code
    can only add revisions; snapshot/checksum/revision identity cannot be
    updated or deleted in place.

    `_after_ddl` is an internal transaction-failure seam used by the migration
    recovery test; production callers must omit it.
    """
    tables = [
        """
        CREATE TABLE IF NOT EXISTS registry_resources(
          type TEXT NOT NULL,
          id TEXT NOT NULL,
          status TEXT NOT NULL,
          current_revision INTEGER NOT NULL DEFAULT 0,
          published_revision INTEGER,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL,
          created_by TEXT,
          updated_by TEXT,
          PRIMARY KEY(type,id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS registry_revisions(
          type TEXT NOT NULL,
          id TEXT NOT NULL,
          revision INTEGER NOT NULL,
          snapshot TEXT NOT NULL,
          checksum TEXT NOT NULL,
          state TEXT NOT NULL,
          created_at REAL NOT NULL,
          created_by TEXT,
          change_note TEXT,
          parent_revision INTEGER,
          PRIMARY KEY(type,id,revision),
          FOREIGN KEY(type,id) REFERENCES registry_resources(type,id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS registry_audit(
          id TEXT PRIMARY KEY,
          ts REAL NOT NULL,
          action TEXT NOT NULL,
          resource_type TEXT NOT NULL,
          resource_id TEXT NOT NULL,
          revision INTEGER,
          actor TEXT,
          result TEXT NOT NULL,
          detail TEXT NOT NULL,
          error TEXT,
          request_id TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS registry_idempotency(
          key TEXT PRIMARY KEY,
          operation TEXT NOT NULL,
          resource_type TEXT NOT NULL,
          resource_id TEXT,
          request_hash TEXT NOT NULL,
          response_json TEXT NOT NULL,
          created_at REAL NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS registry_references(
          source_type TEXT NOT NULL,
          source_id TEXT NOT NULL,
          target_type TEXT NOT NULL,
          target_id TEXT NOT NULL,
          created_at REAL NOT NULL,
          PRIMARY KEY(source_type,source_id,target_type,target_id),
          FOREIGN KEY(source_type,source_id) REFERENCES registry_resources(type,id) ON DELETE CASCADE,
          FOREIGN KEY(target_type,target_id) REFERENCES registry_resources(type,id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS registry_voice_assets(
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          filename TEXT NOT NULL,
          stored_path TEXT NOT NULL,
          checksum TEXT NOT NULL,
          size INTEGER NOT NULL,
          mime TEXT,
          source TEXT,
          consent TEXT,
          reuse_policy TEXT NOT NULL,
          created_at REAL NOT NULL,
          deleted INTEGER NOT NULL DEFAULT 0
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_registry_resources_type ON registry_resources(type,status,updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_registry_revisions_type ON registry_revisions(type,id,revision DESC)",
        "CREATE INDEX IF NOT EXISTS idx_registry_refs_target ON registry_references(target_type,target_id)",
        # exactly one published revision per resource
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_registry_published_one "
        "ON registry_revisions(type,id) WHERE state='published'",
    ]
    if store.DB_BACKEND == "pg":
        tables = [_pg_ddl(t) for t in tables]
    with _tx() as c:
        for stmt in tables:
            c.execute(stmt)
        if store.DB_BACKEND == "pg":
            c.execute("""
                CREATE OR REPLACE FUNCTION registry_revision_immutable()
                RETURNS trigger AS $$
                BEGIN
                  IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'registry revision is immutable';
                  END IF;
                  IF NEW.snapshot IS DISTINCT FROM OLD.snapshot
                     OR NEW.checksum IS DISTINCT FROM OLD.checksum
                     OR NEW.type IS DISTINCT FROM OLD.type
                     OR NEW.id IS DISTINCT FROM OLD.id
                     OR NEW.revision IS DISTINCT FROM OLD.revision THEN
                    RAISE EXCEPTION 'registry revision is immutable';
                  END IF;
                  RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
            """)
            c.execute("DROP TRIGGER IF EXISTS trg_registry_revision_immutable ON registry_revisions")
            c.execute("""
                CREATE TRIGGER trg_registry_revision_immutable
                BEFORE UPDATE OR DELETE ON registry_revisions
                FOR EACH ROW EXECUTE FUNCTION registry_revision_immutable()
            """)
            c.execute("""
                CREATE OR REPLACE FUNCTION registry_published_valid()
                RETURNS trigger AS $$
                BEGIN
                  IF NEW.published_revision IS NOT NULL AND NOT EXISTS (
                    SELECT 1 FROM registry_revisions
                    WHERE type = NEW.type AND id = NEW.id
                      AND revision = NEW.published_revision
                  ) THEN
                    RAISE EXCEPTION 'published revision does not exist';
                  END IF;
                  RETURN NEW;
                END;
                $$ LANGUAGE plpgsql
            """)
            c.execute("DROP TRIGGER IF EXISTS trg_registry_published_valid ON registry_resources")
            c.execute("""
                CREATE TRIGGER trg_registry_published_valid
                BEFORE UPDATE OF published_revision ON registry_resources
                FOR EACH ROW EXECUTE FUNCTION registry_published_valid()
            """)
        else:
            c.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_registry_revision_immutable
                BEFORE UPDATE OF type,id,revision,snapshot,checksum ON registry_revisions
                BEGIN
                  SELECT RAISE(ABORT, 'registry revision is immutable');
                END
            """)
            c.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_registry_revision_no_delete
                BEFORE DELETE ON registry_revisions
                BEGIN
                  SELECT RAISE(ABORT, 'registry revision is immutable');
                END
            """)
            c.execute("""
                CREATE TRIGGER IF NOT EXISTS trg_registry_published_valid
                BEFORE UPDATE OF published_revision ON registry_resources
                WHEN NEW.published_revision IS NOT NULL
                BEGIN
                  SELECT RAISE(ABORT, 'published revision does not exist')
                  WHERE NOT EXISTS (
                    SELECT 1 FROM registry_revisions
                    WHERE type = NEW.type AND id = NEW.id
                      AND revision = NEW.published_revision
                  );
                END
            """)
        c.execute("CREATE TABLE IF NOT EXISTS schema_meta(k TEXT PRIMARY KEY, v TEXT)")
        if _after_ddl is not None:
            _after_ddl(c)
        row = _row(c, "SELECT v FROM schema_meta WHERE k='registry_schema_version'", ())
        if row is None:
            c.execute("INSERT INTO schema_meta(k,v) VALUES(?,?)",
                      ("registry_schema_version", str(REGISTRY_SCHEMA_VERSION)))
        else:
            c.execute("UPDATE schema_meta SET v=? WHERE k='registry_schema_version'",
                      (str(REGISTRY_SCHEMA_VERSION),))


@dataclass(frozen=True)
class ResolvedResource:
    resource_type: str
    resource_id: str
    revision: int
    checksum: str
    snapshot: Mapping[str, Any]
    resolved_at: float = field(default_factory=_now)


class RequestContext:
    """Request-scoped revision lock.

    Phase 0 only defines the contract. Runtime integration is intentionally
    deferred; callers can create one context per request and resolve resources
    once.
    """

    def __init__(self, request_id: str | None = None) -> None:
        self.request_id = request_id or f"req_{_uid()[:12]}"
        self._resolved: dict[tuple[str, str], ResolvedResource] = {}

    def resolve(self, service: "RegistryService", resource_type: str,
                resource_id: str) -> ResolvedResource:
        key = (resource_type, resource_id)
        if key not in self._resolved:
            self._resolved[key] = service.resolve(resource_type, resource_id,
                                                  request_id=self.request_id)
        return self._resolved[key]


class RegistryService:
    # ------------------------------------------------------------------ create

    def create(self, resource_type: str, resource_id: str, payload: dict[str, Any],
               *, actor: str = "system", change_note: str = "",
               idempotency_key: str | None = None,
               request_id: str | None = None) -> dict[str, Any]:
        _assert_type(resource_type)
        _assert_id(resource_id)
        payload = _assert_payload(payload)
        request_hash = _checksum({"type": resource_type, "id": resource_id, "payload": payload})
        if idempotency_key:
            existing = self._idempotent(idempotency_key, "create", request_hash)
            if existing is not None:
                return existing
        snapshot = _canonical(payload)
        digest = _checksum(payload)
        ts = _now()
        with _tx() as c:
            if _resource_row(c, resource_type, resource_id, lock=True):
                raise RegistryError("resource_exists", "资源已存在", 409,
                                    {"resource_type": resource_type, "resource_id": resource_id})
            c.execute(
                "INSERT INTO registry_resources(type,id,status,current_revision,"
                "published_revision,created_at,updated_at,created_by,updated_by) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (resource_type, resource_id, "active", 1, None, ts, ts, actor, actor),
            )
            c.execute(
                "INSERT INTO registry_revisions(type,id,revision,snapshot,checksum,state,"
                "created_at,created_by,change_note,parent_revision) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (resource_type, resource_id, 1, snapshot, digest, "draft", ts, actor,
                 change_note, None),
            )
            _audit(c, action="create", resource_type=resource_type,
                   resource_id=resource_id, revision=1, actor=actor,
                   detail={"checksum": digest}, request_id=request_id)
        result = self.get(resource_type, resource_id, include_snapshot=True)
        if idempotency_key:
            self._idempotent_store(idempotency_key, "create", resource_type,
                                   resource_id, request_hash, result)
        return result

    def update(self, resource_type: str, resource_id: str, payload: dict[str, Any],
               *, expected_revision: int, actor: str = "system",
               change_note: str = "", request_id: str | None = None) -> dict[str, Any]:
        _assert_type(resource_type)
        payload = _assert_payload(payload)
        snapshot = _canonical(payload)
        digest = _checksum(payload)
        with _tx() as c:
            row = _resource_row(c, resource_type, resource_id, lock=True)
            if row is None:
                raise RegistryError("resource_not_found", "资源不存在", 404)
            if row["status"] in {"archived", "deleted"}:
                raise RegistryError("resource_not_editable", "已归档/删除资源不可编辑", 409,
                                    {"status": row["status"]})
            if int(row["current_revision"]) != int(expected_revision):
                raise RegistryError(
                    "revision_conflict", "资源已被其他编辑更新", 409,
                    {"expected": expected_revision, "current": row["current_revision"]},
                )
            revision = int(row["current_revision"]) + 1
            ts = _now()
            c.execute(
                "INSERT INTO registry_revisions(type,id,revision,snapshot,checksum,state,"
                "created_at,created_by,change_note,parent_revision) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (resource_type, resource_id, revision, snapshot, digest, "draft", ts,
                 actor, change_note, int(row["current_revision"])),
            )
            c.execute(
                "UPDATE registry_resources SET current_revision=?,updated_at=?,updated_by=? "
                "WHERE type=? AND id=? AND current_revision=?",
                (revision, ts, actor, resource_type, resource_id, int(row["current_revision"])),
            )
            _audit(c, action="update", resource_type=resource_type,
                   resource_id=resource_id, revision=revision, actor=actor,
                   detail={"expected": expected_revision, "checksum": digest},
                   request_id=request_id)
        return self.get(resource_type, resource_id, include_snapshot=True)

    # ----------------------------------------------------------------- publish

    def publish(self, resource_type: str, resource_id: str, revision: int,
                *, expected_current_revision: int,
                expected_published_revision: int | None,
                actor: str = "system", change_note: str = "",
                request_id: str | None = None) -> dict[str, Any]:
        _assert_type(resource_type)
        with _tx() as c:
            row = _resource_row(c, resource_type, resource_id, lock=True)
            if row is None:
                raise RegistryError("resource_not_found", "资源不存在", 404)
            if row["status"] != "active":
                raise RegistryError("resource_not_active", "只有 active 资源可以发布", 409,
                                    {"status": row["status"]})
            if int(row["current_revision"]) != int(expected_current_revision):
                raise RegistryError("revision_conflict", "资源当前版本已变化", 409,
                                    {"expected": expected_current_revision,
                                     "current": row["current_revision"]})
            if row["published_revision"] != expected_published_revision:
                raise RegistryError("publish_conflict", "资源发布指针已变化", 409,
                                    {"expected": expected_published_revision,
                                     "current": row["published_revision"]})
            target = _revision_row(c, resource_type, resource_id, int(revision))
            if target is None:
                raise RegistryError("revision_not_found", "目标 revision 不存在", 404)
            if _checksum(_loads(target["snapshot"])) != target["checksum"]:
                raise RegistryError("revision_corrupt", "revision checksum 不匹配", 409)
            old = row["published_revision"]
            ts = _now()
            if old is not None and int(old) != int(revision):
                c.execute(
                    "UPDATE registry_revisions SET state='superseded' "
                    "WHERE type=? AND id=? AND revision=? AND revision!=?",
                    (resource_type, resource_id, int(old), int(revision)),
                )
            c.execute(
                "UPDATE registry_revisions SET state='published' "
                "WHERE type=? AND id=? AND revision=?",
                (resource_type, resource_id, int(revision)),
            )
            c.execute(
                "UPDATE registry_resources SET published_revision=?,updated_at=?,updated_by=?,"
                "current_revision=current_revision WHERE type=? AND id=?",
                (int(revision), ts, actor, resource_type, resource_id),
            )
            _audit(c, action="publish", resource_type=resource_type,
                   resource_id=resource_id, revision=int(revision), actor=actor,
                   detail={"old_published": old, "checksum": target["checksum"],
                           "change_note": change_note}, request_id=request_id)
        return self.get(resource_type, resource_id, include_snapshot=True)

    def rollback(self, resource_type: str, resource_id: str, revision: int,
                 *, expected_current_revision: int,
                 expected_published_revision: int | None,
                 actor: str = "system", request_id: str | None = None) -> dict[str, Any]:
        result = self.publish(
            resource_type, resource_id, revision,
            expected_current_revision=expected_current_revision,
            expected_published_revision=expected_published_revision,
            actor=actor, change_note=f"rollback to revision {revision}",
            request_id=request_id,
        )
        # publish already audited; add explicit rollback marker
        with _tx() as c:
            _audit(c, action="rollback", resource_type=resource_type,
                   resource_id=resource_id, revision=int(revision), actor=actor,
                   detail={"expected_published": expected_published_revision},
                   request_id=request_id)
        return result

    # ---------------------------------------------------------- state changes

    def disable(self, resource_type: str, resource_id: str, *, actor: str = "system",
                expected_revision: int,
                request_id: str | None = None) -> dict[str, Any]:
        return self._set_status(resource_type, resource_id, "disabled", actor,
                                expected_revision=expected_revision,
                                request_id=request_id, allowed={"active"})

    def enable(self, resource_type: str, resource_id: str, *, actor: str = "system",
               expected_revision: int,
               request_id: str | None = None) -> dict[str, Any]:
        return self._set_status(resource_type, resource_id, "active", actor,
                                expected_revision=expected_revision,
                                request_id=request_id, allowed={"disabled"})

    def archive(self, resource_type: str, resource_id: str, *, actor: str = "system",
                expected_revision: int,
                request_id: str | None = None) -> dict[str, Any]:
        return self._set_status(resource_type, resource_id, "archived", actor,
                                expected_revision=expected_revision,
                                request_id=request_id, allowed={"active", "disabled"})

    def delete(self, resource_type: str, resource_id: str, *, actor: str = "system",
               expected_revision: int,
               request_id: str | None = None) -> dict[str, Any]:
        """Soft delete. References are checked under the same transaction lock."""
        with _tx() as c:
            row = _resource_row(c, resource_type, resource_id, lock=True)
            if row is None:
                raise RegistryError("resource_not_found", "资源不存在", 404)
            self._check_expected(row, expected_revision)
            if row["status"] == "deleted":
                raise RegistryError("invalid_status_transition", "资源已删除", 409,
                                    {"status": row["status"]})
            refs = _rows(c,
                         "SELECT source_type,source_id FROM registry_references "
                         "WHERE target_type=? AND target_id=?",
                         (resource_type, resource_id))
            if refs:
                raise RegistryError("reference_exists", "资源仍被引用，不能删除", 409,
                                    {"references": refs})
            self._set_status_locked(c, resource_type, resource_id, "deleted",
                                    actor, request_id, action="delete")
        return self.get(resource_type, resource_id, include_snapshot=True)

    def _set_status(self, resource_type: str, resource_id: str, status: str,
                    actor: str, *, expected_revision: int,
                    request_id: str | None = None,
                    allowed: set[str] | None = None) -> dict[str, Any]:
        with _tx() as c:
            row = _resource_row(c, resource_type, resource_id, lock=True)
            if row is None:
                raise RegistryError("resource_not_found", "资源不存在", 404)
            self._check_expected(row, expected_revision)
            if allowed is not None and row["status"] not in allowed:
                raise RegistryError("invalid_status_transition", "当前状态不允许此操作", 409,
                                    {"status": row["status"], "allowed": sorted(allowed)})
            self._set_status_locked(c, resource_type, resource_id, status,
                                    actor, request_id, action=status)
        return self.get(resource_type, resource_id, include_snapshot=True)

    @staticmethod
    def _check_expected(row: dict[str, Any], expected_revision: int | None) -> None:
        if expected_revision is None:
            raise RegistryError("expected_revision_required",
                                "状态变更必须提供 expected_revision", 422)
        if int(row["current_revision"]) != int(expected_revision):
            raise RegistryError("revision_conflict", "资源当前版本已变化", 409,
                                {"expected": expected_revision,
                                 "current": row["current_revision"]})

    def _set_status_locked(self, c, resource_type: str, resource_id: str, status: str,
                           actor: str, request_id: str | None,
                           *, action: str) -> None:
        ts = _now()
        c.execute(
            "UPDATE registry_resources SET status=?,updated_at=?,updated_by=? "
            "WHERE type=? AND id=?",
            (status, ts, actor, resource_type, resource_id),
        )
        _audit(c, action=action, resource_type=resource_type, resource_id=resource_id,
               revision=None, actor=actor, detail={"status": status},
               request_id=request_id)

    # -------------------------------------------------------------- references

    def add_reference(self, source_type: str, source_id: str,
                      target_type: str, target_id: str,
                      *, actor: str = "system", request_id: str | None = None) -> None:
        _assert_type(source_type)
        _assert_type(target_type)
        with _tx() as c:
            src = _resource_row(c, source_type, source_id, lock=True)
            tgt = _resource_row(c, target_type, target_id, lock=True)
            if src is None or tgt is None:
                raise RegistryError("resource_not_found", "引用源或目标不存在", 404)
            if src["status"] not in {"active", "disabled"} or tgt["status"] != "active":
                raise RegistryError("resource_not_referenceable", "只有 active/disabled 源可以引用 active 目标", 409)
            try:
                c.execute(
                    "INSERT INTO registry_references(source_type,source_id,target_type,target_id,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (source_type, source_id, target_type, target_id, _now()),
                )
            except Exception as exc:  # sqlite3.IntegrityError / psycopg UniqueViolation
                if exc.__class__.__name__ not in {"IntegrityError", "UniqueViolation"}:
                    raise
                raise RegistryError("reference_exists", "引用已存在", 409) from exc
            _audit(c, action="add_reference", resource_type=source_type,
                   resource_id=source_id, revision=None, actor=actor,
                   detail={"target": {"type": target_type, "id": target_id}},
                   request_id=request_id)

    def remove_reference(self, source_type: str, source_id: str,
                         target_type: str, target_id: str,
                         *, actor: str = "system", request_id: str | None = None) -> None:
        with _tx() as c:
            cur = c.execute(
                "DELETE FROM registry_references WHERE source_type=? AND source_id=? "
                "AND target_type=? AND target_id=?",
                (source_type, source_id, target_type, target_id),
            )
            if getattr(cur, "rowcount", 0) == 0:
                raise RegistryError("reference_not_found", "引用不存在", 404)
            _audit(c, action="remove_reference", resource_type=source_type,
                   resource_id=source_id, revision=None, actor=actor,
                   detail={"target": {"type": target_type, "id": target_id}},
                   request_id=request_id)

    # ------------------------------------------------------------------ read

    def resolve(self, resource_type: str, resource_id: str,
                *, request_id: str | None = None) -> ResolvedResource:
        _assert_type(resource_type)
        with _tx() as c:
            row = _resource_row(c, resource_type, resource_id)
            if row is None:
                raise RegistryError("resource_not_found", "资源不存在", 404)
            if row["status"] != "active":
                raise RegistryError("resource_unavailable", "资源当前不可用", 409,
                                    {"status": row["status"]})
            published = row["published_revision"]
            if published is None:
                raise RegistryError("resource_not_published", "资源尚未发布", 409)
            rev = _revision_row(c, resource_type, resource_id, int(published))
            if rev is None:
                raise RegistryError("revision_not_found", "发布 revision 丢失", 409)
            snapshot = _loads(rev["snapshot"])
            if _checksum(snapshot) != rev["checksum"]:
                raise RegistryError("revision_corrupt", "revision checksum 不匹配", 409)
            _audit(c, action="resolve", resource_type=resource_type,
                   resource_id=resource_id, revision=int(published), actor="runtime",
                   detail={"checksum": rev["checksum"]}, request_id=request_id)
            return ResolvedResource(resource_type, resource_id, int(published),
                                    str(rev["checksum"]), _freeze(snapshot))

    def get(self, resource_type: str, resource_id: str,
            *, include_snapshot: bool = False) -> dict[str, Any]:
        _assert_type(resource_type)
        with _tx() as c:
            row = _resource_row(c, resource_type, resource_id)
            if row is None:
                raise RegistryError("resource_not_found", "资源不存在", 404)
            result: dict[str, Any] = {
                "type": row["type"], "id": row["id"], "status": row["status"],
                "current_revision": row["current_revision"],
                "published_revision": row["published_revision"],
                "created_at": row["created_at"], "updated_at": row["updated_at"],
                "created_by": row["created_by"], "updated_by": row["updated_by"],
            }
            if include_snapshot:
                current = _revision_row(c, resource_type, resource_id,
                                        int(row["current_revision"]))
                if current:
                    result["current"] = self._revision_public(current)
                pub = row["published_revision"]
                if pub is not None:
                    published = _revision_row(c, resource_type, resource_id, int(pub))
                    if published:
                        result["published"] = self._revision_public(published)
            return result

    def list(self, resource_type: str, *, status: str | None = None,
             limit: int = 50, offset: int = 0) -> dict[str, Any]:
        _assert_type(resource_type)
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with _tx() as c:
            where = "WHERE type=?"
            args: list[Any] = [resource_type]
            if status:
                where += " AND status=?"
                args.append(status)
            total = _row(c, f"SELECT COUNT(*) AS n FROM registry_resources {where}",
                         tuple(args))
            rows = _rows(c, f"SELECT * FROM registry_resources {where} "
                            "ORDER BY updated_at DESC, id ASC LIMIT ? OFFSET ?",
                         tuple(args + [limit, offset]))
            return {"items": [{k: r[k] for k in (
                "type", "id", "status", "current_revision", "published_revision",
                "created_at", "updated_at")} for r in rows],
                "total": int(total["n"]) if total else 0,
                "limit": limit, "offset": offset}

    def revisions(self, resource_type: str, resource_id: str) -> list[dict[str, Any]]:
        _assert_type(resource_type)
        with _tx() as c:
            if _resource_row(c, resource_type, resource_id) is None:
                raise RegistryError("resource_not_found", "资源不存在", 404)
            rows = _rows(c, "SELECT revision,checksum,state,created_at,created_by,"
                            "change_note,parent_revision FROM registry_revisions "
                            "WHERE type=? AND id=? ORDER BY revision DESC",
                         (resource_type, resource_id))
            return rows

    @staticmethod
    def _revision_public(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "revision": row["revision"], "checksum": row["checksum"],
            "state": row["state"], "created_at": row["created_at"],
            "created_by": row["created_by"], "change_note": row["change_note"],
            "parent_revision": row["parent_revision"],
            "payload": _loads(row["snapshot"]),
        }

    # ------------------------------------------------------------ idempotency

    def _idempotent(self, key: str, operation: str,
                    request_hash: str) -> dict[str, Any] | None:
        with _tx() as c:
            row = _row(c, "SELECT * FROM registry_idempotency WHERE key=?", (key,))
            if row is None:
                return None
            if row["operation"] != operation or row["request_hash"] != request_hash:
                raise RegistryError("idempotency_conflict",
                                    "Idempotency-Key 已用于不同请求", 409,
                                    {"key": key})
            return _loads(row["response_json"])

    def _idempotent_store(self, key: str, operation: str, resource_type: str,
                          resource_id: str, request_hash: str,
                          response: dict[str, Any]) -> None:
        try:
            with _tx() as c:
                c.execute(
                    "INSERT INTO registry_idempotency(key,operation,resource_type,"
                    "resource_id,request_hash,response_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (key, operation, resource_type, resource_id, request_hash,
                     _canonical(response), _now()),
                )
        except Exception as exc:  # sqlite3.IntegrityError / psycopg UniqueViolation
            if exc.__class__.__name__ not in {"IntegrityError", "UniqueViolation"}:
                raise
            # Concurrent same key: another request won; re-read and compare.
            existing = self._idempotent(key, operation, request_hash)
            if existing is None:
                raise RegistryError("idempotency_conflict", "Idempotency-Key 并发冲突", 409)
