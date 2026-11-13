"""Phase 2A management API (draft import/validation only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, File, Form, Request, UploadFile

from . import registry_manage, registry_runtime, voice_assets
from .registry import RegistryError, RegistryService
from .services import GatewayClient

router = APIRouter(prefix="/api/registry/manage", tags=["registry-manage"])
service = RegistryService()
gateway = GatewayClient()


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


@router.get("/runtime/status")
async def runtime_status():
    return {"ok": True, **registry_runtime.status()}


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


@router.post("/workflows/{workflow_id}/test")
async def test_workflow(workflow_id: str):
    """Run the current draft/published graph once through Media Gateway.

    This does not create a TaskStore task or publish anything.
    """
    resource = service.get("workflow", workflow_id, include_snapshot=True)
    current = resource.get("current")
    if not current:
        raise RegistryError("revision_not_found", "workflow 没有当前 revision", 409)
    payload = current.get("payload") or {}
    graph = registry_manage.workflow_nodes(payload)
    validation = registry_manage.validate_workflow(payload.get("graph") if isinstance(payload, dict) else payload)
    if validation["status"] != "valid":
        raise RegistryError("workflow_invalid", "工作流验证失败", 422, validation)
    try:
        result = await gateway.test_workflow(graph)
    except Exception as exc:  # noqa: BLE001
        raise RegistryError("workflow_test_failed", f"试跑失败: {type(exc).__name__}: {exc}", 502) from exc
    return {"ok": True, "revision": current.get("revision"), "validation": validation, "result": result}


# ------------------------------------------------------------------ VoiceAsset

@router.get("/voice-assets")
async def list_voice_assets():
    return {"ok": True, "items": voice_assets.list_assets()}


@router.get("/voice-assets/{asset_id}")
async def get_voice_asset(asset_id: str):
    row = voice_assets.get_asset(asset_id)
    if row is None:
        raise RegistryError("asset_not_found", "素材不存在", 404)
    return {"ok": True, "item": row}


@router.post("/voice-assets")
async def upload_voice_asset(
    request: Request,
    file: UploadFile = File(...),
    kind: str = Form("reference_audio"),
    source: str = Form(""),
    consent: str = Form(""),
    reuse_policy: str = Form("private"),
):
    data = await file.read()
    try:
        row = voice_assets.create_asset(
            kind=kind, filename=file.filename or "asset", data=data,
            mime=file.content_type or "", source=source, consent=consent,
            reuse_policy=reuse_policy,
        )
    except voice_assets.AssetError as exc:
        raise RegistryError(exc.code, exc.message, exc.status, exc.details) from exc
    return {"ok": True, "item": row, "actor": _actor(request)}


@router.delete("/voice-assets/{asset_id}")
async def delete_voice_asset(asset_id: str):
    try:
        row = voice_assets.delete_asset(asset_id)
    except voice_assets.AssetError as exc:
        raise RegistryError(exc.code, exc.message, exc.status, exc.details) from exc
    return {"ok": True, "item": row}
