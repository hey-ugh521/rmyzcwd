"""凭据读取：真实密钥一律不进仓库。

取值顺序：环境变量 → secrets.json（SSOT 单一编辑源）→ D:/AI/mobius/.env → 仓库 config.json。
都拿不到时直接抛错，不提供硬编码兜底——兜底字符串本身就是泄露面。
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlsplit

ENV_FILE = os.environ.get("MOBIUS_ENV_FILE", r"D:/AI/mobius/.env")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.environ.get("MOBIUS_CONFIG", os.path.join(_REPO_ROOT, "config.json"))

PG_HOST = "127.0.0.1"
PG_DEFAULT_PORT = "55432"

_env_cache: dict[str, str] | None = None
_cfg_cache: dict | None = None


def _env_file() -> dict[str, str]:
    global _env_cache
    if _env_cache is None:
        _env_cache = {}
        try:
            with open(ENV_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    _env_cache[k.strip()] = v.strip()
        except OSError:
            pass
    return _env_cache


def _config() -> dict:
    global _cfg_cache
    if _cfg_cache is None:
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                _cfg_cache = json.load(f)
        except (OSError, ValueError):
            _cfg_cache = {}
    return _cfg_cache


def _cfg_value(*path: str) -> str:
    node = _config()
    for key in path:
        if not isinstance(node, dict):
            return ""
        node = node.get(key)
    return node if isinstance(node, str) else ""


def _ssot(*keys: str) -> str:
    """从密钥 SSOT（secrets.json）取值；导入失败返回空串。"""
    try:
        sys.path.insert(0, _REPO_ROOT)
        from core import secrets as _secrets

        return _secrets.get(*keys)
    except Exception:  # noqa: BLE001
        return ""


def _lookup(name: str, *config_path: str) -> str:
    return os.environ.get(name) or _env_file().get(name) or _cfg_value(*config_path)


def pg_password() -> str:
    pw = os.environ.get("PG_PASSWORD") or _ssot("pg", "password") or _env_file().get("PG_PASSWORD")
    if not pw:
        dsn = _cfg_value("db_dsn")
        if dsn:
            pw = urlsplit(dsn).password or ""
    if not pw:
        raise RuntimeError(
            f"缺少 PG 口令：请在 {ENV_FILE} 设置 PG_PASSWORD，或在 {CONFIG_FILE} 配置 db_dsn"
        )
    return pw


def pg_port() -> str:
    port = _lookup("PG_PORT")
    if port:
        return port
    dsn = _cfg_value("db_dsn")
    if dsn:
        split = urlsplit(dsn)
        if split.port:
            return str(split.port)
    return PG_DEFAULT_PORT


def pg_dsn(dbname: str, user: str = "postgres") -> str:
    return f"postgresql://{user}:{pg_password()}@{PG_HOST}:{pg_port()}/{dbname}"


def redis_url(db: int = 0) -> str:
    url = _cfg_value("services", "gateway", "env", "MOBIUS_REDIS_URL")
    pw = _lookup("REDIS_PASSWORD")
    if url and pw and f":{pw}@" not in url:
        # .env 轮换过口令，但 config.json 仍是旧值：以 .env 为准
        split = urlsplit(url)
        port = _lookup("REDIS_PORT") or (split.port or "56379")
        return f"redis://:{pw}@{PG_HOST}:{port}/{db}"
    if url:
        return url
    if pw:
        port = _lookup("REDIS_PORT") or "56379"
        return f"redis://:{pw}@{PG_HOST}:{port}/{db}"
    raise RuntimeError(
        f"缺少 Redis 口令：请在 {ENV_FILE} 设置 REDIS_PASSWORD，或在 {CONFIG_FILE} 配置 MOBIUS_REDIS_URL"
    )


def langbot_api_key() -> str:
    key = (os.environ.get("LANGBOT_API_KEY")
           or _ssot("langbot", "integration_api_key")
           or _ssot("langbot", "global_api_key")
           or _lookup("LANGBOT_API_KEY", "integrations", "langbot", "api_key"))
    if not key:
        raise RuntimeError(f"缺少 LangBot API Key：见 {CONFIG_FILE} 的 integrations.langbot.api_key")
    return key


def deepseek_api_key() -> str:
    key = (os.environ.get("MOBIUS_DEEPSEEK_KEY")
           or _ssot("deepseek", "api_key")
           or _lookup("MOBIUS_DEEPSEEK_KEY", "llm", "api_key"))
    if not key:
        raise RuntimeError(f"缺少 DeepSeek Key：见 {CONFIG_FILE} 的 llm.api_key")
    return key
