"""路径契约（L3 P1）：把本机绝对路径从代码/配置里抽出来。

配置中的字符串支持 `${NAME}` 占位符：
- 内置变量：ROOT（应用根目录）、RUNTIME（默认 ROOT/runtime）、MODELS（默认 ROOT/models）、
  DATA（默认 ROOT/data）、LOGS（默认 ROOT/logs）
- 自定义变量：config.json → paths.{NAME}
- 环境变量：找不到上面两项时，回退到同名环境变量

应用根目录解析顺序：MOBIUS_ROOT 环境变量 → 本文件所在包的上一级目录。
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_ROOT = "MOBIUS_ROOT"


def app_root() -> Path:
    env = os.environ.get(_ENV_ROOT, "").strip()
    if env:
        p = Path(env)
        if (p / "config.json").is_file() or (p / "core").is_dir():
            return p.resolve()
    return Path(__file__).resolve().parent.parent


def builtin_paths(root: Path | None = None) -> dict[str, str]:
    r = (root or app_root()).resolve()
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


def variable_map(cfg: dict | None = None) -> dict[str, str]:
    mapping = builtin_paths()
    for key, value in ((cfg or {}).get("paths") or {}).items():
        if isinstance(value, str):
            mapping[str(key)] = value
    # 展开自定义变量里可能引用的内置变量（最多 5 层，防循环）
    for _ in range(5):
        changed = False
        for key, value in list(mapping.items()):
            new = _expand_once(value, mapping)
            if new != value:
                mapping[key] = new
                changed = True
        if not changed:
            break
    return mapping


def _expand_once(value: str, mapping: dict[str, str]) -> str:
    if "${" not in value:
        return value
    out = value
    for name, replacement in mapping.items():
        token = "${" + name + "}"
        if token in out:
            out = out.replace(token, replacement)
    # 环境变量兜底（${SOME_ENV}）
    if "${" in out:
        import re

        def repl(match: "re.Match[str]") -> str:
            name = match.group(1)
            return os.environ.get(name, match.group(0))

        out = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, out)
    return out


def expand(value: str, mapping: dict[str, str] | None = None) -> str:
    if not isinstance(value, str):
        return value
    mapping = mapping or variable_map()
    out = value
    for _ in range(5):
        new = _expand_once(out, mapping)
        if new == out:
            break
        out = new
    return out


def expand_config(cfg: dict, root: Path | None = None) -> dict:
    """原地把所有字符串值里的 ${VAR} 展开，返回同一对象。"""
    mapping = variable_map({**cfg, "paths": cfg.get("paths") or {}}) if root is None else None
    if mapping is None:
        base = builtin_paths(root)
        mapping = dict(base)
        for key, value in (cfg.get("paths") or {}).items():
            if isinstance(value, str):
                mapping[str(key)] = expand(value, mapping)

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


def resolve_path(value: str, root: Path | None = None) -> str:
    """展开后若仍是相对路径，则相对应用根目录解析。"""
    expanded = expand(value, variable_map())
    if not expanded:
        return expanded
    p = Path(expanded)
    if p.is_absolute():
        return str(p)
    return str(((root or app_root()) / p).resolve())
