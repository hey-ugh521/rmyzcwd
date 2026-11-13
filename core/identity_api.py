"""Phase 4F-2C: read-only GET /api/identity/me.

This endpoint does not participate in Legacy authorization. It resolves the
currently configured legacy Bearer key to an existing Principal only.
"""

from __future__ import annotations

import hmac
import logging
import re
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import identity

router = APIRouter()
log = logging.getLogger("mobius.identity_api")

_SCHEME_RE = re.compile(r"^([!#$%&'*+\-.^_`|~0-9A-Za-z]+) +(.*)$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


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
    m = _SCHEME_RE.match(raw)
    if not m:
        return None
    scheme, token = m.group(1), m.group(2)
    if scheme.lower() != "bearer" or not token:
        return None
    if any(c.isspace() for c in token):
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
    return _response(401, {"ok": False, "error": "unauthenticated"}, challenge=True)


def _unavailable() -> JSONResponse:
    return _response(500, {"ok": False, "error": "identity_unavailable"})


@router.get("/api/identity/me")
async def identity_me(request: Request) -> JSONResponse:
    request_id = _request_id(request)
    token = _parse_bearer(request)
    if not token:
        return _unauthorized()
    try:
        configured = identity.configured_legacy_key()
        if not configured:
            log.warning("identity.me configuration_unavailable request_id=%s", request_id)
            return _unavailable()
        if not hmac.compare_digest(token.encode("utf-8"), configured.encode("utf-8")):
            return _unauthorized()

        state = identity.inspect_legacy_api_key(token)

        if identity.configured_legacy_key() != configured:
            log.warning("identity.me configuration_changed request_id=%s", request_id)
            return _unavailable()

        status = str(state.get("state") or "")
        if status == "resolved":
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
        if status in {"unmapped", "mapping_missing", "conflict", "revoked",
                      "expired", "principal_disabled"}:
            return _unauthorized()
        log.warning("identity.me unknown_state request_id=%s", request_id)
        return _unavailable()
    except Exception as exc:  # noqa: BLE001 - never expose exception text
        log.warning("identity.me unavailable error_class=%s request_id=%s",
                    type(exc).__name__, request_id)
        return _unavailable()
