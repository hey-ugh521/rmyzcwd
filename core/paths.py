"""Core 路径契约（薄兼容层）。

路径展开的唯一实现已收敛到 `shared/paths.py`（Core 与启动器共用），
本模块只做 re-export，保留原有导出名，`core/store.py` 等调用方无需改动。

配置中的字符串支持 `${NAME}` 占位符：
- 内置变量：ROOT / RUNTIME / MODELS / DATA / LOGS
- 自定义变量：config.json → paths.{NAME}
- 环境变量：以上都找不到时，回退到同名环境变量

应用根目录解析顺序：MOBIUS_ROOT（必须含标记）→ 本文件所在包向上找标记。
"""

from __future__ import annotations

from shared.paths import (
    app_root,
    builtin_paths,
    expand,
    expand_config,
    resolve_path,
    variable_map,
)

__all__ = [
    "app_root",
    "builtin_paths",
    "variable_map",
    "expand",
    "expand_config",
    "resolve_path",
]
