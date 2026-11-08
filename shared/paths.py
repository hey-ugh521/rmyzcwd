"""Möbius 共享路径展开模块（Core 与启动器唯一实现）。

配置中的字符串支持 `${NAME}` 占位符：
- 内置变量：ROOT（应用根目录）、RUNTIME（默认 ROOT/runtime）、MODELS（默认 ROOT/models）、
  DATA（默认 ROOT/data）、LOGS（默认 ROOT/logs）
- 自定义变量：config.json → paths.{NAME}（可嵌套引用，最多 5 层防循环）
- 环境变量：以上都找不到时，回退到同名环境变量；仍找不到则原样保留

应用根目录解析顺序：MOBIUS_ROOT（必须含标记才算数）→ 从本文件向上找标记
（config.json / config.example.json / shared/paths.py，最多 5 层）→ 本文件所在目录。

约束：仅依赖标准库、Windows 路径安全、纯函数（除显式读取环境变量外无副作用），
便于 Core 与启动器直接复用和单元测试。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ENV_ROOT = "MOBIUS_ROOT"
# 判断一个目录是否为 Möbius 根目录的标记（相对候选目录）
DEFAULT_MARKERS: tuple[str, ...] = ("config.json", "config.example.json", "shared/paths.py")
DEFAULT_MAX_LEVELS = 5
MAX_EXPAND_DEPTH = 5
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

__all__ = [
    "find_root",
    "app_root",
    "builtin_paths",
    "variable_map",
    "expand",
    "expand_config",
    "resolve_path",
]


def _has_marker(directory: str, markers: tuple[str, ...]) -> bool:
    """候选目录是否含有任一标记（标记允许用 / 表示子路径）。"""
    for marker in markers:
        parts = [p for p in str(marker).replace("\\", "/").split("/") if p]
        if parts and os.path.exists(os.path.join(directory, *parts)):
            return True
    return False


def find_root(
    start: str | os.PathLike[str],
    env: str = ENV_ROOT,
    markers: tuple[str, ...] = DEFAULT_MARKERS,
    max_levels: int = DEFAULT_MAX_LEVELS,
) -> str:
    """从 start 目录向上探测 Möbius 根目录，返回规范化绝对路径。

    - MOBIUS_ROOT 优先，但必须包含标记才采用；无效时继续向上搜索。
    - 搜索最多 max_levels 层（含 start 本身）。
    - 找不到时返回 start 的规范化绝对路径（不抛异常，便于启动器 bootstrap）。
    """
    start_abs = os.path.abspath(os.fspath(start))
    if os.path.isfile(start_abs):  # 容错：传入文件时从所在目录开始
        start_abs = os.path.dirname(start_abs)

    env_root = os.environ.get(env, "").strip()
    if env_root:
        env_abs = os.path.abspath(os.path.expanduser(env_root))
        if _has_marker(env_abs, markers):
            return env_abs

    cur = start_abs
    for _ in range(max(0, int(max_levels))):
        if _has_marker(cur, markers):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return start_abs


def app_root() -> Path:
    """默认应用根目录：从 shared/paths.py 向上探测（受 MOBIUS_ROOT 影响）。"""
    return Path(find_root(Path(__file__).resolve().parent))


def builtin_paths(root: Path | str | None = None) -> dict[str, str]:
    """内置路径变量；root 缺省时使用 app_root()。"""
    r = Path(root).resolve() if root is not None else app_root()
    runtime = os.environ.get("MOBIUS_RUNTIME", "").strip() or str(r / "runtime")
    models = os.environ.get("MOBIUS_MODELS", "").strip() or str(r / "models")
    data = os.environ.get("MOBIUS_DATA", "").strip() or str(r / "data")
    return {
        "ROOT": str(r),
        "RUNTIME": runtime,
        "MODELS": models,
        "DATA": data,
        "LOGS": str(r / "logs"),
    }


def _expand_once(value: str, mapping: dict[str, str]) -> str:
    """单次替换：先查 mapping，再回退同名环境变量，未知变量保留原样。"""
    if not isinstance(value, str) or "${" not in value:
        return value
    out = value
    for name, replacement in mapping.items():
        token = "${" + str(name) + "}"
        if token in out:
            out = out.replace(token, str(replacement))
    if "${" in out:
        out = _VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), out)
    return out


def variable_map(cfg: dict | None = None, root: Path | str | None = None) -> dict[str, str]:
    """内置变量 + config["paths"] 自定义变量，递归展开（最多 5 层，防循环）。"""
    mapping = builtin_paths(root)
    for key, value in ((cfg or {}).get("paths") or {}).items():
        if isinstance(value, str):
            mapping[str(key)] = value
    # 展开自定义变量里可能引用的内置/其它自定义变量（最多 5 层，防循环）
    for _ in range(MAX_EXPAND_DEPTH):
        changed = False
        for key, value in list(mapping.items()):
            new = _expand_once(value, mapping)
            if new != value:
                mapping[key] = new
                changed = True
        if not changed:
            break
    return mapping


def expand(value: str, mapping: dict[str, str] | None = None) -> str:
    """展开字符串中的 ${VAR}；mapping 缺省时使用 variable_map()。"""
    if not isinstance(value, str):
        return value
    if mapping is None:
        mapping = variable_map()
    out = value
    for _ in range(MAX_EXPAND_DEPTH):
        new = _expand_once(out, mapping)
        if new == out:
            break
        out = new
    return out


def expand_config(cfg: dict, root: Path | str | None = None) -> dict:
    """原地递归展开 dict/list 中所有字符串值，返回同一对象（与 Core 现状一致）。"""
    base_cfg = cfg if isinstance(cfg, dict) else None
    mapping = variable_map(base_cfg, root)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                node[key] = walk(value)
            return node
        if isinstance(node, list):
            for i, value in enumerate(node):
                node[i] = walk(value)
            return node
        if isinstance(node, str):
            return expand(node, mapping)
        return node

    return walk(cfg)


def resolve_path(value: str, root: Path | str | None = None) -> str:
    """展开后若仍是相对路径，则相对应用根目录（root 缺省为 app_root()）解析。"""
    if not isinstance(value, str):
        return value
    expanded = expand(value, variable_map(None, root))
    if not expanded:
        return expanded
    p = Path(expanded)
    if p.is_absolute():
        return str(p)
    base = Path(root).resolve() if root is not None else app_root()
    return str((base / p).resolve())
