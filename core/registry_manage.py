"""Phase 2A resource management helpers.

Imports legacy files into Registry as drafts and validates ComfyUI API prompt
JSON before it can be saved. This module does not execute workflows, publish
resources, or touch Agent/Gateway runtime.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from . import registry_index
from .registry import RegistryError, RegistryService

CHAR_BLOCKS = {
    "description": "description",
    "personality": "personality",
    "scenario": "scenario",
    "first_mes": "greeting",
    "mes_example": "example",
    "system_prompt": "system",
}

HIGH_RISK_NODES = re.compile(
    r"(python|script|shell|command|execute|subprocess|http|request|download|upload)",
    re.IGNORECASE,
)
OUTPUT_NODES = {"SaveImage", "PreviewImage", "SaveAnimatedWEBP", "SaveAnimatedPNG"}


def _slug(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip()).strip("-")
    return text[:64] or "character"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _prompt_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for field, kind in CHAR_BLOCKS.items():
        value = str(data.get(field) or "").strip()
        if value:
            blocks.append({
                "id": field, "kind": kind, "title": field,
                "content": value, "enabled": True,
                "order": len(blocks), "placement": "before_history",
            })
    return blocks


def normalize_character_card(card: dict[str, Any], fallback_id: str = "") -> dict[str, Any]:
    if not isinstance(card, dict):
        raise RegistryError("invalid_character_card", "角色卡必须是 object", 422)
    data = card.get("data") if isinstance(card.get("data"), dict) else card
    name = str(data.get("name") or card.get("name") or fallback_id or "").strip()
    if not name:
        raise RegistryError("invalid_character_card", "角色卡缺少 name", 422)
    cid = _slug(str(card.get("id") or data.get("id") or fallback_id or name))
    spec = str(card.get("spec") or data.get("spec") or "").lower()
    source_format = "chara_card_v2" if "v2" in spec else "chara_card_v3" if "v3" in spec else "legacy_json"
    extensions = data.get("extensions") if isinstance(data.get("extensions"), dict) else {}
    tags = data.get("tags") if isinstance(data.get("tags"), list) else []
    return {
        "id": cid,
        "identity": {
            "name": name,
            "aliases": data.get("aliases") if isinstance(data.get("aliases"), list) else [],
            "tags": [str(x) for x in tags],
            "creator": data.get("creator") or "",
            "version": data.get("character_version") or "",
            "notes": data.get("creator_notes") or data.get("creatorcomment") or "",
        },
        "prompt": {
            "blocks": _prompt_blocks(data),
            "postHistoryInstructions": data.get("post_history_instructions") or "",
        },
        "expression": {
            "speechStyle": data.get("speech_style") or "",
            "relationStance": data.get("relation_stance") or "",
            "taboo": data.get("taboo") if isinstance(data.get("taboo"), list) else [],
            "visualAnchor": data.get("visual_anchor") or "",
        },
        "voice_profile_id": None,
        "image_defaults": {
            "workflow": data.get("workflow") or "",
            "checkpoint": data.get("checkpoint") or "",
            "size": data.get("size") or None,
            "stylePrompt": data.get("style_prompt") or "",
            "negativePrompt": data.get("negative_prompt") or "",
            "loras": data.get("loras") or [],
        },
        "media_policy": data.get("media_policy") or {"allow_image": True, "allow_voice": True},
        "memory_policy": {"enabled": True, "scope": "user", "autoNoteExtraction": False},
        "extensions": extensions,
        "source": {
            "format": source_format,
            "raw": card,
            "checksum": _digest(card),
        },
    }


def _voice_payload(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "name": item["name"],
        "provider": item.get("engine") or "",
        "engine": item.get("engine") or "",
        "voice": item.get("voice") or "",
        "model": item.get("model") or "",
        "weight": item.get("weight"),
        "speed": item.get("speed", 1.0),
        "language": item.get("language") or "",
        "used_by": item.get("used_by") or [],
        "source": {"kind": "legacy_character_voice"},
    }


def _workflow_payload(item: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name") or item["id"],
        "format": validation["format"],
        "graph": item.get("payload") or {},
        "parameters": validation["parameters"],
        "requirements": validation["requirements"],
        "validation": validation,
        "source": {
            "kind": "legacy_workflow_json",
            "path": item.get("source"),
            "checksum": item.get("checksum"),
        },
    }


def validate_workflow(payload: Any) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    parameters: list[dict[str, Any]] = []
    requirements: dict[str, Any] = {"node_classes": [], "high_risk_nodes": []}
    if not isinstance(payload, dict):
        return {"format": "unknown", "status": "invalid", "errors": ["payload 必须是 JSON object"],
                "warnings": [], "parameters": [], "requirements": requirements, "node_count": 0}
    if isinstance(payload.get("nodes"), list):
        return {"format": "ui_workflow", "status": "invalid",
                "errors": ["Phase 2 只支持 ComfyUI API Prompt JSON（{node_id: {class_type, inputs}}）；请导出 API format"],
                "warnings": [], "parameters": [], "requirements": requirements,
                "node_count": len(payload["nodes"])}
    nodes = {str(k): v for k, v in payload.items() if isinstance(v, dict) and v.get("class_type")}
    if not nodes:
        return {"format": "unknown", "status": "invalid", "errors": ["未找到带 class_type 的节点"],
                "warnings": [], "parameters": [], "requirements": requirements, "node_count": 0}

    classes: set[str] = set()
    outputs: set[str] = set()
    for node_id, node in nodes.items():
        ctype = str(node.get("class_type") or "").strip()
        inputs = node.get("inputs")
        if not ctype:
            errors.append(f"节点 {node_id} 缺少 class_type")
            continue
        if not isinstance(inputs, dict):
            errors.append(f"节点 {node_id} inputs 必须是 object")
            continue
        classes.add(ctype)
        if ctype in OUTPUT_NODES:
            outputs.add(node_id)
        if HIGH_RISK_NODES.search(ctype):
            requirements["high_risk_nodes"].append(ctype)
            warnings.append(f"高权限节点需要人工信任: {ctype}")
        low = ctype.lower()
        if "ksampler" in low:
            for name, lo, hi in (("steps", 1, 64), ("cfg", 0, 20)):
                if name in inputs:
                    val = inputs[name]
                    if not isinstance(val, (int, float)):
                        errors.append(f"{node_id}.{name} 必须是 number")
                    elif not lo <= float(val) <= hi:
                        errors.append(f"{node_id}.{name}={val} 超出范围 {lo}..{hi}")
                    else:
                        parameters.append({"nodeId": node_id, "input": name,
                                           "expectedType": "number", "min": lo, "max": hi,
                                           "required": False})
        if "emptylatentimage" in low or "emptysd3latentimage" in low:
            for name in ("width", "height"):
                if name in inputs:
                    val = inputs[name]
                    if not isinstance(val, (int, float)):
                        errors.append(f"{node_id}.{name} 必须是 number")
                    elif not 64 <= float(val) <= 2048:
                        errors.append(f"{node_id}.{name}={val} 超出范围 64..2048")
                    else:
                        parameters.append({"nodeId": node_id, "input": name,
                                           "expectedType": "number", "min": 64, "max": 2048,
                                           "required": False})
            if "batch_size" in inputs:
                val = inputs["batch_size"]
                if not isinstance(val, (int, float)) or not 1 <= float(val) <= 4:
                    errors.append(f"{node_id}.batch_size={val} 超出范围 1..4")

    if not outputs:
        errors.append("未找到输出节点（SaveImage / PreviewImage）")
    requirements["node_classes"] = sorted(classes)
    status = "invalid" if errors else "valid"
    return {
        "format": "api_prompt",
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "parameters": parameters,
        "requirements": requirements,
        "node_count": len(nodes),
        "checksum": _digest(payload),
    }


def import_workflow(service: RegistryService, payload: Any, *, workflow_id: str = "",
                    name: str = "", actor: str = "phase2-import") -> dict[str, Any]:
    validation = validate_workflow(payload)
    if validation["status"] != "valid":
        raise RegistryError("workflow_invalid", "工作流验证失败", 422, validation)
    wid = _slug(workflow_id or name or "workflow")
    item = {"id": wid, "name": name or wid, "payload": payload,
            "source": {"kind": "imported_api_prompt"}, "checksum": validation["checksum"]}
    resource = service.create("workflow", wid, _workflow_payload(item, validation),
                              actor=actor, change_note="Phase 2 workflow import")
    return {"resource": resource, "validation": validation}


def import_character_card(service: RegistryService, card: dict[str, Any], *,
                          character_id: str = "", actor: str = "phase2-import") -> dict[str, Any]:
    payload = normalize_character_card(card, character_id)
    resource = service.create("character", payload["id"], payload,
                              actor=actor, change_note="Phase 2 character card import")
    return {"resource": resource, "payload": payload}


def import_legacy(service: RegistryService, *, actor: str = "phase2-import") -> dict[str, Any]:
    data = registry_index.index()
    result = {"characters": {"imported": 0, "skipped": 0, "failed": 0},
              "voices": {"imported": 0, "skipped": 0, "failed": 0},
              "workflows": {"imported": 0, "skipped": 0, "failed": 0},
              "errors": []}
    existing = set()
    for rtype in ("character", "voice_profile", "workflow"):
        listed = service.list(rtype, limit=100)
        existing.update((rtype, x["id"]) for x in listed["items"])

    def _already(rtype: str, rid: str) -> bool:
        return (rtype, rid) in existing

    for item in data["characters"]:
        try:
            if _already("character", item["id"]):
                result["characters"]["skipped"] += 1
                continue
            payload = normalize_character_card(item.get("payload") or item, item["id"])
            service.create("character", payload["id"], payload, actor=actor,
                           change_note="Phase 2 legacy import")
            existing.add(("character", payload["id"]))
            result["characters"]["imported"] += 1
        except Exception as exc:  # noqa: BLE001
            result["characters"]["failed"] += 1
            result["errors"].append(f"character {item.get('id')}: {exc}")

    for item in data["voices"]:
        try:
            if _already("voice_profile", item["id"]):
                result["voices"]["skipped"] += 1
                continue
            service.create("voice_profile", item["id"], _voice_payload(item),
                           actor=actor, change_note="Phase 2 legacy voice import")
            existing.add(("voice_profile", item["id"]))
            result["voices"]["imported"] += 1
        except Exception as exc:  # noqa: BLE001
            result["voices"]["failed"] += 1
            result["errors"].append(f"voice {item.get('id')}: {exc}")

    for item in data["workflows"]:
        try:
            if _already("workflow", item["id"]):
                result["workflows"]["skipped"] += 1
                continue
            validation = validate_workflow(item.get("payload") or {})
            if validation["status"] != "valid":
                result["workflows"]["skipped"] += 1
                result["errors"].append(f"workflow {item['id']}: {validation['errors'][0] if validation['errors'] else validation['status']}")
                continue
            service.create("workflow", item["id"], _workflow_payload(item, validation),
                           actor=actor, change_note="Phase 2 legacy workflow import")
            existing.add(("workflow", item["id"]))
            result["workflows"]["imported"] += 1
        except Exception as exc:  # noqa: BLE001
            result["workflows"]["failed"] += 1
            result["errors"].append(f"workflow {item.get('id')}: {exc}")
    return result
