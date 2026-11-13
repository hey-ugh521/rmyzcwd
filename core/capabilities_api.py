"""Read/write capability policy and execution maintenance surfaces."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request

from . import capabilities

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


def _raise(exc: capabilities.CapabilityError) -> None:
    raise HTTPException(status_code=exc.status, detail={
        "error": exc.code, "message": exc.message, "details": exc.details,
    })


@router.get("")
async def catalog():
    return {"ok": True, "capabilities": capabilities.CAPABILITIES}


@router.get("/invocations")
async def invocations(limit: int = 50):
    return {"ok": True, "items": capabilities.list_invocations(limit)}


@router.get("/permissions")
async def permissions():
    return {"ok": True, "items": capabilities.permission_rows(),
            "default_allow": bool((capabilities._config() or {}).get("default_allow", True))}


@router.post("/permissions")
async def set_permission(request: Request, body: dict[str, Any] = Body(...)):
    try:
        row = capabilities.set_permission(
            subject_type=str(body.get("subject_type") or ""),
            subject=str(body.get("subject") or ""),
            capability=str(body.get("capability") or "*"),
            effect=str(body.get("effect") or "allow"),
            priority=int(body.get("priority") or 0),
            enabled=bool(body.get("enabled", True)),
            scope=body.get("scope") if isinstance(body.get("scope"), dict) else None,
            expected_version=(int(body["expected_version"])
                              if body.get("expected_version") is not None else None),
            actor=str(getattr(request.state, "actor", "anonymous") or "anonymous"),
        )
    except capabilities.CapabilityError as exc:
        _raise(exc)
    return {"ok": True, "item": row}


@router.delete("/permissions/{permission_id}")
async def delete_permission(permission_id: str, request: Request,
                            expected_version: int | None = None):
    try:
        capabilities.delete_permission(
            permission_id, expected_version=expected_version,
            actor=str(getattr(request.state, "actor", "anonymous") or "anonymous"),
        )
    except capabilities.CapabilityError as exc:
        _raise(exc)
    return {"ok": True}


@router.get("/providers")
async def providers():
    return {"ok": True, "items": capabilities.provider_rows()}


@router.get("/provider-health")
async def provider_health():
    from .services import Supervisor
    svc = capabilities.CapabilityService(supervisor=Supervisor())
    seen: dict[str, Any] = {}
    for row in capabilities.provider_rows():
        provider = str(row.get("provider"))
        if provider not in seen:
            seen[provider] = await svc.provider_health(provider)
    return {"ok": True, "items": list(seen.values())}


@router.post("/providers")
async def set_provider(request: Request, body: dict[str, Any] = Body(...)):
    try:
        row = capabilities.set_provider(
            capability=str(body.get("capability") or ""),
            provider=str(body.get("provider") or ""),
            priority=int(body.get("priority") or 100),
            enabled=bool(body.get("enabled", True)),
            constraints=body.get("constraints") if isinstance(body.get("constraints"), dict) else None,
            expected_version=(int(body["expected_version"])
                              if body.get("expected_version") is not None else None),
            actor=str(getattr(request.state, "actor", "anonymous") or "anonymous"),
        )
    except capabilities.CapabilityError as exc:
        _raise(exc)
    return {"ok": True, "item": row}


@router.get("/retention")
async def retention():
    return {"ok": True, **capabilities.retention_policy()}


@router.post("/maintenance/prune")
async def prune(body: dict[str, Any] | None = Body(default=None)):
    body = body or {}
    result = capabilities.prune_invocations(
        retention_days=(int(body["retention_days"]) if "retention_days" in body else None),
        max_records=(int(body["max_records"]) if "max_records" in body else None),
    )
    return {"ok": True, **result}
