"""Read-only capability catalog and execution records."""

from __future__ import annotations

from fastapi import APIRouter

from . import capabilities

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


@router.get("")
async def catalog():
    return {"ok": True, "capabilities": capabilities.CAPABILITIES}


@router.get("/invocations")
async def invocations(limit: int = 50):
    return {"ok": True, "items": capabilities.list_invocations(limit)}
