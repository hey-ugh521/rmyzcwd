"""Read-only Registry index API used by Studio Phase 1."""

from __future__ import annotations

from fastapi import APIRouter

from . import registry_index
from .registry import RegistryError

router = APIRouter(prefix="/api/registry/index", tags=["registry-index"])


@router.get("")
async def get_index():
    return {"ok": True, **registry_index.index()}


@router.get("/characters")
async def get_characters():
    items = registry_index.character_items()
    return {"ok": True, "items": items, "total": len(items)}


@router.get("/characters/{item_id}")
async def get_character(item_id: str):
    item = registry_index.character_by_id(item_id)
    if item is None:
        raise RegistryError("index_item_not_found", "角色索引项不存在", 404)
    return {"ok": True, "item": item}


@router.get("/voices")
async def get_voices():
    items = registry_index.voice_items()
    return {"ok": True, "items": items, "total": len(items)}


@router.get("/voices/{item_id}")
async def get_voice(item_id: str):
    item = registry_index.voice_by_id(item_id)
    if item is None:
        raise RegistryError("index_item_not_found", "音色索引项不存在", 404)
    return {"ok": True, "item": item}


@router.get("/workflows")
async def get_workflows():
    items = registry_index.workflow_items()
    return {"ok": True, "items": items, "total": len(items)}


@router.get("/workflows/{item_id}")
async def get_workflow(item_id: str):
    item = registry_index.workflow_by_id(item_id)
    if item is None:
        raise RegistryError("index_item_not_found", "工作流索引项不存在", 404)
    return {"ok": True, "item": item}


@router.get("/image-models")
async def get_image_models():
    items = registry_index.image_model_items()
    return {"ok": True, "items": items, "total": len(items)}
