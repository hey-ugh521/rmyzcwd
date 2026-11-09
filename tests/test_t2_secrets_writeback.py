"""T2 回归：secrets SSOT 写盘收口（core/secrets.py set_value）。

覆盖：
  - set_value 新建文件 + 嵌套键、保留既有其它键、round-trip 可被 get 读回
  - 原子写：无 .tmp 残留
  - 损坏文件（非 JSON / 非 dict）拒绝覆盖并抛错，不静默清空
  - 无键调用报错
  - 路径解析：MOBIUS_SECRETS_FILE 环境变量优先

纯文件系统 + 临时目录，绝不连 PG、绝不碰任何真实 secrets.json。

运行：
  D:\\Anaconda3\\envs\\open-webui\\python.exe tests\\test_t2_secrets_writeback.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import secrets as sec  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="t2-secrets-"))


def test_set_value_basic() -> None:
    print("\n[1] set_value 新建 + 嵌套键 + round-trip")
    d = _tmp()
    target = d / "secrets.json"
    os.environ["MOBIUS_SECRETS_FILE"] = str(target)
    sec.set_value("gateway", "token", value="abc123")
    check("文件被创建", target.is_file())
    data = json.loads(target.read_text(encoding="utf-8"))
    check("嵌套键写入正确", data == {"gateway": {"token": "abc123"}}, json.dumps(data))
    check("get 可读回", sec.get("gateway", "token") == "abc123")


def test_set_value_preserves() -> None:
    print("\n[2] set_value 保留既有其它键")
    d = _tmp()
    target = d / "secrets.json"
    target.write_text(json.dumps({"pg": {"password": "p"}, "core": {"api_key": "k"}}),
                      encoding="utf-8")
    os.environ["MOBIUS_SECRETS_FILE"] = str(target)
    sec.set_value("gateway", "token", value="t")
    data = json.loads(target.read_text(encoding="utf-8"))
    check("既有 pg.password 保留", data.get("pg", {}).get("password") == "p")
    check("既有 core.api_key 保留", data.get("core", {}).get("api_key") == "k")
    check("新键 gateway.token 写入", data.get("gateway", {}).get("token") == "t")
    # 覆盖同键
    sec.set_value("gateway", "token", value="t2")
    check("同键覆盖生效", sec.get("gateway", "token") == "t2")


def test_atomic_no_tmp() -> None:
    print("\n[3] 原子写：无 .tmp 残留")
    d = _tmp()
    target = d / "secrets.json"
    os.environ["MOBIUS_SECRETS_FILE"] = str(target)
    sec.set_value("gateway", "token", value="x")
    leftovers = [p.name for p in d.iterdir() if p.suffix == ".tmp"]
    check("无 .tmp 残留", not leftovers, str(leftovers))
    check("仅生成目标文件", [p.name for p in d.iterdir()] == ["secrets.json"])


def test_corrupt_refused() -> None:
    print("\n[4] 损坏文件拒绝覆盖（不静默清空）")
    d = _tmp()
    target = d / "secrets.json"
    os.environ["MOBIUS_SECRETS_FILE"] = str(target)
    target.write_text("not json {{{", encoding="utf-8")
    try:
        sec.set_value("gateway", "token", value="x")
        check("非 JSON 文件抛错拒绝覆盖", False, "未抛异常")
    except ValueError:
        check("非 JSON 文件抛错拒绝覆盖", True)
    check("损坏文件未被改写", target.read_text(encoding="utf-8") == "not json {{{")

    d2 = _tmp()
    t2 = d2 / "secrets.json"
    os.environ["MOBIUS_SECRETS_FILE"] = str(t2)
    t2.write_text("[1,2,3]", encoding="utf-8")  # 合法 JSON 但非 dict
    try:
        sec.set_value("gateway", "token", value="x")
        check("非 dict JSON 抛错拒绝", False, "未抛异常")
    except ValueError:
        check("非 dict JSON 抛错拒绝", True)


def test_no_keys() -> None:
    print("\n[5] 无键调用报错")
    d = _tmp()
    os.environ["MOBIUS_SECRETS_FILE"] = str(d / "secrets.json")
    try:
        sec.set_value(value="x")  # type: ignore[call-arg]
        check("set_value 无键抛 ValueError", False, "未抛异常")
    except ValueError:
        check("set_value 无键抛 ValueError", True)


def test_path_priority() -> None:
    print("\n[6] 路径解析：MOBIUS_SECRETS_FILE 优先")
    d = _tmp()
    target = d / "custom.json"
    os.environ["MOBIUS_SECRETS_FILE"] = str(target)
    check("secrets_path 返回 env 指定路径", sec.secrets_path(d) == target)
    os.environ.pop("MOBIUS_SECRETS_FILE", None)
    # 无 env 时回退链不命中本机生产文件：把 DEFAULT_FALLBACK 也指向不存在路径
    saved = sec.DEFAULT_FALLBACK
    sec.DEFAULT_FALLBACK = d / "absent-fallback.json"
    try:
        check("无 env+无文件时 load 返回空 dict", sec.load(d / "nope") == {})
    finally:
        sec.DEFAULT_FALLBACK = saved


def main() -> int:
    print("==> T2 secrets SSOT 写盘回归")
    old = os.environ.get("MOBIUS_SECRETS_FILE")
    try:
        test_set_value_basic()
        test_set_value_preserves()
        test_atomic_no_tmp()
        test_corrupt_refused()
        test_no_keys()
        test_path_priority()
    finally:
        if old is None:
            os.environ.pop("MOBIUS_SECRETS_FILE", None)
        else:
            os.environ["MOBIUS_SECRETS_FILE"] = old
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
