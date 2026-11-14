"""Phase 4F-2C: read-only GET /api/identity/me.

This endpoint does not participate in Legacy authorization. It resolves the
currently configured legacy Bearer key to an existing Principal only.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import re
import threading
import time
import uuid
import weakref

from anyio.to_thread import run_sync
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import identity, store

router = APIRouter()
log = logging.getLogger("mobius.identity_api")

IDENTITY_ME_TIMEOUT_SECONDS = 5.0
IDENTITY_ME_MAX_CONCURRENCY = 4
IDENTITY_DB_STATEMENT_TIMEOUT_MS = 5000
IDENTITY_STATS_LOG_INTERVAL_SECONDS = 60.0
_IDENTITY_SEMAPHORES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_SCHEME_RE = re.compile(r"([!#$%&'*+\-.^_`|~0-9A-Za-z]+) +(\S+)")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_STATS_LOCK = threading.Lock()
_LAST_STATS_LOG = 0.0
_STATS = {
    "requests": 0,
    "success": 0,
    "unauthorized": 0,
    "unavailable": 0,
    "slot_timeouts": 0,
    "db_timeouts": 0,
    "background_errors": 0,
    "active_workers": 0,
    "max_active_workers": 0,
}


def _bump(name: str, delta: int = 1) -> None:
    with _STATS_LOCK:
        _STATS[name] = _STATS.get(name, 0) + delta
        if name == "active_workers":
            _STATS["max_active_workers"] = max(
                _STATS["max_active_workers"], _STATS[name])


def stats_snapshot() -> dict:
    with _STATS_LOCK:
        return dict(_STATS)


def _maybe_log_stats() -> None:
    global _LAST_STATS_LOG
    now = time.monotonic()
    with _STATS_LOCK:
        if now - _LAST_STATS_LOG < IDENTITY_STATS_LOG_INTERVAL_SECONDS:
            return
        _LAST_STATS_LOG = now
        snapshot = dict(_STATS)
    log.info("identity.me stats %s", snapshot)


class _IdentityTimeout(Exception):
    pass


def _identity_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _IDENTITY_SEMAPHORES.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(IDENTITY_ME_MAX_CONCURRENCY)
        _IDENTITY_SEMAPHORES[loop] = sem
    return sem


def _release_slot(loop: asyncio.AbstractEventLoop,
                  sem: asyncio.Semaphore) -> None:
    try:
        loop.call_soon_threadsafe(sem.release)
    except RuntimeError:
        pass


def _resolve_with_slot(token: str, loop: asyncio.AbstractEventLoop,
                       sem: asyncio.Semaphore, state: dict) -> dict:
    """Run one DB resolution while holding a slot until it truly completes."""
    state["started"] = True
    _bump("active_workers")
    try:
        with store.pg_timeouts(
                statement_timeout_ms=IDENTITY_DB_STATEMENT_TIMEOUT_MS):
            return _resolve_identity(token)
    finally:
        _bump("active_workers", -1)
        _release_slot(loop, sem)


def _consume_task(task: asyncio.Task, loop: asyncio.AbstractEventLoop,
                  sem: asyncio.Semaphore, state: dict) -> None:
    if not state.get("started"):
        _release_slot(loop, sem)
    if task.cancelled():
        return
    exc = task.exception()
    if state.get("timed_out") and exc is not None:
        _bump("background_errors")
        log.warning("identity.me background_error error_class=%s request_id=%s",
                    type(exc).__name__, state.get("request_id"))


async def _run_identity(token: str, request_id: str) -> dict:
    loop = asyncio.get_running_loop()
    sem = _identity_semaphore()
    deadline = loop.time() + IDENTITY_ME_TIMEOUT_SECONDS
    try:
        await asyncio.wait_for(sem.acquire(),
                               timeout=max(0.001, deadline - loop.time()))
    except asyncio.TimeoutError as exc:
        _bump("slot_timeouts")
        raise _IdentityTimeout("slot_timeout") from exc

    state: dict = {"request_id": request_id}
    task = asyncio.create_task(
        run_sync(_resolve_with_slot, token, loop, sem, state))
    task.add_done_callback(
        lambda t: _consume_task(t, loop, sem, state))
    try:
        return await asyncio.wait_for(
            asyncio.shield(task),
            timeout=max(0.001, deadline - loop.time()),
        )
    except asyncio.TimeoutError as exc:
        state["timed_out"] = True
        _bump("db_timeouts")
        raise _IdentityTimeout("db_timeout") from exc




class _Unavailable(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _raw_header_values(request: Request, name: bytes) -> list[bytes]:
    return [value for key, value in request.scope.get("headers", [])
            if key.lower() == name]


def _parse_bearer(request: Request) -> str | None:
    """Parse standard Bearer auth from the raw ASGI header list.

    Any missing/malformed/duplicate Authorization header is unauthenticated.
    """
    values = _raw_header_values(request, b"authorization")
    if len(values) != 1:
        return None
    try:
        raw = values[0].decode("latin-1")
    except Exception:  # noqa: BLE001
        return None
    m = _SCHEME_RE.fullmatch(raw)
    if not m:
        return None
    scheme, token = m.group(1), m.group(2)
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _request_id(request: Request) -> str:
    values = _raw_header_values(request, b"x-request-id")
    if len(values) == 1:
        try:
            candidate = values[0].decode("ascii")
        except Exception:  # noqa: BLE001
            candidate = ""
        if _REQUEST_ID_RE.fullmatch(candidate):
            return candidate
    return uuid.uuid4().hex


def _response(status_code: int, body: dict, *, challenge: bool = False) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if challenge:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(body, status_code=status_code, headers=headers)


def _unauthorized() -> JSONResponse:
    _bump("unauthorized")
    return _response(401, {"ok": False, "error": "unauthenticated"}, challenge=True)


def _unavailable() -> JSONResponse:
    _bump("unavailable")
    return _response(500, {"ok": False, "error": "identity_unavailable"})


def _resolve_identity(token: str) -> dict:
    """Synchronous read-only resolution; runs in the Starlette threadpool."""
    configured = identity.configured_legacy_key()
    if not configured:
        raise _Unavailable("configuration_unavailable")
    if not hmac.compare_digest(token.encode("utf-8"), configured.encode("utf-8")):
        return {"state": "unauthenticated"}
    state = identity.inspect_legacy_api_key(token)
    if identity.configured_legacy_key() != configured:
        raise _Unavailable("configuration_changed")
    return state


@router.get("/api/identity/me")
async def identity_me(request: Request) -> JSONResponse:
    _bump("requests")
    _maybe_log_stats()
    request_id = _request_id(request)
    token = _parse_bearer(request)
    if not token:
        return _unauthorized()
    try:
        state = await _run_identity(token, request_id)
    except _IdentityTimeout as exc:
        log.warning("identity.me timeout reason=%s request_id=%s",
                    str(exc), request_id)
        return _unavailable()
    except _Unavailable as exc:
        log.warning("identity.me %s request_id=%s", exc.reason, request_id)
        return _unavailable()
    except Exception as exc:  # noqa: BLE001 - never expose exception text
        log.warning("identity.me unavailable error_class=%s request_id=%s",
                    type(exc).__name__, request_id)
        return _unavailable()

    status = str(state.get("state") or "")
    if status == "resolved":
        _bump("success")
        principal = state.get("principal") or {}
        return _response(200, {
            "ok": True,
            "identity_mode": "legacy",
            "principal": {
                "id": principal.get("id"),
                "kind": principal.get("kind"),
                "role": principal.get("role"),
            },
        })
    if status in {"unauthenticated", "unmapped", "mapping_missing", "conflict",
                  "revoked", "expired", "principal_disabled"}:
        return _unauthorized()
    log.warning("identity.me unknown_state request_id=%s", request_id)
    return _unavailable()
