"""密钥 SSOT 读取层（单一口径）。

secrets.json 是密钥的唯一编辑源；本模块负责：
- 解析路径（环境变量 → config.json 的 secrets_file → <root>/data/secrets.json → D:/AI/mobius/secrets.json）
- 读取/校验
- 把密钥覆盖到已展开的 Core 配置（core.api_key / llm.api_key / integrations.langbot.api_key / db_dsn 口令）

调用方：core/store.py（启动时 overlay）、ops/credentials.py（其它进程）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

DEFAULT_FALLBACK = Path(r"D:\AI\mobius\secrets.json")


def _root() -> Path:
    env = os.environ.get("MOBIUS_ROOT", "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    return Path(__file__).resolve().parent.parent


def _config_file(root: Path) -> Path:
    env = os.environ.get("MOBIUS_CONFIG", "").strip()
    return Path(env) if env else (root / "config.json")


def secrets_path(root: Path | None = None) -> Path:
    """按优先级解析 secrets.json 路径（不保证存在）。"""
    env = os.environ.get("MOBIUS_SECRETS_FILE", "").strip()
    if env:
        return Path(env)
    root = root or _root()
    cfg_file = _config_file(root)
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        rel = str(cfg.get("secrets_file") or "").strip()
        if rel:
            p = Path(rel)
            return p if p.is_absolute() else (root / p)
    except (OSError, ValueError):
        pass
    candidate = root / "data" / "secrets.json"
    if candidate.is_file():
        return candidate
    return DEFAULT_FALLBACK


def load(root: Path | None = None) -> dict:
    """读取 secrets.json；不存在/损坏返回空 dict（调用方回退旧行为）。"""
    path = secrets_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def get(*keys: str, default: str = "", data: dict | None = None) -> str:
    """按层级取值，如 get("pg", "password")。"""
    node: object = data if data is not None else load()
    for key in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
    return str(node) if isinstance(node, str) and node else default


def _with_password(dsn: str, password: str) -> str:
    """替换 DSN 中的口令（保留其它部分）。"""
    try:
        parts = urlsplit(dsn)
        if not parts.password or not parts.username:
            return dsn
        netloc = f"{parts.username}:{quote(password, safe='')}@{parts.hostname or '127.0.0.1'}"
        if parts.port:
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except ValueError:
        return dsn


def apply_to_config(cfg: dict, root: Path | None = None) -> bool:
    """把 SSOT 密钥覆盖到已展开的 Core 配置；返回是否应用成功。"""
    data = load(root)
    if not data:
        return False
    pg = get("pg", "password", default="", data=data)
    core_key = get("core", "api_key", default="", data=data)
    ds_key = get("deepseek", "api_key", default="", data=data)
    lb = data.get("langbot") or {}
    lb_key = str(lb.get("integration_api_key") or lb.get("global_api_key") or "")

    if core_key:
        cfg.setdefault("core", {})["api_key"] = core_key
    if ds_key:
        cfg.setdefault("llm", {})["api_key"] = ds_key
    if lb_key:
        cfg.setdefault("integrations", {}).setdefault("langbot", {})["api_key"] = lb_key
    if pg:
        for key in ("db_dsn",):
            if isinstance(cfg.get(key), str):
                cfg[key] = _with_password(cfg[key], pg)
        gw = ((cfg.get("services") or {}).get("gateway") or {}).get("env") or {}
        if isinstance(gw.get("MOBIUS_TASK_DSN"), str):
            gw["MOBIUS_TASK_DSN"] = _with_password(gw["MOBIUS_TASK_DSN"], pg)
    return True
