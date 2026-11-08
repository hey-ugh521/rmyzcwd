"""tests/test_shared_paths.py — 路径展开公共模块单测（独立脚本，无需 pytest）。

覆盖：
1. find_root：标记探测、多层父目录、MOBIUS_ROOT 生效/无效回退、找不到返回 start。
2. 展开：内置变量、cfg.paths 自定义+嵌套、${ENV} 回退、未知变量保留、
   dict/list 递归、相对 resolve_path。
3. 一致性：真实 config.example.json 经 shared.paths 与 core.paths 展开后逐字段相等。
4. launcher 源码静态检查：不再内联 _path_map/_expand_cfg，改为加载 shared/paths.py。
5. py_compile：core/paths.py、shared/paths.py、launcher。
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def _write(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _norm(path) -> str:
    """Windows 安全的路径比较（忽略大小写、斜杠与 . / ..）。"""
    return os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))


def test_find_root(shared, tmp: Path) -> None:
    print("[1] find_root")
    old_root = os.environ.get("MOBIUS_ROOT")
    try:
        os.environ.pop("MOBIUS_ROOT", None)

        proj = tmp / "proj"
        _write(proj / "config.json", "{}")
        deep = proj / "a" / "b" / "c"
        deep.mkdir(parents=True)
        check("向上多层找到 ROOT", _norm(shared.find_root(deep)) == _norm(proj),
              shared.find_root(deep))
        check("start 自身含标记", _norm(shared.find_root(proj)) == _norm(proj))

        exactly5 = proj / "a" / "b" / "c" / "d"
        exactly5.mkdir(parents=True, exist_ok=True)
        check("默认 5 层边界内找到", _norm(shared.find_root(exactly5)) == _norm(proj))
        too_deep = exactly5 / "e"
        too_deep.mkdir(parents=True, exist_ok=True)
        check("超过 5 层不找到", _norm(shared.find_root(too_deep)) == _norm(too_deep))

        proj2 = tmp / "proj2"
        _write(proj2 / "shared" / "paths.py", "# marker")
        start2 = proj2 / "x" / "y"
        start2.mkdir(parents=True)
        check("shared/paths.py 标记", _norm(shared.find_root(start2)) == _norm(proj2))

        proj3 = tmp / "proj3"
        _write(proj3 / "config.example.json", "{}")
        check("config.example.json 标记",
              _norm(shared.find_root(proj3)) == _norm(proj3))

        # MOBIUS_ROOT 优先（必须含标记才生效）
        os.environ["MOBIUS_ROOT"] = str(proj2)
        check("MOBIUS_ROOT 优先", _norm(shared.find_root(tmp / "unrelated")) == _norm(proj2))

        bogus = tmp / "not-a-root"
        bogus.mkdir(exist_ok=True)
        os.environ["MOBIUS_ROOT"] = str(bogus)
        check("MOBIUS_ROOT 无效回退搜索", _norm(shared.find_root(deep)) == _norm(proj))

        # 找不到时返回 start 的规范化绝对路径
        os.environ.pop("MOBIUS_ROOT", None)
        empty = tmp / "empty" / "deep"
        empty.mkdir(parents=True)
        found = shared.find_root(empty, max_levels=1)
        check("找不到返回 start", _norm(found) == _norm(empty) and os.path.isabs(found), found)
        weird = str(empty) + os.sep + ".." + os.sep + "deep"
        check("返回规范化路径",
              _norm(shared.find_root(weird, max_levels=1)) == _norm(os.path.abspath(weird)))
    finally:
        if old_root is None:
            os.environ.pop("MOBIUS_ROOT", None)
        else:
            os.environ["MOBIUS_ROOT"] = old_root


def test_expand(shared, tmp: Path) -> None:
    print("[2] 变量展开")
    keys = ("MOBIUS_RUNTIME", "MOBIUS_MODELS", "MOBIUS_DATA", "MOBIUS_TEST_ENV_FALLBACK")
    old = {k: os.environ.get(k) for k in keys}
    try:
        for k in ("MOBIUS_RUNTIME", "MOBIUS_MODELS", "MOBIUS_DATA"):
            os.environ.pop(k, None)

        root = tmp / "root2"
        root.mkdir(exist_ok=True)
        base = shared.builtin_paths(root=root)
        check("内置 ROOT", _norm(base["ROOT"]) == _norm(root), base["ROOT"])
        check("内置 RUNTIME", _norm(base["RUNTIME"]) == _norm(root / "runtime"))
        check("内置 MODELS", _norm(base["MODELS"]) == _norm(root / "models"))
        check("内置 DATA", _norm(base["DATA"]) == _norm(root / "data"))
        check("内置 LOGS", _norm(base["LOGS"]) == _norm(root / "logs"))

        os.environ["MOBIUS_RUNTIME"] = str(tmp / "env-runtime")
        check("MOBIUS_RUNTIME 覆盖",
              _norm(shared.builtin_paths(root=root)["RUNTIME"]) == _norm(tmp / "env-runtime"))
        os.environ.pop("MOBIUS_RUNTIME", None)

        # cfg.paths 自定义变量 + 嵌套展开
        cfg = {"paths": {
            "AI": "${ROOT}/ai",
            "SHARED_MODELS": "${AI}/models",
            "DEEPEST": "${SHARED_MODELS}/x",
        }}
        mapping = shared.variable_map(cfg, root=root)
        expected = root / "ai" / "models" / "x"
        check("cfg.paths 自定义+嵌套", _norm(mapping["DEEPEST"]) == _norm(expected),
              mapping["DEEPEST"])
        check("expand 使用变量表", _norm(shared.expand("${DEEPEST}", mapping)) == _norm(expected))

        # 环境变量兜底、未知变量保留
        os.environ["MOBIUS_TEST_ENV_FALLBACK"] = "env-val"
        check("${ENV} 回退",
              shared.expand("${MOBIUS_TEST_ENV_FALLBACK}/x", {}) == "env-val/x")
        unknown = "${MOBIUS_NO_SUCH_VAR_123}"
        check("未知变量保留", shared.expand(unknown, {}) == unknown)

        # dict/list 递归
        cfg2 = {
            "root_path": "${ROOT}/a",
            "nested": {"list": ["${MODELS}/m", {"b": "${DATA}/d"}], "n": 42},
        }
        out = shared.expand_config(cfg2, root=root)
        ok = (
            _norm(out["root_path"]) == _norm(root / "a")
            and _norm(out["nested"]["list"][0]) == _norm(root / "models" / "m")
            and _norm(out["nested"]["list"][1]["b"]) == _norm(root / "data" / "d")
            and out["nested"]["n"] == 42
        )
        check("dict/list 递归", ok, str(out["nested"]["list"][1]))

        cfg3 = {"paths": {"CUSTOM": "${ROOT}/custom"}, "p": "${CUSTOM}/x"}
        shared.expand_config(cfg3, root=root)
        check("expand_config 识别 cfg.paths",
              _norm(cfg3["p"]) == _norm(root / "custom" / "x"), cfg3["p"])

        # resolve_path：相对路径按 root 解析，绝对路径保持
        check("相对 resolve_path",
              _norm(shared.resolve_path("data/characters", root=root))
              == _norm(root / "data" / "characters"))
        abs_path = str(tmp / "abs" / "file.txt")
        check("绝对 resolve_path", _norm(shared.resolve_path(abs_path, root=root)) == _norm(abs_path))
        check("${ROOT} resolve_path",
              _norm(shared.resolve_path("${ROOT}/logs", root=root)) == _norm(root / "logs"))
        check("空字符串 resolve_path", shared.resolve_path("", root=root) == "")
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_consistency() -> None:
    print("[3] shared.paths 与 core.paths 一致性")
    from core import paths as core_paths
    from shared import paths as shared_paths

    example = ROOT / "config.example.json"
    check("config.example.json 存在", example.is_file(), str(example))
    cfg = json.loads(example.read_text(encoding="utf-8"))

    a = shared_paths.expand_config(copy.deepcopy(cfg))
    b = core_paths.expand_config(copy.deepcopy(cfg))

    counter = {"n": 0}

    def compare(x, y, trail: str = "") -> str:
        if isinstance(x, dict):
            if not isinstance(y, dict) or list(x) != list(y):
                return trail or "<root>"
            for k in x:
                bad = compare(x[k], y[k], f"{trail}.{k}" if trail else str(k))
                if bad:
                    return bad
            return ""
        if isinstance(x, list):
            if not isinstance(y, list) or len(x) != len(y):
                return trail or "<root>"
            for i, (xa, ya) in enumerate(zip(x, y)):
                bad = compare(xa, ya, f"{trail}[{i}]")
                if bad:
                    return bad
            return ""
        if isinstance(x, str):
            counter["n"] += 1
        return "" if x == y else (trail or "<root>")

    bad = compare(a, b)
    check("逐字段相等", not bad,
          ("首个差异: " + bad) if bad else f"{counter['n']} 个字符串字段")

    raw = example.read_text(encoding="utf-8")
    check("示例配置未被修改", example.read_text(encoding="utf-8") == raw)


def test_launcher_source() -> None:
    print("[4] launcher 源码静态检查")
    src = (ROOT / "launcher" / "mobius_launcher.py").read_text(encoding="utf-8")
    check("不再定义 _path_map", "def _path_map" not in src)
    check("不再定义 _expand_cfg", "def _expand_cfg" not in src)
    check("不再内联 _expand", "def _expand(" not in src)
    check("含 shared/paths.py 动态加载",
          "spec_from_file_location" in src and "shared" in src and "paths.py" in src)
    check("load_config 走 shared.expand_config",
          "expand_config(cfg, root=ROOT)" in src)


def test_py_compile() -> None:
    print("[5] py_compile")
    targets = [
        ROOT / "shared" / "__init__.py",
        ROOT / "shared" / "paths.py",
        ROOT / "core" / "paths.py",
        ROOT / "launcher" / "mobius_launcher.py",
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "py_compile", *map(str, targets)],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    detail = (proc.stderr or proc.stdout or "").strip()[:400]
    check("py_compile 通过", proc.returncode == 0, detail)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="shared-paths-"))
    env_keys = ("MOBIUS_ROOT", "MOBIUS_RUNTIME", "MOBIUS_MODELS", "MOBIUS_DATA")
    old_env = {k: os.environ.get(k) for k in env_keys}
    try:
        from shared import paths as shared_paths

        test_find_root(shared_paths, tmp)
        test_expand(shared_paths, tmp)
        test_consistency()
        test_launcher_source()
        test_py_compile()
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
