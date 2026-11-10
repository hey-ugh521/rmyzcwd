"""M2c 资源调度器测试（gateway venv，隔离，不依赖真实后端）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from providers.resources import (  # noqa: E402
    ResourceCanceled,
    ResourceManager,
    ResourceTimeout,
    UnknownResource,
)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


async def test_exclusive() -> None:
    print("\n[gpu_heavy 独占]")
    mgr = ResourceManager({"gpu_heavy": 1, "cpu": 2})
    l1 = await mgr.acquire("gpu_heavy", "task-1", timeout=0.5)
    check("首次 acquire 成功", l1.resource == "gpu_heavy" and mgr.busy("gpu_heavy"))
    try:
        await mgr.acquire("gpu_heavy", "task-2", timeout=0.15)
        check("GPU heavy 不允许并发", False)
    except ResourceTimeout:
        check("GPU heavy 不允许并发", True)
    mgr.release(l1)
    l2 = await mgr.acquire("gpu_heavy", "task-2", timeout=0.5)
    check("release 后可再次 acquire", l2.owner == "task-2")
    mgr.release(l2)
    mgr.release(l2)
    check("release 幂等", not mgr.busy("gpu_heavy"))


async def test_shared() -> None:
    print("\n[cpu 共享]")
    mgr = ResourceManager({"gpu_heavy": 1, "cpu": 2})
    a = await mgr.acquire("cpu", "k1", timeout=0.5)
    b = await mgr.acquire("cpu", "k2", timeout=0.5)
    check("cpu 并发上限 2", len(mgr.snapshot()["resources"]["cpu"]["leases"]) == 2)
    try:
        await mgr.acquire("cpu", "k3", timeout=0.12)
        check("cpu 超限排队超时", False)
    except ResourceTimeout:
        check("cpu 超限排队超时", True)
    mgr.release(a)
    c = await mgr.acquire("cpu", "k3", timeout=0.5)
    check("释放后 cpu 可再入", c.owner == "k3")
    for lease in (b, c):
        mgr.release(lease)


async def test_fifo() -> None:
    print("\n[FIFO 队列]")
    mgr = ResourceManager({"gpu_heavy": 1})
    order: list[str] = []
    lease = await mgr.acquire("gpu_heavy", "holder", timeout=0.5)

    async def worker(name: str):
        l = await mgr.acquire("gpu_heavy", name, timeout=2.0)
        order.append(name)
        await asyncio.sleep(0.02)
        mgr.release(l)

    tasks = [asyncio.create_task(worker(n)) for n in ("w1", "w2", "w3")]
    await asyncio.sleep(0.05)
    mgr.release(lease)
    await asyncio.gather(*tasks)
    check("FIFO 顺序", order == ["w1", "w2", "w3"], str(order))


async def test_cancel() -> None:
    print("\n[取消等待]")
    mgr = ResourceManager({"gpu_heavy": 1})
    lease = await mgr.acquire("gpu_heavy", "holder", timeout=0.5)
    canceled = {"v": False}

    async def waiter():
        return await mgr.acquire("gpu_heavy", "w", timeout=2.0,
                                 cancel_check=lambda: canceled["v"])

    t = asyncio.create_task(waiter())
    await asyncio.sleep(0.03)
    canceled["v"] = True
    try:
        await t
        check("排队中取消 → ResourceCanceled", False)
    except ResourceCanceled:
        check("排队中取消 → ResourceCanceled", True)
    mgr.release(lease)
    check("取消后队列清空", mgr.snapshot()["resources"]["gpu_heavy"]["queued"] == 0)


async def test_lease_and_snapshot() -> None:
    print("\n[租约/快照]")
    mgr = ResourceManager({"gpu_heavy": 1, "cpu": 2}, default_ttl=0.05)
    lease = await mgr.acquire("gpu_heavy", "t", timeout=0.5)
    snap = mgr.snapshot()
    check("snapshot 含 capacity/active/queued/leases",
          snap["resources"]["gpu_heavy"]["capacity"] == 1
          and snap["resources"]["gpu_heavy"]["active"] == 1
          and snap["resources"]["gpu_heavy"]["leases"][0]["owner"] == "t")
    await asyncio.sleep(0.1)
    expired = mgr.reap_expired()
    check("过期租约回收", len(expired) == 1 and expired[0]["owner"] == "t")
    check("回收后 active=0", mgr.snapshot()["resources"]["gpu_heavy"]["active"] == 0)
    mgr.renew(lease, 10)
    # lease 已不在 active；renew 不应崩溃
    try:
        await mgr.acquire("bad", "x", timeout=0.1)
        check("未知资源拒绝", False)
    except UnknownResource:
        check("未知资源拒绝", True)


async def test_owner_fairness() -> None:
    print("\n[队列 owner 可见]")
    mgr = ResourceManager({"gpu_heavy": 1})
    lease = await mgr.acquire("gpu_heavy", "a", timeout=0.5)
    t = asyncio.create_task(mgr.acquire("gpu_heavy", "b", timeout=1.0))
    await asyncio.sleep(0.03)
    snap = mgr.snapshot()["resources"]["gpu_heavy"]
    check("等待队列可见", snap["queued"] == 1)
    mgr.release(lease)
    got = await t
    check("等待者获得租约", got.owner == "b")
    mgr.release(got)


async def main_async() -> int:
    await test_exclusive()
    await test_shared()
    await test_fifo()
    await test_cancel()
    await test_lease_and_snapshot()
    await test_owner_fairness()
    return 0


def main() -> int:
    asyncio.run(main_async())
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
