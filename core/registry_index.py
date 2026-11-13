"""Read-only index of legacy resources for Registry Phase 1.

This module never writes to the Registry or legacy files. It projects existing
character JSON, ComfyUI workflow JSON and voice settings into one read-only
view for Studio.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from . import store


def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _source_path(path: Path) -> str:
    try:
        return str(path.relative_to(store.BASE))
    except Exception:  # noqa: BLE001
        return str(path)


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _char_files() -> list[Path]:
    base = Path(store.CHARS_DIR)
    if not base.exists():
        return []
    return sorted(base.glob("*.json"))


def character_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for path in _char_files():
        try:
            data = _read_json(path)
        except Exception as exc:  # noqa: BLE001
            items.append({
                "id": path.stem, "name": path.stem, "source": _source_path(path),
                "status": "invalid", "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        if not isinstance(data, dict):
            continue
        cid = str(data.get("id") or path.stem)
        items.append({
            "id": cid,
            "name": data.get("name") or cid,
            "aliases": data.get("aliases") or [],
            "workflow": data.get("workflow"),
            "checkpoint": data.get("checkpoint"),
            "size": data.get("size"),
            "loras": data.get("loras") or [],
            "voice": data.get("voice") or {},
            "media_policy": data.get("media_policy") or {},
            "notes": data.get("notes") or "",
            "source": _source_path(path),
            "source_kind": "legacy_character_json",
            "status": "legacy",
            "checksum": _digest(data),
            "payload": data,
        })
    return items


def _workflow_dirs() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ.get("MOBIUS_WORKFLOWS_DIR")
    if env:
        candidates.append(Path(env))
    reg = store.CFG.get("registry") or {}
    if reg.get("workflows_dir"):
        candidates.append(Path(str(reg["workflows_dir"])))
    media = store.CFG.get("media") or {}
    if media.get("workflows_dir"):
        candidates.append(Path(str(media["workflows_dir"])))
    paths = store.CFG.get("paths") or {}
    if paths.get("AI"):
        candidates.append(Path(str(paths["AI"])) / "comic" / "workflows")
    candidates.append(store.BASE / "configs" / "workflows")
    unique: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        key = str(c.resolve()) if c.exists() else str(c)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def workflow_source_dir() -> Path | None:
    for candidate in _workflow_dirs():
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _workflow_node_stats(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"format": "unknown", "node_count": 0, "class_types": []}
    if isinstance(data.get("nodes"), dict):
        values = [v for v in data["nodes"].values() if isinstance(v, dict) and v.get("class_type")]
        if values:
            return {"format": "comfy_workflow", "node_count": len(values),
                    "class_types": sorted({str(v.get("class_type")) for v in values})}
    if isinstance(data.get("nodes"), list):
        classes = [str(n.get("type") or "") for n in data["nodes"] if isinstance(n, dict)]
        return {"format": "ui_workflow", "node_count": len(data["nodes"]),
                "class_types": sorted(set(c for c in classes if c))}
    node_values = [v for v in data.values() if isinstance(v, dict) and v.get("class_type")]
    if node_values:
        return {"format": "api_prompt", "node_count": len(node_values),
                "class_types": sorted({str(v.get("class_type")) for v in node_values})}
    return {"format": "unknown", "node_count": 0, "class_types": []}


def workflow_items() -> list[dict[str, Any]]:
    directory = workflow_source_dir()
    if directory is None:
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if path.name == "models.json":
            continue
        try:
            data = _read_json(path)
        except Exception as exc:  # noqa: BLE001
            items.append({
                "id": path.stem, "name": path.stem, "source": _source_path(path),
                "status": "invalid", "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        stats = _workflow_node_stats(data)
        items.append({
            "id": path.stem,
            "name": path.stem,
            "source": _source_path(path),
            "source_kind": "legacy_workflow_json",
            "status": "legacy",
            **stats,
            "checksum": _digest(data),
            "payload": data,
        })
    return items


def image_model_items() -> list[dict[str, Any]]:
    directory = workflow_source_dir()
    if directory is None:
        return []
    path = directory / "models.json"
    if not path.exists():
        return []
    try:
        data = _read_json(path)
    except Exception:  # noqa: BLE001
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    items: list[dict[str, Any]] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        mid = str(model.get("id") or "")
        if not mid:
            continue
        items.append({
            "id": mid,
            "name": model.get("name") or mid,
            "workflow": model.get("workflow"),
            "checkpoint": model.get("checkpoint"),
            "style": model.get("style") or "",
            "available": model.get("available") is not False,
            "default": bool(model.get("default")),
            "vram_gb": model.get("vram_gb"),
            "source": _source_path(path),
            "source_kind": "legacy_models_json",
            "status": "legacy",
            "payload": model,
        })
    return items


def voice_items(characters: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    chars = characters if characters is not None else character_items()
    buckets: dict[str, dict[str, Any]] = {}
    for ch in chars:
        voice = ch.get("voice") or {}
        if not isinstance(voice, dict):
            continue
        engine = str(voice.get("engine") or "").strip()
        name = str(voice.get("voice") or "").strip()
        if not engine and not name:
            continue
        key = f"{engine}|{name}|{voice.get('speed', 1.0)}"
        item = buckets.setdefault(key, {
            "id": "voice_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10],
            "name": name or engine,
            "engine": engine,
            "voice": name,
            "speed": voice.get("speed", 1.0),
            "model": voice.get("model"),
            "weight": voice.get("weight"),
            "language": voice.get("language"),
            "source_kind": "legacy_character_voice",
            "status": "legacy",
            "used_by": [],
        })
        item["used_by"].append(ch["id"])
    return sorted(buckets.values(), key=lambda x: (x["name"], x["id"]))


def index() -> dict[str, Any]:
    characters = character_items()
    workflows = workflow_items()
    models = image_model_items()
    voices = voice_items(characters)
    workflow_use: dict[str, list[str]] = {}
    for ch in characters:
        wf = ch.get("workflow")
        if wf:
            workflow_use.setdefault(str(wf), []).append(ch["id"])
    for wf in workflows:
        keys = {wf["id"], wf["id"].split("-", 1)[-1]}
        used: list[str] = []
        for key in keys:
            for cid in workflow_use.get(key, []):
                if cid not in used:
                    used.append(cid)
        wf["used_by"] = used
    return {
        "summary": {
            "characters": len(characters),
            "voices": len(voices),
            "workflows": len(workflows),
            "image_models": len(models),
            "read_only": True,
            "workflow_dir": _source_path(workflow_source_dir()) if workflow_source_dir() else None,
        },
        "characters": characters,
        "voices": voices,
        "workflows": workflows,
        "image_models": models,
    }


def character_by_id(item_id: str) -> dict[str, Any] | None:
    return next((x for x in character_items() if x["id"] == item_id), None)


def workflow_by_id(item_id: str) -> dict[str, Any] | None:
    return next((x for x in workflow_items() if x["id"] == item_id), None)


def voice_by_id(item_id: str) -> dict[str, Any] | None:
    return next((x for x in voice_items() if x["id"] == item_id), None)
