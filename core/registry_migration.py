"""Phase 3B migration state machine and legacy/registry comparison.

The state is intentionally small and auditable. It never flips runtime behavior
by itself; the caller advances it after evidence, and Registry runtime reads the
same state to decide whether legacy fallback is still allowed.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from . import registry_index, store
from .registry import RegistryService

STATES = [
    "LEGACY_DISCOVERED",
    "LEGACY_INDEXED",
    "REGISTRY_IMPORTED",
    "REGISTRY_VERIFIED",
    "RUNTIME_SHADOW",
    "RUNTIME_SWITCHED",
    "LEGACY_READ_DISABLED",
]
_LOCK = threading.Lock()


def _path() -> Path:
    return store.DATA_DIR / "registry_migration.json"


def current_state() -> str:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        state = str(data.get("state") or "")
        return state if state in STATES else STATES[0]
    except Exception:  # noqa: BLE001
        return STATES[0]


def history() -> list[dict[str, Any]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        return list(data.get("history") or [])
    except Exception:  # noqa: BLE001
        return []


def legacy_fallback_enabled() -> bool:
    if current_state() == "LEGACY_READ_DISABLED":
        return False
    raw = os.environ.get("MOBIUS_REGISTRY_LEGACY_FALLBACK")
    if raw is None:
        raw = (store.CFG.get("registry") or {}).get("legacy_fallback", True)
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _list_published(rtype: str) -> dict[str, dict[str, Any]]:
    svc = RegistryService()
    listed = svc.list(rtype, limit=100)["items"]
    out: dict[str, dict[str, Any]] = {}
    for item in listed:
        if item.get("published_revision") is None or item.get("status") != "active":
            continue
        try:
            got = svc.get(rtype, item["id"], include_snapshot=True)
        except Exception:  # noqa: BLE001
            continue
        out[item["id"]] = got
    return out


def _workflow_published(workflow_id: str, published: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for candidate in (workflow_id, f"comic-{workflow_id}"):
        if candidate in published:
            return published[candidate]
    return None


def coverage() -> dict[str, Any]:
    idx = registry_index.index()
    chars = {c["id"]: c for c in idx["characters"]}
    voices = {v["id"]: v for v in idx["voices"]}
    workflows = {w["id"]: w for w in idx["workflows"]}
    pc = _list_published("character")
    pv = _list_published("voice_profile")
    pw = _list_published("workflow")
    missing_chars = sorted(set(chars) - set(pc))
    missing_voices = sorted(set(voices) - set(pv))
    missing_workflows = sorted(set(workflows) - set(pw))
    missing_character_workflows = []
    for c in chars.values():
        wf = str(c.get("workflow") or "")
        if wf and _workflow_published(wf, pw) is None:
            missing_character_workflows.append(f"{c['id']}->{wf}")
    return {
        "legacy": {"characters": len(chars), "voices": len(voices), "workflows": len(workflows)},
        "published": {"characters": len(pc), "voices": len(pv), "workflows": len(pw)},
        "missing": {
            "characters": missing_chars,
            "voices": missing_voices,
            "workflows": missing_workflows,
            "character_workflows": sorted(set(missing_character_workflows)),
        },
        "complete": (not missing_chars and not missing_voices and not missing_workflows
                     and not missing_character_workflows),
    }


def _norm_lora(value: Any) -> list[dict[str, Any]]:
    out = []
    for item in value or []:
        if isinstance(item, dict):
            out.append({
                "name": str(item.get("name") or item.get("lora") or ""),
                "strength": float(item.get("strength") or item.get("weight") or 1.0),
            })
    return out


def _effective_policy(value: Any) -> dict[str, bool]:
    policy = value if isinstance(value, dict) else {}
    return {
        "allow_image": policy.get("allow_image", True) is not False,
        "allow_voice": policy.get("allow_voice", True) is not False,
    }


def _graph_classes(value: Any) -> dict[str, str]:
    raw = value
    if isinstance(raw, dict) and isinstance(raw.get("nodes"), dict):
        raw = raw["nodes"]
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): str(v.get("class_type"))
        for k, v in raw.items()
        if isinstance(v, dict) and v.get("class_type")
    }


def compare() -> dict[str, Any]:
    idx = registry_index.index()
    published_chars = _list_published("character")
    published_voices = _list_published("voice_profile")
    published_workflows = _list_published("workflow")
    mismatches: list[dict[str, Any]] = []
    checked = 0
    for legacy_item in idx["characters"]:
        rid = legacy_item["id"]
        legacy = legacy_item.get("payload") if isinstance(legacy_item.get("payload"), dict) else legacy_item
        resource = published_chars.get(rid)
        if resource is None:
            mismatches.append({"type": "character", "id": rid, "field": "published", "legacy": True, "registry": False})
            continue
        payload = resource["published"]["payload"] or {}
        identity = payload.get("identity") or {}
        image = payload.get("image_defaults") or {}
        raw = (payload.get("source") or {}).get("raw") if isinstance(payload.get("source"), dict) else {}
        raw = raw if isinstance(raw, dict) else {}
        legacy_voice = legacy.get("voice") or {}
        registry_voice = payload.get("voice") if isinstance(payload.get("voice"), dict) else raw.get("voice")
        pairs = {
            "name": (legacy.get("name"), identity.get("name")),
            "workflow": (legacy.get("workflow"), image.get("workflow")),
            "style_prompt": (legacy.get("style_prompt"), image.get("stylePrompt")),
            "negative_prompt": (legacy.get("negative_prompt"), image.get("negativePrompt")),
            "checkpoint": (legacy.get("checkpoint") or None, image.get("checkpoint") or None),
            "size": (legacy.get("size"), image.get("size")),
            "loras": (_norm_lora(legacy.get("loras")), _norm_lora(image.get("loras"))),
            "media_policy": (_effective_policy(legacy.get("media_policy")),
                             _effective_policy(payload.get("media_policy"))),
            "voice.engine": (legacy_voice.get("engine"), (registry_voice or {}).get("engine")),
            "voice.voice": (legacy_voice.get("voice"), (registry_voice or {}).get("voice")),
            "voice.speed": (legacy_voice.get("speed", 1.0), (registry_voice or {}).get("speed", 1.0)),
        }
        checked += 1
        for field, (left, right) in pairs.items():
            if left != right:
                mismatches.append({"type": "character", "id": rid, "field": field,
                                   "legacy": left, "registry": right})
        # workflow alias must resolve to a published revision and preserve graph classes
        wf = str(legacy.get("workflow") or "")
        if wf:
            wf_resource = _workflow_published(wf, published_workflows)
            if wf_resource is None:
                mismatches.append({"type": "workflow", "id": wf, "field": "published",
                                   "legacy": True, "registry": False})
            else:
                old_graph = _graph_classes(legacy_item.get("payload"))
                new_graph = _graph_classes((wf_resource["published"]["payload"] or {}).get("graph"))
                if old_graph and new_graph and old_graph != new_graph:
                    mismatches.append({"type": "workflow", "id": wf, "field": "nodes",
                                       "legacy": old_graph, "registry": new_graph})

    for legacy_voice in idx["voices"]:
        vid = legacy_voice["id"]
        resource = published_voices.get(vid)
        if resource is None:
            mismatches.append({"type": "voice_profile", "id": vid, "field": "published",
                               "legacy": True, "registry": False})
            continue
        payload = resource["published"]["payload"] or {}
        checked += 1
        for field, left, right in (
            ("engine", legacy_voice.get("engine"), payload.get("engine") or payload.get("provider")),
            ("voice", legacy_voice.get("voice"), payload.get("voice") or payload.get("model")),
            ("speed", legacy_voice.get("speed", 1.0), payload.get("speed", 1.0)),
            ("used_by", sorted(legacy_voice.get("used_by") or []), sorted(payload.get("used_by") or [])),
        ):
            if left != right:
                mismatches.append({"type": "voice_profile", "id": vid, "field": field,
                                   "legacy": left, "registry": right})

    for legacy_wf in idx["workflows"]:
        wid = legacy_wf["id"]
        resource = published_workflows.get(wid)
        if resource is None:
            mismatches.append({"type": "workflow", "id": wid, "field": "published",
                               "legacy": True, "registry": False})
            continue
        checked += 1
        old_graph = _graph_classes(legacy_wf.get("payload"))
        new_graph = _graph_classes((resource["published"]["payload"] or {}).get("graph"))
        if old_graph and new_graph and old_graph != new_graph:
            mismatches.append({"type": "workflow", "id": wid, "field": "nodes",
                               "legacy": old_graph, "registry": new_graph})

    return {
        "ok": not mismatches,
        "checked": checked,
        "mismatches": mismatches,
        "coverage": coverage(),
    }


def _write(state: str, actor: str, note: str = "") -> None:
    _path().parent.mkdir(parents=True, exist_ok=True)
    hist = history()
    hist.append({"state": state, "at": time.time(), "actor": actor, "note": note})
    data = {"state": state, "updated_at": time.time(), "history": hist[-50:]}
    tmp = _path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_path())


def advance(target: str, *, actor: str = "api", note: str = "") -> dict[str, Any]:
    if target not in STATES:
        raise ValueError(f"unknown migration state {target!r}")
    with _LOCK:
        current = current_state()
        ci, ti = STATES.index(current), STATES.index(target)
        if ti != ci + 1:
            raise ValueError(f"only the next state is allowed: {current} -> {target}")
        cmp = compare()
        if target == "REGISTRY_VERIFIED" and not cmp["ok"]:
            raise ValueError("compare has mismatches; cannot mark REGISTRY_VERIFIED")
        if target == "LEGACY_READ_DISABLED":
            if not cmp["ok"] or not cmp["coverage"]["complete"]:
                raise ValueError("coverage/compare incomplete; cannot disable legacy reads")
        _write(target, actor, note)
    store.audit(actor, "registry.migration.advance", {
        "from": current, "to": target, "note": note,
    }, source="core")
    return status()


def status() -> dict[str, Any]:
    state = current_state()
    idx = STATES.index(state)
    return {
        "state": state,
        "next": STATES[idx + 1] if idx + 1 < len(STATES) else None,
        "legacy_fallback": legacy_fallback_enabled(),
        "coverage": coverage(),
        "history": history()[-10:],
    }
