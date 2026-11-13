"""Phase 2A management API (draft import/validation only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from . import registry_manage
from .registry import RegistryError, RegistryService

router = APIRouter(prefix="/api/registry/manage", tags=["registry-manage"])
service = RegistryService()


def _actor(request: Request) -> str:
    return str(getattr(request.state, "actor", "anonymous") or "anonymous")


def _workflow_payload(body: dict[str, Any]) -> Any:
    if "payload" in body:
        return body["payload"]
    if "workflow" in body:
        return body["workflow"]
    if "json" in body:
        return body["json"]
    return body


@router.post("/import/legacy")
async def import_legacy(request: Request):
    return {"ok": True, **registry_manage.import_legacy(service, actor=_actor(request))}


@router.post("/characters/import")
async def import_character(request: Request, body: dict[str, Any] = Body(...)):
    card = body.get("card") or body.get("payload") or body
    if not isinstance(card, dict):
        raise RegistryError("invalid_character_card", "card 必须是 object", 422)
    result = registry_manage.import_character_card(
        service, card, character_id=str(body.get("id") or ""), actor=_actor(request),
    )
    return {"ok": True, **result}


@router.post("/workflows/validate")
async def validate_workflow(body: dict[str, Any] = Body(...)):
    payload = _workflow_payload(body)
    info = registry_manage.object_info() if body.get("live_check") else None
    validation = registry_manage.validate_workflow(payload, object_info=info)
    validation["live_checked"] = info is not None
    validation["comfyui_url"] = registry_manage.comfyui_url()
    return {"ok": True, "validation": validation}


@router.get("/comfyui/nodes")
async def comfyui_nodes():
    info = registry_manage.object_info()
    return {
        "ok": info is not None,
        "url": registry_manage.comfyui_url(),
        "node_count": len(info) if info else 0,
    }


@router.post("/workflows/import")
async def import_workflow(request: Request, body: dict[str, Any] = Body(...)):
    payload = _workflow_payload(body)
    result = registry_manage.import_workflow(
        service, payload,
        workflow_id=str(body.get("id") or ""),
        name=str(body.get("name") or ""),
        actor=_actor(request),
    )
    return {"ok": True, **result}
