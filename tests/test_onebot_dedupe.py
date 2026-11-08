"""tests/test_onebot_dedupe.py — OneBot message_id 去重单测（独立脚本）。

被测实现位于本地 LangBot 源码（ops/patches 记录的补丁）：
  D:\\AI\\langbot-native\\src\\langbot\\pkg\\platform\\adapters\\aiocqhttp\\dedupe.py
若该路径不存在（例如 CI/其它机器未应用补丁），测试标记 SKIP 并返回 0。
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

PASSED: list[str] = []
FAILED: list[str] = []
SKIPPED: list[str] = []

SRC = Path(r"D:\AI\langbot-native\src\langbot\pkg\platform\adapters\aiocqhttp\dedupe.py")


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def load_module():
    if not SRC.is_file():
        return None
    spec = importlib.util.spec_from_file_location("onebot_dedupe_local", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    mod = load_module()
    if mod is None:
        print(f"  SKIP  {SRC} 不存在（未应用 OneBot 去重补丁）")
        print("结果: 0/0 通过（SKIP）")
        return 0

    now = [1000.0]
    d = mod.MessageDedupe(ttl=10, max_size=3, clock=lambda: now[0])
    check("首次不重复", d.is_duplicate("a") is False)
    check("窗口内重复", d.is_duplicate("a") is True)
    check("不同 key 独立", d.is_duplicate("b") is False)
    now[0] += 5
    check("TTL 内仍重复", d.is_duplicate("a") is True)
    now[0] += 6  # 距首次 11s > ttl
    check("TTL 过期后不算重复", d.is_duplicate("a") is False)

    # LRU eviction：max_size=3，插入 4 个不同 key 后最早的应被淘汰
    d2 = mod.MessageDedupe(ttl=100, max_size=3, clock=lambda: now[0])
    for k in ("k1", "k2", "k3"):
        d2.is_duplicate(k)
    d2.is_duplicate("k4")  # 淘汰 k1
    check("LRU 淘汰后 k1 可再次通过", d2.is_duplicate("k1") is False and d2.stats()["size"] <= 3,
          str(d2.stats()))

    # TTL=0 关闭
    d3 = mod.MessageDedupe(ttl=0, max_size=3)
    check("TTL=0 关闭去重", d3.enabled is False and d3.is_duplicate("x") is False)

    # 空 key
    check("空 key 不去重", d.is_duplicate("") is False)

    # from_env
    old = {k: os.environ.get(k) for k in ("MOBIUS_ONEBOT_DEDUPE_TTL", "MOBIUS_ONEBOT_DEDUPE_MAX")}
    try:
        os.environ["MOBIUS_ONEBOT_DEDUPE_TTL"] = "0"
        os.environ["MOBIUS_ONEBOT_DEDUPE_MAX"] = "8"
        e = mod.MessageDedupe.from_env()
        check("from_env 读取 TTL/MAX", e.ttl == 0 and e.max_size == 8 and e.enabled is False,
              str(e.stats()))
        os.environ["MOBIUS_ONEBOT_DEDUPE_TTL"] = "bad"
        e2 = mod.MessageDedupe.from_env()
        check("from_env 非法值回退默认", e2.ttl == 600.0)
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
