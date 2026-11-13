"""Voice asset lifecycle (Phase 2C).

Assets are stored outside the registry snapshots. Profiles/revisions can
reference asset ids; this module owns bytes, checksum, provenance and soft
deletion.
"""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import store

KINDS = {"reference_audio", "generated_audio", "speaker_model", "model_weight", "preview_cache"}
MAX_BYTES = 25 * 1024 * 1024
ALLOWED_EXT = {".wav", ".mp3", ".flac", ".ogg", ".safetensors", ".pt", ".pth", ".bin", ".json"}


class AssetError(Exception):
    def __init__(self, code: str, message: str, status: int = 400,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def root_dir() -> Path:
    raw = (store.CFG.get("registry") or {}).get("voice_assets_dir")
    path = Path(str(raw)) if raw else store.DATA_DIR / "voice_assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _conn() -> Iterator[Any]:
    c = store.conn()
    try:
        if store.DB_BACKEND != "pg":
            c.execute("PRAGMA foreign_keys=ON")
        yield c
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    finally:
        c.close()


def _row(c, sql: str, args: tuple = ()) -> dict[str, Any] | None:
    r = c.execute(sql, args).fetchone()
    return dict(r) if r else None


def _safe_name(filename: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", (filename or "").strip())
    return name[:120] or "asset"


def list_assets(include_deleted: bool = False) -> list[dict[str, Any]]:
    with _conn() as c:
        sql = "SELECT * FROM registry_voice_assets"
        if not include_deleted:
            sql += " WHERE deleted=0"
        sql += " ORDER BY created_at DESC"
        return [dict(r) for r in c.execute(sql).fetchall()]


def get_asset(asset_id: str) -> dict[str, Any] | None:
    with _conn() as c:
        row = _row(c, "SELECT * FROM registry_voice_assets WHERE id=?", (asset_id,))
    if row and row.get("deleted"):
        return None
    return row


def create_asset(*, kind: str, filename: str, data: bytes, mime: str = "",
                 source: str = "", consent: str = "",
                 reuse_policy: str = "private") -> dict[str, Any]:
    if kind not in KINDS:
        raise AssetError("invalid_asset_kind", f"未知素材类型 {kind!r}", 422,
                         {"allowed": sorted(KINDS)})
    if not data:
        raise AssetError("empty_asset", "素材为空", 422)
    if len(data) > MAX_BYTES:
        raise AssetError("asset_too_large", f"素材超过 {MAX_BYTES // 1024 // 1024}MB", 413)
    safe = _safe_name(filename)
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise AssetError("unsupported_asset_type", f"不支持的文件类型 {ext or '(none)'}", 422,
                         {"allowed": sorted(ALLOWED_EXT)})
    if reuse_policy not in {"private", "this_profile", "this_character", "workspace"}:
        raise AssetError("invalid_reuse_policy", "reuse_policy 非法", 422)
    asset_id = f"va_{uuid.uuid4().hex[:12]}"
    stored = root_dir() / f"{asset_id}{ext}"
    stored.write_bytes(data)
    checksum = hashlib.sha256(data).hexdigest()
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO registry_voice_assets(id,kind,filename,stored_path,checksum,size,mime,"
                "source,consent,reuse_policy,created_at,deleted) VALUES(?,?,?,?,?,?,?,?,?,?,?,0)",
                (asset_id, kind, safe, str(stored), checksum, len(data), mime,
                 source, consent, reuse_policy, time.time()),
            )
    except Exception:
        try:
            stored.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        raise
    return get_asset(asset_id) or {}


def delete_asset(asset_id: str) -> dict[str, Any]:
    with _conn() as c:
        row = _row(c, "SELECT * FROM registry_voice_assets WHERE id=?", (asset_id,))
        if row is None:
            raise AssetError("asset_not_found", "素材不存在", 404)
        c.execute("UPDATE registry_voice_assets SET deleted=1 WHERE id=?", (asset_id,))
    return {**row, "deleted": 1}


def read_asset_bytes(asset_id: str) -> bytes:
    row = get_asset(asset_id)
    if row is None:
        raise AssetError("asset_not_found", "素材不存在", 404)
    path = Path(str(row["stored_path"]))
    if not path.is_file():
        raise AssetError("asset_missing_file", "素材文件丢失", 410)
    return path.read_bytes()
