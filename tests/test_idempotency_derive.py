"""服务端幂等 key 派生回归测试（纯函数，不连真实网关 / 真实任务库）。

背景（2026-11-08）：
  submit_generate 原本只在调用方显式传 idempotency_key 时才幂等；MCP 工具把
  idempotency_key 定义为「可选，由 LLM 自己生成」，LLM 漏传就完全失去幂等。
  修复：由服务端按请求上下文派生稳定 key——
    1. 显式 body["idempotency_key"] 优先；
    2. 否则有 OneBot 消息标识（message_id / event_id）或 session_id + user_id 时，
       对规范化字段串取 sha256 hex 前 32 位；
    3. 完全没有上下文标识时返回 None（保持原来的随机 key 行为）。

运行：
  D:\\Anaconda3\\envs\\open-webui\\python.exe tests\\test_idempotency_derive.py
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe tests\\test_idempotency_derive.py

隔离说明：
  导入 server.py 会构造 Settings / TaskStore 并注册 MCP 工具。测试先把
  MOBIUS_TASK_DB 指向临时目录、MOBIUS_CONFIG 指向不存在的文件，并预置 logging
  handler（让 server._setup_logging 提前返回），避免触碰运行中网关的任务库与
  gateway.log；结束时关闭临时 store 并清理临时目录。
  若解释器缺少 mcp 2.x（例如 open-webui 环境的 mcp 版本较旧），则退回
  「只解析 server.py 中幂等派生纯函数源码」的最小导入方式，不影响本测试目的。
"""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SERVER_PY = ROOT / "media_gateway" / "server.py"

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []
_TMP_DIR = ""

HEX32 = re.compile(r"^[0-9a-f]{32}$")
_PURE_NAMES = {
    "_idem_text",
    "_idem_json",
    "canonical_idempotency_source",
    "derive_idempotency_key",
}


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} {detail}")


# ---------------------------------------------------------------------------
# 被测函数加载（优先真实导入；缺依赖时最小解析源码）
# ---------------------------------------------------------------------------


def _import_gateway_module():
    """真实导入 server.py，但把副作用限制在临时目录。"""
    global _TMP_DIR
    _TMP_DIR = tempfile.mkdtemp(prefix="mobius_idem_test_")
    os.environ["MOBIUS_TASK_DB"] = os.path.join(_TMP_DIR, "tasks.db")
    os.environ["MOBIUS_TASK_STORE"] = "sqlite"
    os.environ["MOBIUS_CONFIG"] = os.path.join(_TMP_DIR, "missing-config.json")
    # 预置 root handler，让 server._setup_logging 提前 return，不写真实 gateway.log
    logging.basicConfig(level=logging.CRITICAL)
    sys.path.insert(0, str(ROOT / "media_gateway"))
    try:
        spec = importlib.util.spec_from_file_location("mobius_gateway_server", SERVER_PY)
        if spec is None or spec.loader is None:  # pragma: no cover - 文件不存在时
            raise ImportError(f"无法加载 {SERVER_PY}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        shutil.rmtree(_TMP_DIR, ignore_errors=True)
        _TMP_DIR = ""
        raise


def _extract_pure_functions():
    """最小导入：只把 server.py 中的幂等派生纯函数编译进隔离命名空间。"""
    tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"), filename=str(SERVER_PY))
    picked: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_IDEM_SOURCE_FIELDS"
            for target in node.targets
        ):
            picked.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in _PURE_NAMES:
            picked.append(node)
    found = {node.name for node in picked if isinstance(node, ast.FunctionDef)}
    missing = _PURE_NAMES - found
    if missing:
        raise RuntimeError(f"server.py 缺少幂等派生函数: {sorted(missing)}")
    module = ast.Module(body=picked, type_ignores=[])
    code = compile(ast.fix_missing_locations(module), str(SERVER_PY), "exec")
    namespace: dict[str, Any] = {"hashlib": hashlib, "json": json, "Any": Any}
    exec(code, namespace)  # noqa: S102 - 仅执行本仓库 server.py 中已声明的纯函数
    names = set(_PURE_NAMES) | {"_IDEM_SOURCE_FIELDS"}
    return types.SimpleNamespace(**{name: namespace[name] for name in names})


def load_derive_functions():
    try:
        return _import_gateway_module()
    except Exception as exc:  # noqa: BLE001
        print(
            f"  NOTE  当前解释器无法导入 media_gateway/server.py"
            f"（{type(exc).__name__}: {exc}），改为直接解析其中的幂等派生纯函数。"
        )
        return _extract_pure_functions()


# ---------------------------------------------------------------------------
# 基础请求（模拟 MCP / REST 传入的 body）
# ---------------------------------------------------------------------------

_BASE_BODY = {
    "source": "qqbot",
    "message_id": "msg-20261108-0001",
    "user_id": "u-100",
    "session_id": "s-200",
    "prompt": "海边日落，少女侧脸",
    "negative": "模糊, 低清",
    "model": "qwen_image_2_1",
    "workflow": "qwen",
    "checkpoint": "",
    "character": "",
    "width": 768,
    "height": 1024,
    "seed": 123456,
    "reference_image": "",
}


# ---------------------------------------------------------------------------
# 1) 显式 key 优先
# ---------------------------------------------------------------------------


def test_explicit_key_priority(gw) -> None:
    body = dict(_BASE_BODY, idempotency_key="explicit-key-1")
    key, derived = gw.derive_idempotency_key(body)
    check("显式 key 优先且不标记派生", key == "explicit-key-1" and derived is False, f"{key!r}/{derived}")

    padded, padded_derived = gw.derive_idempotency_key({**body, "idempotency_key": "  padded  "})
    check("显式 key 去首尾空白", padded == "padded" and padded_derived is False, repr(padded))

    k3, d3 = gw.derive_idempotency_key({"message_id": "same", "prompt": "x", "idempotency_key": "a"})
    k4, d4 = gw.derive_idempotency_key({"message_id": "same", "prompt": "x", "idempotency_key": "b"})
    check("显式 key 不被上下文覆盖", k3 != k4 and not d3 and not d4)

    derived_only, _ = gw.derive_idempotency_key(
        {name: value for name, value in body.items() if name != "idempotency_key"}
    )
    check("显式 key 与派生 key 不同源", key != derived_only, f"{key} vs {derived_only}")

    blank_key, blank_derived = gw.derive_idempotency_key(
        {"idempotency_key": "   ", "message_id": "m-1", "prompt": "一只猫"}
    )
    check(
        "空白显式 key 视为未传（走派生）",
        blank_derived is True and bool(HEX32.fullmatch(blank_key or "")),
        repr(blank_key),
    )


# ---------------------------------------------------------------------------
# 2) 同 message_id + 同 payload 派生稳定
# ---------------------------------------------------------------------------


def test_stable_derivation(gw) -> None:
    body = dict(_BASE_BODY)
    k1, d1 = gw.derive_idempotency_key(body)
    k2, d2 = gw.derive_idempotency_key(copy.deepcopy(body))
    check("同 message_id + 同 payload 派生稳定", k1 == k2 and d1 is True and d2 is True, f"{k1} vs {k2}")

    shuffled = {name: body[name] for name in reversed(list(body))}
    k3, _ = gw.derive_idempotency_key(shuffled)
    check("字段顺序不影响派生", k3 == k1, f"{k1} vs {k3}")

    padded = dict(body, prompt="  海边日落，少女侧脸  ", model=" qwen_image_2_1 ")
    k4, _ = gw.derive_idempotency_key(padded)
    check("字符串首尾空白归一化", k4 == k1, f"{k1} vs {k4}")

    event_body = {name: value for name, value in body.items() if name not in ("message_id", "event_id")}
    event_body["event_id"] = "evt-1"
    ke1, de1 = gw.derive_idempotency_key(event_body)
    ke2, _ = gw.derive_idempotency_key(copy.deepcopy(event_body))
    check("event_id 可单独作为上下文标识且稳定", de1 is True and ke1 == ke2 and bool(HEX32.fullmatch(ke1 or "")))


# ---------------------------------------------------------------------------
# 3) 不同 message_id / prompt 等派生不同 key
# ---------------------------------------------------------------------------


def test_different_inputs(gw) -> None:
    base = dict(_BASE_BODY)
    k0, _ = gw.derive_idempotency_key(base)
    cases = (
        ("message_id", {"message_id": "msg-other"}),
        ("prompt", {"prompt": "森林清晨，少女侧脸"}),
        ("negative", {"negative": "过曝"}),
        ("model", {"model": "anime_illustrious"}),
        ("workflow", {"workflow": "portrait"}),
        ("character", {"character": "幽兰黛尔"}),
        ("width", {"width": 1024}),
        ("height", {"height": 512}),
        ("seed", {"seed": 654321}),
        ("reference_image", {"reference_image": "upload-abc"}),
        ("source", {"source": "console"}),
    )
    for name, patch in cases:
        changed, derived = gw.derive_idempotency_key({**base, **patch})
        check(f"不同 {name} 派生不同 key", changed != k0 and derived is True, f"{k0} vs {changed}")

    a, _ = gw.derive_idempotency_key({"message_id": "x", "prompt": "p"})
    b, _ = gw.derive_idempotency_key({"event_id": "x", "prompt": "p"})
    check("message_id 与 event_id 语义区分", a != b, f"{a} vs {b}")

    c, _ = gw.derive_idempotency_key({"user_id": "u", "session_id": "s1", "prompt": "p"})
    d, _ = gw.derive_idempotency_key({"user_id": "u", "session_id": "s2", "prompt": "p"})
    check("不同 session_id 派生不同 key", c != d, f"{c} vs {d}")


# ---------------------------------------------------------------------------
# 4) 无上下文标识返回 None（保持原行为）
# ---------------------------------------------------------------------------


def test_no_context(gw) -> None:
    bodies = (
        {"prompt": "一只猫"},
        {"source": "console", "prompt": "一只猫", "model": "qwen_image_2_1"},
        {"user_id": "u-1", "prompt": "一只猫"},
        {"session_id": "s-1", "prompt": "一只猫"},
        {},
    )
    for body in bodies:
        key, derived = gw.derive_idempotency_key(body)
        check(
            f"无上下文返回 None（字段={sorted(body)}）",
            key is None and derived is False,
            f"{key!r}/{derived}",
        )


# ---------------------------------------------------------------------------
# 5) session_id + user_id 兜底派生
# ---------------------------------------------------------------------------


def test_session_user_fallback(gw) -> None:
    body = {
        "source": "qqbot",
        "session_id": "group-123",
        "user_id": "user-456",
        "prompt": "一只橘猫",
        "width": 512,
        "height": 512,
    }
    k1, d1 = gw.derive_idempotency_key(body)
    k2, d2 = gw.derive_idempotency_key(copy.deepcopy(body))
    check("session_id+user_id 可派生且稳定", d1 is True and d2 is True and k1 == k2, str(k1))

    k3, d3 = gw.derive_idempotency_key({**body, "prompt": "一只黑猫"})
    check("session_id+user_id 下不同 prompt 派生不同", k3 != k1 and d3 is True, f"{k1} vs {k3}")

    missing_user = {name: value for name, value in body.items() if name != "user_id"}
    check("只有 session_id 不派生", gw.derive_idempotency_key(missing_user) == (None, False))


# ---------------------------------------------------------------------------
# 6) 派生 key 形状与哈希规则
# ---------------------------------------------------------------------------


def test_key_shape(gw) -> None:
    body = dict(_BASE_BODY)
    key, derived = gw.derive_idempotency_key(body)
    check("派生 key 为 32 位小写 hex", bool(HEX32.fullmatch(key or "")), repr(key))
    check("派生标记为 True", derived is True, repr(derived))

    canonical = gw.canonical_idempotency_source(body)
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    check("派生 key = sha256(规范化串) 前 32 位", key == expected, f"{key} vs {expected}")

    # 规范化串只收白名单字段：idempotency_key 与传输层参数不能影响派生结果
    noisy = dict(body, idempotency_key="should-not-matter", **{"async": True}, timeout_sec=999)
    check("规范化串不含 idempotency_key/传输层参数", gw.canonical_idempotency_source(noisy) == canonical)

    # 无上下文时同样返回 None，不能意外生成 32 位 hex
    none_key, _ = gw.derive_idempotency_key({"prompt": "x"})
    check("无上下文不产生 32 位 hex", none_key is None)


# ---------------------------------------------------------------------------
# 7) 服务端接入点（静态检查，防止只加纯函数但没接到 submit_generate / MCP 工具）
# ---------------------------------------------------------------------------


def _tool_signature(src: str, tool_name: str) -> str:
    match = re.search(rf"async def {tool_name}\((.*?)\) -> str:", src, re.S)
    return match.group(1) if match else ""


def test_integration_static() -> None:
    src = SERVER_PY.read_text(encoding="utf-8")
    check("submit_generate 调用派生函数", "derive_idempotency_key(body)" in src)
    check("请求返回实际使用的 idempotency_key", src.count('"idempotency_key":') >= 2)
    check("请求返回 idempotency_derived", src.count('"idempotency_derived"') >= 2)

    signature = _tool_signature(src, "comfyui_task_submit")
    check("comfyui_task_submit 新增可选 message_id", 'message_id: str = ""' in signature, signature[-160:])
    check("comfyui_task_submit 新增可选 event_id", 'event_id: str = ""' in signature, signature[-160:])
    check(
        "message_id/event_id 写入 body",
        'body["message_id"] = message_id' in src and 'body["event_id"] = event_id' in src,
    )
    check("工具说明写明按 message_id 派生幂等 key", "服务端会据此派生幂等 key" in src)


# ---------------------------------------------------------------------------


def main() -> int:
    print("==> 幂等 key 派生回归测试")
    gateway = load_derive_functions()
    try:
        print("\n[1] 显式 key 优先")
        test_explicit_key_priority(gateway)
        print("\n[2] 同 message_id + 同 payload 派生稳定")
        test_stable_derivation(gateway)
        print("\n[3] 不同上下文 / payload 派生不同 key")
        test_different_inputs(gateway)
        print("\n[4] 无上下文返回 None")
        test_no_context(gateway)
        print("\n[5] session_id + user_id 兜底派生")
        test_session_user_fallback(gateway)
        print("\n[6] 派生 key 形状与哈希规则")
        test_key_shape(gateway)
        print("\n[7] 服务端接入点（静态检查）")
        test_integration_static()
    finally:
        try:
            gateway.store.close()
        except Exception:  # noqa: BLE001 - 最小导入路径没有 store
            pass
        if _TMP_DIR:
            shutil.rmtree(_TMP_DIR, ignore_errors=True)

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
