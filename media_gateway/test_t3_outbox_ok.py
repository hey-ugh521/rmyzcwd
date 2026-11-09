"""T3 回归：outbox 投递 ok 校验——sender 返回 False 或抛异常时，绝不把条目标记为已发送。

背景：投递函数返回 bool；旧实现忽略返回值会把「假成功」（sender 返回 False）也标记 sent，
事件永久丢失。T3 在两端（sqlite taskstore + pg taskstore）都加了 `if not ok: break`。

隔离：
  - sqlite 端：临时库真实集成（不碰生产）。
  - pg 端：PgTaskStore.__new__ 绕过会连库的 __init__，monkeypatch outbox_pending/
    mark_outbox_sent 为内存实现，flush_outbox 全程不触网、不连 PG。

运行：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe media_gateway\\test_t3_outbox_ok.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from taskstore import DurableTaskStore  # noqa: E402
from pg_taskstore import PgTaskStore  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


# ---------------------------------------------------------------- sqlite 端
def test_sqlite_flush() -> None:
    print("\n[1] sqlite taskstore.flush_outbox（临时库真实集成）")
    tmp = tempfile.mkdtemp(prefix="t3-sqlite-")
    store = DurableTaskStore(os.path.join(tmp, "t3.db"))
    store.submit(kind="image", payload={"p": 1}, spec={}, idempotency_key="a")
    store.submit(kind="image", payload={"p": 2}, spec={}, idempotency_key="b")
    check("提交后 outbox 有 2 条待发", store.outbox_pending_count() == 2,
          str(store.outbox_pending_count()))

    calls: list[int] = []

    async def sender_fail_second(item: dict) -> bool:
        calls.append(item["seq"])
        return len(calls) < 2  # 第一条 True，第二条 False

    n = asyncio.run(store.flush_outbox(sender_fail_second))
    check("ok=False 即停：只发第一条", n == 1, f"sent={n}")
    check("ok=False 的条目未被标记（保留重试）", store.outbox_pending_count() == 1,
          f"pending={store.outbox_pending_count()}")

    async def sender_boom(item: dict) -> bool:
        raise RuntimeError("network down")

    n2 = asyncio.run(store.flush_outbox(sender_boom))
    check("sender 抛异常：不标记已发送", n2 == 0 and store.outbox_pending_count() == 1,
          f"sent={n2} pending={store.outbox_pending_count()}")

    async def sender_ok(item: dict) -> bool:
        return True

    n3 = asyncio.run(store.flush_outbox(sender_ok))
    check("恢复后可投递完", n3 == 1 and store.outbox_pending_count() == 0,
          f"sent={n3} pending={store.outbox_pending_count()}")


# ---------------------------------------------------------------- pg 端（mock）
def _make_pg_mock(pending: list[dict]) -> tuple[PgTaskStore, list[int]]:
    pg = PgTaskStore.__new__(PgTaskStore)  # 绕过 __init__/ensure_schema（会连库）
    pg._dsn = "postgresql://unused"
    pg._outbox_limit = 500
    marked: list[int] = []

    def outbox_pending(limit: int = 100) -> list[dict]:
        return list(pending)

    def mark_outbox_sent(seqs: list[int]) -> None:
        for s in seqs:
            marked.append(s)
            pending[:] = [it for it in pending if it["seq"] != s]

    pg.outbox_pending = outbox_pending  # type: ignore[method-assign]
    pg.mark_outbox_sent = mark_outbox_sent  # type: ignore[method-assign]
    return pg, marked


def test_pg_flush_mock() -> None:
    print("\n[2] pg taskstore.flush_outbox（__new__ + monkeypatch，不连 PG）")
    pending = [{"seq": 1, "op": "task.created"}, {"seq": 2, "op": "task.created"}]
    pg, marked = _make_pg_mock(pending)

    async def sender_fail_second(item: dict) -> bool:
        return item["seq"] != 2

    n = asyncio.run(pg.flush_outbox(sender_fail_second))
    check("pg：ok=False 即停，仅标记 seq=1", n == 1 and marked == [1], f"sent={n} marked={marked}")
    check("pg：seq=2 保留待发", [it["seq"] for it in pending] == [2], str(pending))

    async def sender_boom(item: dict) -> bool:
        raise RuntimeError("boom")

    pg2, marked2 = _make_pg_mock([{"seq": 5}, {"seq": 6}])
    n2 = asyncio.run(pg2.flush_outbox(sender_boom))
    check("pg：sender 抛异常不标记", n2 == 0 and marked2 == [], f"sent={n2} marked={marked2}")

    async def sender_ok(item: dict) -> bool:
        return True

    pg3, marked3 = _make_pg_mock([{"seq": 7}, {"seq": 8}])
    n3 = asyncio.run(pg3.flush_outbox(sender_ok))
    check("pg：全成功则全部标记", n3 == 2 and marked3 == [7, 8], f"sent={n3} marked={marked3}")


def main() -> int:
    print("==> T3 outbox ok 校验回归")
    test_sqlite_flush()
    test_pg_flush_mock()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
