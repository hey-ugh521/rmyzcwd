"""Phase 3A runtime resolver with shadow/enforce/fallback.

Reads only published Registry revisions. Default mode is off so existing runtime
behavior is unchanged until explicitly enabled.
"""

from __future__ import annotations

import os
from typing import Any

from . import registry_migration, store
from .registry import RegistryService

MODES = {"off", "shadow", "enforce"}


def mode() -> str:
    raw = os.environ.get("MOBIUS_REGISTRY_RUNTIME") or (store.CFG.get("registry") or {}).get("runtime_mode") or "off"
    value = str(raw).strip().lower()
    return value if value in MODES else "off"


def enabled() -> bool:
    return mode() != "off"


def _service() -> RegistryService:
    return RegistryService()


def _published(rtype: str, rid: str) -> dict[str, Any] | None:
    try:
        resource = _service().get(rtype, rid, include_snapshot=True)
    except Exception:  # noqa: BLE001
        return None
    if not resource.get("published"):
        return None
    return resource


def _voice(resource: dict[str, Any] | None, legacy_voice: dict[str, Any] | None) -> dict[str, Any]:
    if resource is None:
        return dict(legacy_voice or {})
    payload = resource.get("published", {}).get("payload") or {}
    profile_id = payload.get("voice_profile_id")
    if profile_id:
        profile = _published("voice_profile", str(profile_id))
        if profile:
            p = profile["published"]["payload"]
            lv = legacy_voice or {}
            return {
                "engine": p.get("engine") or p.get("provider") or lv.get("engine") or "",
                "voice": p.get("voice") or p.get("model") or lv.get("voice") or "",
                "speed": p.get("speed", lv.get("speed", 1.0)),
                "_registry": {
                    "id": profile_id,
                    "revision": profile["published_revision"],
                    "checksum": profile["published"]["checksum"],
                },
            }
    raw_voice = payload.get("voice")
    if not (isinstance(raw_voice, dict) and raw_voice):
        raw = (payload.get("source") or {}).get("raw") if isinstance(payload.get("source"), dict) else None
        raw_voice = raw.get("voice") if isinstance(raw, dict) else None
    if isinstance(raw_voice, dict) and raw_voice:
        return dict(raw_voice)
    return dict(legacy_voice or {})


def _published_character_by_key(key: str) -> dict[str, Any] | None:
    """Resolve a published character without reading legacy character files."""
    direct = _published("character", key)
    if direct is not None:
        return direct
    needle = key.strip().lower()
    if not needle:
        return None
    try:
        listed = _service().list("character", limit=100)["items"]
    except Exception:  # noqa: BLE001
        return None
    for item in listed:
        if item.get("published_revision") is None or item.get("status") != "active":
            continue
        try:
            resource = _service().get("character", item["id"], include_snapshot=True)
        except Exception:  # noqa: BLE001
            continue
        payload = resource.get("published", {}).get("payload") or {}
        identity = payload.get("identity") or {}
        names = [identity.get("name"), *(identity.get("aliases") or [])]
        if any(str(n).strip().lower() == needle for n in names if n):
            return resource
    return None


def character(key: str | None) -> dict[str, Any] | None:
    fallback = registry_migration.legacy_fallback_enabled()
    if enabled() and not fallback:
        lookup = str(key or (store.CFG.get("default_character") or "")).strip()
        resource = _published_character_by_key(lookup) if lookup else None
        if resource is None:
            store.audit("system", "registry.runtime.required_missing", {
                "type": "character", "key": lookup, "mode": mode(),
            }, source="runtime")
            return None
        legacy: dict[str, Any] = {}
    else:
        legacy = store.get_character(key)
        if not enabled() or legacy is None:
            return legacy
        rid = str(legacy.get("id") or key or "")
        resource = _published("character", rid) if rid else None
        if resource is None:
            store.audit("system", "registry.runtime.fallback", {
                "type": "character", "id": rid, "mode": mode(),
                "legacy_fallback": fallback,
            }, source="runtime")
            if not fallback:
                return None
            return legacy
    rid = str(resource["id"] or key or "")
    payload = resource["published"]["payload"] or {}
    identity = payload.get("identity") or {}
    image = payload.get("image_defaults") or {}
    expression = payload.get("expression") or {}
    mapped = dict(legacy)
    mapped.update({
        "id": rid,
        "name": identity.get("name") or legacy.get("name") or rid,
        "aliases": identity.get("aliases") or legacy.get("aliases") or [],
        "style_prompt": image.get("stylePrompt") or legacy.get("style_prompt") or "",
        "negative_prompt": image.get("negativePrompt") or legacy.get("negative_prompt") or "",
        "workflow": image.get("workflow") or legacy.get("workflow") or "",
        "checkpoint": image.get("checkpoint") or legacy.get("checkpoint") or "",
        "size": image.get("size") or legacy.get("size"),
        "loras": image.get("loras") or legacy.get("loras") or [],
        "media_policy": payload.get("media_policy") or legacy.get("media_policy") or {},
        "speech_style": expression.get("speechStyle") or legacy.get("speech_style") or "",
        "visual_anchor": expression.get("visualAnchor") or legacy.get("visual_anchor") or "",
        "voice": _voice(resource, legacy.get("voice")),
        "_registry": {
            "type": "character",
            "id": rid,
            "revision": resource["published_revision"],
            "checksum": resource["published"]["checksum"],
        },
    })
    if mode() == "shadow":
        changed = [k for k in mapped if legacy.get(k) != mapped.get(k) and k != "_registry"]
        store.audit("system", "registry.runtime.shadow", {
            "type": "character", "id": rid, "revision": resource["published_revision"],
            "changed_fields": changed,
        }, source="runtime")
        return legacy
    return mapped


def workflow(workflow_id: str | None) -> dict[str, Any] | None:
    if not enabled() or not workflow_id:
        return None
    candidates = [str(workflow_id), f"comic-{workflow_id}"]
    resource = None
    for candidate in candidates:
        resource = _published("workflow", candidate)
        if resource is not None:
            workflow_id = candidate
            break
    if resource is None:
        return None
    payload = resource["published"]["payload"] or {}
    graph = payload.get("graph") if isinstance(payload.get("graph"), dict) else payload
    return {
        "id": str(workflow_id),
        "revision": resource["published_revision"],
        "checksum": resource["published"]["checksum"],
        "graph": graph,
    }


def status() -> dict[str, Any]:
    return {"mode": mode(), "enabled": enabled()}
