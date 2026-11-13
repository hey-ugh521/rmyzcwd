"""Phase 4F-1: identity data model and credential lifecycle.

This module only defines data facts and lifecycle operations. It deliberately
does not change HTTP authentication or route authorization yet.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

from . import store

ROLES = {"admin", "operator", "service"}
KINDS = {"human", "machine", "local"}
CREDENTIAL_KINDS = {"api_key", "service_token", "admin_bootstrap", "recovery"}
TOKEN_RE = re.compile(r"^mob_(cred|boot)_([0-9a-f]{8})_(.+)$")
SESSION_RE = re.compile(r"^mob_sess_([A-Za-z0-9-]+)_(.+)$")
_TOKEN_PREFIX_BYTES = 8
_SECRET_BYTES = 32


class IdentityError(Exception):
    def __init__(self, code: str, message: str, status: int = 400,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def _audit(c, *, actor: str, action: str, detail: dict[str, Any],
           result: str = "ok", error: str | None = None) -> None:
    c.execute(
        "INSERT INTO audit(id,ts,actor,character,action,detail,result,error,task_id,source) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        ("audit_" + uuid.uuid4().hex[:12], time.time(), actor, None, action,
         json.dumps(detail, ensure_ascii=False, default=str), result, error, None, "identity"),
    )


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
    with _tx() as c:
        c.execute("""
        CREATE TABLE IF NOT EXISTS principals(
          id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          display_name TEXT NOT NULL,
          role TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at REAL NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS credentials(
          id TEXT PRIMARY KEY,
          principal_id TEXT NOT NULL,
          kind TEXT NOT NULL,
          lookup_prefix TEXT NOT NULL UNIQUE,
          secret_hash TEXT NOT NULL,
          hash_algo TEXT NOT NULL,
          expires_at REAL,
          revoked_at REAL,
          last_used_at REAL,
          created_at REAL NOT NULL,
          rotated_from TEXT,
          FOREIGN KEY(principal_id) REFERENCES principals(id),
          FOREIGN KEY(rotated_from) REFERENCES credentials(id)
        )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS ix_credentials_principal "
                  "ON credentials(principal_id)")
        c.execute("""
        CREATE TABLE IF NOT EXISTS admin_sessions(
          id TEXT PRIMARY KEY,
          principal_id TEXT NOT NULL,
          token_hash TEXT NOT NULL,
          csrf_hash TEXT NOT NULL,
          created_at REAL NOT NULL,
          expires_at REAL NOT NULL,
          last_seen_at REAL,
          revoked_at REAL,
          FOREIGN KEY(principal_id) REFERENCES principals(id)
        )
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS bootstrap_tokens(
          id TEXT PRIMARY KEY,
          lookup_prefix TEXT NOT NULL UNIQUE,
          token_hash TEXT NOT NULL,
          hash_algo TEXT NOT NULL,
          purpose TEXT NOT NULL,
          principal_id TEXT NOT NULL,
          expires_at REAL NOT NULL,
          consumed_at REAL,
          created_at REAL NOT NULL,
          FOREIGN KEY(principal_id) REFERENCES principals(id)
        )
        """)


def _hash_secret(secret: str) -> str:
    # Credentials are 256-bit random tokens, not human passwords. A salted
    # SHA-256 is sufficient here and keeps per-request authentication fast;
    # human passwords (future) must use scrypt/argon2 instead.
    salt = os.urandom(16)
    digest = hashlib.sha256(salt + secret.encode("utf-8")).digest()
    return "sha256-v1$%s$%s" % (
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def _verify_secret(secret: str, encoded: str) -> bool:
    try:
        algo, salt_b64, hash_b64 = encoded.split("$", 2)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(hash_b64.encode("ascii"))
        if algo == "sha256-v1":
            actual = hashlib.sha256(salt + secret.encode("utf-8")).digest()
            return hmac.compare_digest(actual, expected)
        if algo == "scrypt-v1":
            # Legacy/compat only; credentials created by 4F-1 use sha256-v1.
            n, r, p = 2**14, 8, 1
            actual = hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=n, r=r,
                                    p=p, dklen=len(expected))
            return hmac.compare_digest(actual, expected)
        return False
    except Exception:  # noqa: BLE001
        return False


def _token(prefix_kind: str = "cred") -> tuple[str, str]:
    prefix = secrets.token_hex(_TOKEN_PREFIX_BYTES // 2)
    secret = secrets.token_urlsafe(_SECRET_BYTES)
    return f"mob_{prefix_kind}_{prefix}_{secret}", prefix


def _row(c, sql: str, args: tuple = ()) -> dict[str, Any] | None:
    row = c.execute(sql, args).fetchone()
    return dict(row) if row else None


def _principal(c, principal_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    sql = "SELECT * FROM principals WHERE id=?"
    if lock and store.DB_BACKEND == "pg":
        sql += " FOR UPDATE"
    return _row(c, sql, (principal_id,))


def _public(row: dict[str, Any] | None,
            drop: tuple[str, ...] = ("secret_hash", "token_hash", "csrf_hash")) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: v for k, v in row.items() if k not in drop}


def create_principal(*, kind: str, display_name: str, role: str,
                     actor: str = "cli", principal_id: str | None = None,
                     metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    if kind not in KINDS:
        raise IdentityError("invalid_principal_kind", "principal kind 非法", 422)
    if role not in ROLES:
        raise IdentityError("invalid_role", "role 非法", 422)
    pid = principal_id or f"{kind}-{uuid.uuid4().hex[:8]}"
    now = time.time()
    with _tx() as c:
        if _principal(c, pid) is not None:
            raise IdentityError("principal_exists", "principal 已存在", 409)
        c.execute(
            "INSERT INTO principals(id,kind,display_name,role,enabled,created_at,metadata_json) "
            "VALUES(?,?,?,?,1,?,?)",
            (pid, kind, display_name, role, now,
             json.dumps(metadata or {}, ensure_ascii=False)),
        )
        _audit(c, actor=actor, action="identity.principal.create",
               detail={"principal_id": pid, "kind": kind, "role": role})
        row = _principal(c, pid)
    return row or {}


def list_principals() -> list[dict[str, Any]]:
    with _tx() as c:
        rows = c.execute("SELECT * FROM principals ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def disable_principal(principal_id: str, *, actor: str = "cli") -> dict[str, Any]:
    now = time.time()
    with _tx() as c:
        principal = _principal(c, principal_id, lock=True)
        if principal is None:
            raise IdentityError("principal_not_found", "principal 不存在", 404)
        c.execute("UPDATE principals SET enabled=0 WHERE id=?", (principal_id,))
        c.execute("UPDATE credentials SET revoked_at=? WHERE principal_id=? AND revoked_at IS NULL",
                  (now, principal_id))
        c.execute("UPDATE admin_sessions SET revoked_at=? WHERE principal_id=? AND revoked_at IS NULL",
                  (now, principal_id))
        c.execute("UPDATE bootstrap_tokens SET consumed_at=? WHERE principal_id=? AND consumed_at IS NULL",
                  (now, principal_id))
        _audit(c, actor=actor, action="identity.principal.disable",
               detail={"principal_id": principal_id})
        result = _principal(c, principal_id)
    return result or {}


def create_credential(*, principal_id: str, kind: str = "api_key",
                      expires_at: float | None = None,
                      actor: str = "cli", rotated_from: str | None = None) -> dict[str, Any]:
    if kind not in CREDENTIAL_KINDS:
        raise IdentityError("invalid_credential_kind", "credential kind 非法", 422)
    token, prefix = _token("cred")
    cid = "cred_" + uuid.uuid4().hex[:12]
    now = time.time()
    with _tx() as c:
        principal = _principal(c, principal_id, lock=True)
        if principal is None:
            raise IdentityError("principal_not_found", "principal 不存在", 404)
        if not principal.get("enabled"):
            raise IdentityError("principal_disabled", "principal 已禁用", 409)
        c.execute(
            "INSERT INTO credentials(id,principal_id,kind,lookup_prefix,secret_hash,hash_algo,"
            "expires_at,revoked_at,last_used_at,created_at,rotated_from) "
            "VALUES(?,?,?,?,?,?,?,NULL,NULL,?,?)",
            (cid, principal_id, kind, prefix, _hash_secret(token),
             "sha256-v1", expires_at, now, rotated_from),
        )
        _audit(c, actor=actor, action="identity.credential.create",
               detail={"credential_id": cid, "principal_id": principal_id, "kind": kind,
                       "rotated_from": rotated_from})
        row = _row(c, "SELECT * FROM credentials WHERE id=?", (cid,))
    return {"credential": _public(row), "secret": token}


def authenticate_credential(token: str, *, touch: bool = True) -> dict[str, Any] | None:
    m = TOKEN_RE.match(str(token or ""))
    if not m or m.group(1) != "cred":
        return None
    prefix = m.group(2)
    now = time.time()
    with _tx() as c:
        row = _row(c, "SELECT * FROM credentials WHERE lookup_prefix=?", (prefix,))
        if row is None or row.get("revoked_at"):
            return None
        if row.get("expires_at") is not None and float(row["expires_at"]) <= now:
            return None
        principal = _principal(c, str(row["principal_id"]))
        if principal is None or not principal.get("enabled"):
            return None
        if not _verify_secret(token, str(row["secret_hash"])):
            return None
        if touch:
            last = float(row.get("last_used_at") or 0)
            if now - last > 60:
                c.execute("UPDATE credentials SET last_used_at=? WHERE id=?", (now, row["id"]))
    return {"principal": _public(principal), "credential": _public(row)}


def revoke_credential(credential_id: str, *, actor: str = "cli") -> dict[str, Any]:
    now = time.time()
    with _tx() as c:
        row = _row(c, "SELECT * FROM credentials WHERE id=?", (credential_id,))
        if row is None:
            raise IdentityError("credential_not_found", "credential 不存在", 404)
        c.execute("UPDATE credentials SET revoked_at=? WHERE id=?", (now, credential_id))
        _audit(c, actor=actor, action="identity.credential.revoke",
               detail={"credential_id": credential_id, "principal_id": row["principal_id"]})
        row = _row(c, "SELECT * FROM credentials WHERE id=?", (credential_id,))
    return _public(row) or {}


def rotate_credential(credential_id: str, *, actor: str = "cli") -> dict[str, Any]:
    token, prefix = _token("cred")
    new_id = "cred_" + uuid.uuid4().hex[:12]
    now = time.time()
    with _tx() as c:
        old = _row(c, "SELECT * FROM credentials WHERE id=?", (credential_id,))
        if old is None:
            raise IdentityError("credential_not_found", "credential 不存在", 404)
        principal = _principal(c, str(old["principal_id"]), lock=True)
        if principal is None or not principal.get("enabled"):
            raise IdentityError("principal_disabled", "principal 已禁用", 409)
        c.execute(
            "INSERT INTO credentials(id,principal_id,kind,lookup_prefix,secret_hash,hash_algo,"
            "expires_at,revoked_at,last_used_at,created_at,rotated_from) "
            "VALUES(?,?,?,?,?,?,?,NULL,NULL,?,?)",
            (new_id, old["principal_id"], old["kind"], prefix, _hash_secret(token),
             "sha256-v1", old.get("expires_at"), now, credential_id),
        )
        c.execute("UPDATE credentials SET revoked_at=? WHERE id=?", (now, credential_id))
        _audit(c, actor=actor, action="identity.credential.rotate",
               detail={"old_credential_id": credential_id, "new_credential_id": new_id,
                       "principal_id": old["principal_id"]})
        row = _row(c, "SELECT * FROM credentials WHERE id=?", (new_id,))
    return {"credential": _public(row), "secret": token}


def admin_principals() -> list[dict[str, Any]]:
    return [p for p in list_principals() if p.get("role") == "admin" and p.get("enabled")]


def _create_bootstrap_token(*, purpose: str, principal_id: str, ttl: int,
                            actor: str) -> dict[str, Any]:
    token, prefix = _token("boot")
    tid = "boot_" + uuid.uuid4().hex[:12]
    now = time.time()
    with _tx() as c:
        principal = _principal(c, principal_id, lock=True)
        if principal is None or not principal.get("enabled"):
            raise IdentityError("principal_not_found", "principal 不存在或已禁用", 404)
        c.execute(
            "INSERT INTO bootstrap_tokens(id,lookup_prefix,token_hash,hash_algo,purpose,"
            "principal_id,expires_at,consumed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (tid, prefix, _hash_secret(token), "sha256-v1", purpose, principal_id,
             now + ttl, None, now),
        )
        _audit(c, actor=actor, action="identity.bootstrap.create",
               detail={"purpose": purpose, "principal_id": principal_id,
                       "token_id": tid, "expires_at": now + ttl})
    return {"token": token, "token_id": tid, "principal_id": principal_id,
            "purpose": purpose, "expires_at": now + ttl}


def bootstrap_admin(*, ttl: int = 900, actor: str = "cli") -> dict[str, Any]:
    if any(p.get("role") == "admin" for p in list_principals()):
        raise IdentityError("bootstrap_exists", "已存在 admin principal，请使用 recover", 409)
    principal = create_principal(kind="human", display_name="bootstrap-admin",
                                 role="admin", actor=actor)
    return _create_bootstrap_token(purpose="bootstrap", principal_id=principal["id"],
                                   ttl=ttl, actor=actor)


def recover_admin(*, ttl: int = 900, actor: str = "cli") -> dict[str, Any]:
    admins = admin_principals()
    if not admins:
        raise IdentityError("admin_not_found", "没有启用的 admin principal", 404)
    return _create_bootstrap_token(purpose="recovery", principal_id=admins[0]["id"],
                                   ttl=ttl, actor=actor)


def consume_bootstrap_token(token: str, *, actor: str = "cli") -> dict[str, Any]:
    m = TOKEN_RE.match(str(token or ""))
    if not m or m.group(1) != "boot":
        raise IdentityError("invalid_token", "token 格式非法", 401)
    prefix = m.group(2)
    now = time.time()
    with _tx() as c:
        sql = "SELECT * FROM bootstrap_tokens WHERE lookup_prefix=?"
        if store.DB_BACKEND == "pg":
            sql += " FOR UPDATE"
        row = _row(c, sql, (prefix,))
        if row is None or not _verify_secret(token, str(row["token_hash"])):
            raise IdentityError("invalid_token", "token 无效", 401)
        if row.get("consumed_at") is not None:
            raise IdentityError("token_consumed", "token 已被消费", 409)
        if float(row["expires_at"]) <= now:
            raise IdentityError("token_expired", "token 已过期", 410)
        cur = c.execute("UPDATE bootstrap_tokens SET consumed_at=? "
                        "WHERE id=? AND consumed_at IS NULL", (now, row["id"]))
        if cur.rowcount != 1:
            raise IdentityError("token_consumed", "token 已被消费", 409)
        principal = _principal(c, str(row["principal_id"]))
        _audit(c, actor=actor, action="identity.bootstrap.consume",
               detail={"purpose": row["purpose"], "principal_id": row["principal_id"],
                       "token_id": row["id"]})
    return {"principal": _public(principal), "purpose": row["purpose"]}


def create_admin_session(*, principal_id: str, ttl: int = 1800,
                         actor: str = "cli") -> dict[str, Any]:
    sid = "sess-" + uuid.uuid4().hex[:12]
    token = f"mob_sess_{sid}_{secrets.token_urlsafe(_SECRET_BYTES)}"
    csrf = secrets.token_urlsafe(24)
    now = time.time()
    with _tx() as c:
        principal = _principal(c, principal_id, lock=True)
        if principal is None or not principal.get("enabled") or principal.get("role") != "admin":
            raise IdentityError("admin_required", "只有启用的 admin 可以创建管理会话", 403)
        c.execute(
            "INSERT INTO admin_sessions(id,principal_id,token_hash,csrf_hash,created_at,"
            "expires_at,last_seen_at,revoked_at) VALUES(?,?,?,?,?,?,?,NULL)",
            (sid, principal_id, _hash_secret(token), _hash_secret(csrf), now,
             now + ttl, now),
        )
        _audit(c, actor=actor, action="identity.session.create",
               detail={"session_id": sid, "principal_id": principal_id,
                       "expires_at": now + ttl})
    return {"session_id": sid, "session_token": token, "csrf_token": csrf,
            "expires_at": now + ttl}


def authenticate_admin_session(token: str, *, touch: bool = True) -> dict[str, Any] | None:
    m = SESSION_RE.match(str(token or ""))
    if not m:
        return None
    sid = m.group(1)
    now = time.time()
    with _tx() as c:
        row = _row(c, "SELECT * FROM admin_sessions WHERE id=?", (sid,))
        if row is None or row.get("revoked_at"):
            return None
        if float(row["expires_at"]) <= now:
            return None
        if not _verify_secret(token, str(row["token_hash"])):
            return None
        principal = _principal(c, str(row["principal_id"]))
        if principal is None or not principal.get("enabled"):
            return None
        if touch:
            last = float(row.get("last_seen_at") or 0)
            if now - last > 60:
                c.execute("UPDATE admin_sessions SET last_seen_at=? WHERE id=?", (now, sid))
    return {"principal": _public(principal), "session": _public(row)}


def verify_csrf(session_id: str, csrf_token: str) -> bool:
    with _tx() as c:
        row = _row(c, "SELECT * FROM admin_sessions WHERE id=?", (session_id,))
        if row is None or row.get("revoked_at"):
            return False
        if float(row["expires_at"]) <= time.time():
            return False
        return _verify_secret(csrf_token, str(row["csrf_hash"]))


def revoke_admin_session(session_id: str, *, actor: str = "cli") -> None:
    with _tx() as c:
        row = _row(c, "SELECT * FROM admin_sessions WHERE id=?", (session_id,))
        if row is None:
            raise IdentityError("session_not_found", "session 不存在", 404)
        c.execute("UPDATE admin_sessions SET revoked_at=? WHERE id=?", (time.time(), session_id))
        _audit(c, actor=actor, action="identity.session.revoke",
               detail={"session_id": session_id, "principal_id": row["principal_id"]})


def purge_expired_sessions(*, actor: str = "system") -> int:
    now = time.time()
    with _tx() as c:
        rows = c.execute("SELECT id FROM admin_sessions WHERE expires_at<=? AND revoked_at IS NULL",
                         (now,)).fetchall()
        c.execute("UPDATE admin_sessions SET revoked_at=? WHERE expires_at<=? AND revoked_at IS NULL",
                  (now, now))
        for row in rows:
            _audit(c, actor=actor, action="identity.session.expire",
                   detail={"session_id": row["id"]})
    return len(rows)
