"""Phase 0 Registry API contract.

Mounted by core.server at /api/registry. No Agent/Gateway integration.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from .registry import RegistryError, RegistryService

router = APIRouter(prefix="/api/registry", tags=["registry"])
service = RegistryService()


async def registry_error_handler(request: Request, exc: RegistryError) -> JSONResponse:
    body = exc.as_dict()
    request_id = request.headers.get("x-request-id")
    if request_id:
        body["error"]["request_id"] = request_id
    return JSONResponse(body, status_code=exc.status_code)


def _request_id(request: Request) -> str | None:
    return request.headers.get("x-request-id")


def _if_match(request: Request) -> int | None:
    raw = (request.headers.get("if-match") or "").strip().strip('"')
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _expected(request: Request, body: dict[str, Any], key: str = "expected_revision") -> int:
    value = body.get(key)
    if value is None:
        value = _if_match(request)
    if value is None:
        raise RegistryError("expected_revision_required", f"缺少 {key}", 422)
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RegistryError("invalid_revision", f"{key} 必须是整数", 422) from exc


@router.post("/{resource_type}")
async def create_resource(resource_type: str, request: Request,
                          body: dict[str, Any] = Body(...)):
    resource_id = str(body.get("id") or "").strip()
    payload = body.get("payload")
    if not resource_id:
        raise RegistryError("resource_id_required", "缺少 id", 422)
    if not isinstance(payload, dict):
        raise RegistryError("payload_required", "缺少 payload object", 422)
    return service.create(
        resource_type, resource_id, payload,
        actor=str(body.get("actor") or "local"),
        change_note=str(body.get("change_note") or ""),
        idempotency_key=request.headers.get("idempotency-key"),
        request_id=_request_id(request),
    )


@router.get("/{resource_type}")
async def list_resources(resource_type: str, status: str | None = None,
                         limit: int = 50, offset: int = 0):
    return service.list(resource_type, status=status, limit=limit, offset=offset)


@router.get("/{resource_type}/{resource_id}")
async def get_resource(resource_type: str, resource_id: str):
    return service.get(resource_type, resource_id, include_snapshot=True)


@router.put("/{resource_type}/{resource_id}")
async def update_resource(resource_type: str, resource_id: str, request: Request,
                          body: dict[str, Any] = Body(...)):
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise RegistryError("payload_required", "缺少 payload object", 422)
    return service.update(
        resource_type, resource_id, payload,
        expected_revision=_expected(request, body),
        actor=str(body.get("actor") or "local"),
        change_note=str(body.get("change_note") or ""),
        request_id=_request_id(request),
    )


@router.post("/{resource_type}/{resource_id}/publish")
async def publish_resource(resource_type: str, resource_id: str, request: Request,
                           body: dict[str, Any] = Body(...)):
    if "expected_published_revision" not in body:
        raise RegistryError("expected_published_revision_required",
                            "发布必须提供 expected_published_revision（初始发布传 null）", 422)
    return service.publish(
        resource_type, resource_id, int(body.get("revision") or 0),
        expected_current_revision=_expected(request, body, "expected_current_revision"),
        expected_published_revision=body.get("expected_published_revision"),
        actor=str(body.get("actor") or "local"),
        change_note=str(body.get("change_note") or ""),
        request_id=_request_id(request),
    )


@router.post("/{resource_type}/{resource_id}/rollback")
async def rollback_resource(resource_type: str, resource_id: str, request: Request,
                            body: dict[str, Any] = Body(...)):
    if "expected_published_revision" not in body:
        raise RegistryError("expected_published_revision_required",
                            "回滚必须提供 expected_published_revision", 422)
    return service.rollback(
        resource_type, resource_id, int(body.get("revision") or 0),
        expected_current_revision=_expected(request, body, "expected_current_revision"),
        expected_published_revision=body.get("expected_published_revision"),
        actor=str(body.get("actor") or "local"),
        request_id=_request_id(request),
    )


@router.post("/{resource_type}/{resource_id}/disable")
async def disable_resource(resource_type: str, resource_id: str, request: Request,
                           expected_revision: int | None = None):
    return service.disable(resource_type, resource_id,
                           actor="local", expected_revision=expected_revision,
                           request_id=_request_id(request))


@router.post("/{resource_type}/{resource_id}/enable")
async def enable_resource(resource_type: str, resource_id: str, request: Request,
                          expected_revision: int | None = None):
    return service.enable(resource_type, resource_id,
                          actor="local", expected_revision=expected_revision,
                          request_id=_request_id(request))


@router.post("/{resource_type}/{resource_id}/archive")
async def archive_resource(resource_type: str, resource_id: str, request: Request,
                           expected_revision: int | None = None):
    return service.archive(resource_type, resource_id,
                           actor="local", expected_revision=expected_revision,
                           request_id=_request_id(request))


@router.delete("/{resource_type}/{resource_id}")
async def delete_resource(resource_type: str, resource_id: str, request: Request,
                          expected_revision: int | None = None):
    return service.delete(resource_type, resource_id,
                          actor="local", expected_revision=expected_revision,
                          request_id=_request_id(request))


@router.get("/{resource_type}/{resource_id}/revisions")
async def list_revisions(resource_type: str, resource_id: str):
    return {"items": service.revisions(resource_type, resource_id)}


@router.post("/{resource_type}/{resource_id}/references")
async def add_reference(resource_type: str, resource_id: str, request: Request,
                        body: dict[str, Any] = Body(...)):
    target_type = str(body.get("target_type") or "").strip()
    target_id = str(body.get("target_id") or "").strip()
    if not target_type or not target_id:
        raise RegistryError("invalid_reference", "缺少 target_type/target_id", 422)
    service.add_reference(resource_type, resource_id, target_type, target_id,
                          actor="local", request_id=_request_id(request))
    return {"ok": True}


@router.delete("/{resource_type}/{resource_id}/references")
async def remove_reference(resource_type: str, resource_id: str, request: Request,
                           target_type: str, target_id: str):
    service.remove_reference(resource_type, resource_id, target_type, target_id,
                             actor="local", request_id=_request_id(request))
    return {"ok": True}
