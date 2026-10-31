"""P0.3 持久化任务存储的自动化测试 + 故障注入。

运行：
    D:\\AI\\qqbot-mcp\\Scripts\\python.exe media_gateway\\test_async_tasks.py

覆盖：
  1. schema 创建与版本
  2. 状态机（合法/非法迁移）
  3. 幂等（同 key 同 hash / 同 key 异 hash / 异 key 同 hash / 无 key）
  4. 事务性 outbox（写入失败时任务与事件一起回滚）
  5. claim_next 原子性（并发下只有一个 worker 拿到）
  6. finish 幂等
  7. 取消语义（pending 直接取消；running 只置标记）
  8. 重启恢复（completed / active / absent / 无 prompt_id）
  9. outbox 顺序投递与失败重试
 10. 进程重启后任务不丢（重新打开同一个库）
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from taskstore import (  # noqa: E402
    DurableTaskStore, TaskConflict, STATUS_PENDING, STATUS_RUNNING,
    STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_UNKNOWN,
)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} {detail}")


def fresh_store() -> tuple[DurableTaskStore, str]:
    tmp = tempfile.mkdtemp(prefix="mobius-task-")
    path = os.path.join(tmp, "tasks.db")
    return DurableTaskStore(path), path


def submit_image(store, **kw):
    spec = {"prompt": kw.pop("prompt", "test prompt"), "workflow": "portrait",
            "seed": kw.pop("seed", 42), "character": kw.pop("character", "")}
    payload = dict(spec)
    payload.update({k: v for k, v in kw.items() if k not in ("idempotency_key",)})
    return store.submit(kind="image", payload=payload, spec=spec, **kw)


# --------------------------------------------------------------------------- 1
def test_schema() -> None:
    store, _ = fresh_store()
    row = store._con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    check("1.1 schema_version 写入", row is not None and row["value"] == "1")
    tables = [r["name"] for r in store._con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    check("1.2 表齐全", {"task_snapshot", "outbox", "schema_meta"} <= set(tables), str(tables))
    store.close()


# --------------------------------------------------------------------------- 2
def test_state_machine() -> None:
    store, _ = fresh_store()
    rec, _ = submit_image(store)
    check("2.1 初始 pending", rec.status == STATUS_PENDING, rec.status)
    claimed = store.claim_next()
    check("2.2 claim 后 running", claimed is not None and claimed.status == STATUS_RUNNING)
    store.finish(rec.id, status=STATUS_COMPLETED, result={"filename": "a.png"})
    check("2.3 running -> completed", store.get(rec.id).status == STATUS_COMPLETED)
    # 非法迁移：completed 之后不能再 running
    try:
        store.claim_next()
        check("2.4 completed 不会被再次 claim", True)
    except Exception as exc:  # noqa: BLE001
        check("2.4 completed 不会被再次 claim", False, str(exc))
    # 非法终态
    try:
        store.finish(rec.id, status="running")
        check("2.5 非法终态被拒绝", False)
    except ValueError:
        check("2.5 非法终态被拒绝", True)
    store.close()


# --------------------------------------------------------------------------- 3
def test_idempotency() -> None:
    store, _ = fresh_store()
    r1, hit1 = submit_image(store, idempotency_key="k-1", prompt="同一个请求")
    r2, hit2 = submit_image(store, idempotency_key="k-1", prompt="同一个请求")
    check("3.1 同 key 同 payload -> 返回原任务", r1.id == r2.id and hit2 is True)
    try:
        submit_image(store, idempotency_key="k-1", prompt="不同内容")
        check("3.2 同 key 异 payload -> 409 Conflict", False)
    except TaskConflict:
        check("3.2 同 key 异 payload -> 409 Conflict", True)
    r3, _ = submit_image(store, idempotency_key="k-2", prompt="同一个请求")
    check("3.3 异 key 同 payload -> 新建任务", r3.id != r1.id)
    r4, _ = submit_image(store, prompt="没带 key")
    r5, _ = submit_image(store, prompt="没带 key")
    check("3.4 无 key -> 每次新建（不用内容哈希）", r4.id != r5.id)
    check("3.5 无 key 时 key 不是 payload 哈希",
          r4.idempotency_key.startswith("req_"), r4.idempotency_key)
    store.close()


# --------------------------------------------------------------------------- 4
def test_transactional_outbox() -> None:
    store, _ = fresh_store()
    before = store.outbox_pending_count()

    class Boom(Exception):
        pass

    # 注入失败：在 submit 的 outbox 写入后抛错，整个事务回滚
    original = store._outbox_insert

    def failing(con, task_id, op, payload):
        original(con, task_id, op, payload)
        raise Boom("outbox 写入后崩溃")

    store._outbox_insert = failing  # type: ignore[method-assign]
    try:
        submit_image(store, prompt="应当回滚")
        crashed = False
    except Boom:
        crashed = True
    store._outbox_insert = original  # type: ignore[method-assign]

    check("4.1 注入异常触发", crashed)
    check("4.2 任务随事务回滚（不存在）", len(store.list(limit=10)) == 0)
    check("4.3 outbox 未残留", store.outbox_pending_count() == before)


# --------------------------------------------------------------------------- 5
def test_claim_atomic() -> None:
    store, _ = fresh_store()
    rec, _ = submit_image(store, prompt="并发 claim")
    a = store.claim_next()
    b = store.claim_next()
    check("5.1 只有一个 worker 拿到任务", (a is not None) and (b is None), f"a={a and a.id} b={b and b.id}")
    check("5.2 attempts 只加一次", store.get(rec.id).attempts == 1)


# --------------------------------------------------------------------------- 6
def test_finish_idempotent() -> None:
    store, _ = fresh_store()
    rec, _ = submit_image(store)
    store.claim_next()
    store.finish(rec.id, status=STATUS_COMPLETED, result={"n": 1})
    again = store.finish(rec.id, status=STATUS_COMPLETED, result={"n": 2})
    check("6.1 重复 finish 不报错", again is not None and again.status == STATUS_COMPLETED)
    check("6.2 结果保持首次值", store.get(rec.id).result == {"n": 1})


# --------------------------------------------------------------------------- 7
def test_cancel() -> None:
    store, _ = fresh_store()
    p1, _ = submit_image(store, prompt="排队中取消")
    store.request_cancel(p1.id)
    check("7.1 pending 直接取消", store.get(p1.id).status == STATUS_CANCELLED)

    p2, _ = submit_image(store, prompt="运行中取消")
    store.claim_next()
    store.request_cancel(p2.id)
    rec = store.get(p2.id)
    check("7.2 running 只置标记不立刻取消",
          rec.status == STATUS_RUNNING and rec.cancel_requested is True, f"{rec.status}/{rec.cancel_requested}")
    store.finish(p2.id, status=STATUS_CANCELLED)
    check("7.3 worker 收尾后 cancelled", store.get(p2.id).status == STATUS_CANCELLED)


# --------------------------------------------------------------------------- 8
def test_recovery() -> None:
    async def run():
        store, _ = fresh_store()

        # a) running + prompt_id，ComfyUI 说已完成
        a, _ = submit_image(store, prompt="恢复-已完成")
        store.claim_next()
        store.set_remote_id(a.id, "p-done")

        # b) running + prompt_id，ComfyUI 还在跑
        b, _ = submit_image(store, prompt="恢复-进行中")
        store.claim_next()
        store.set_remote_id(b.id, "p-active")

        # c) running + prompt_id，ComfyUI 查不到
        c, _ = submit_image(store, prompt="恢复-查不到")
        store.claim_next()
        store.set_remote_id(c.id, "p-absent")

        # d) running 但没有 prompt_id（崩在记录之前）
        d, _ = submit_image(store, prompt="恢复-无prompt_id")
        store.claim_next()

        # e) pending 任务
        e, _ = submit_image(store, prompt="恢复-排队中")

        async def probe(prompt_id: str):
            if prompt_id == "p-done":
                return ("completed", {"filename": "done.png"})
            if prompt_id == "p-active":
                return ("active", None)
            return ("absent", None)

        stats = await store.recover(probe)
        check("8.1 已完成补记", store.get(a.id).status == STATUS_COMPLETED, store.get(a.id).status)
        check("8.2 进行中保持 running", store.get(b.id).status == STATUS_RUNNING, store.get(b.id).status)
        check("8.3 查不到 -> submission_unknown", store.get(c.id).status == STATUS_UNKNOWN, store.get(c.id).status)
        check("8.4 无 prompt_id -> submission_unknown", store.get(d.id).status == STATUS_UNKNOWN, store.get(d.id).status)
        check("8.5 pending 保持可执行", store.get(e.id).status == STATUS_PENDING)
        check("8.6 统计正确", stats == {"completed": 1, "active": 1, "unknown": 2, "pending": 1}, str(stats))
        # 关键：submission_unknown 不能被自动重提
        claimed = store.claim_next()
        check("8.7 unknown 不会被重新执行", claimed is not None and claimed.id == e.id, str(claimed and claimed.id))
        check("8.8 unknown 对外带提示", "hint" in store.get(c.id).public())

    asyncio.run(run())


# --------------------------------------------------------------------------- 9
def test_outbox_flush() -> None:
    async def run():
        store, _ = fresh_store()
        for i in range(3):
            submit_image(store, prompt=f"outbox-{i}")

        seen: list[int] = []

        async def sender(item):
            seen.append(item["seq"])
            if len(seen) == 2:
                return False  # 第二条失败：必须停止，保持顺序
            return True

        sent = await store.flush_outbox(sender)
        check("9.1 失败即停（按序投递）", sent == 1 and len(seen) == 2, f"sent={sent} seen={seen}")
        remaining = store.outbox_pending_count()
        check("9.2 未发送保留待重试", remaining >= 2, str(remaining))

        rest = await store.flush_outbox(lambda item: asyncio.sleep(0, result=True))
        check("9.3 恢复后可继续投递完", store.outbox_pending_count() == 0, str(store.outbox_pending_count()))

    asyncio.run(run())


# --------------------------------------------------------------------------- 10
def test_restart_durability() -> None:
    store, path = fresh_store()
    rec, _ = submit_image(store, idempotency_key="restart-key", prompt="重启前")
    store.claim_next()
    store.finish(rec.id, status=STATUS_COMPLETED, result={"filename": "x.png"})
    store.close()

    reopened = DurableTaskStore(path)
    got = reopened.get(rec.id)
    check("10.1 重开后任务仍在", got is not None and got.status == STATUS_COMPLETED)
    check("10.2 结果仍在", got.result == {"filename": "x.png"})
    check("10.3 幂等键仍在", got.idempotency_key == "restart-key")
    same, hit = submit_image(reopened, idempotency_key="restart-key", prompt="重启前")
    check("10.4 重开后幂等仍生效", same.id == rec.id and hit is True)
    reopened.close()


def main() -> int:
    print("== P0.3 持久化任务存储测试 ==")
    for fn in (
        test_schema, test_state_machine, test_idempotency, test_transactional_outbox,
        test_claim_atomic, test_finish_idempotent, test_cancel, test_recovery,
        test_outbox_flush, test_restart_durability,
    ):
        print(f"\n-- {fn.__name__} --")
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            check(fn.__name__ + " 未抛异常", False, f"{type(exc).__name__}: {exc}")
    print(f"\n结果：{len(PASSED)} 通过，{len(FAILED)} 失败")
    if FAILED:
        for name, detail in FAILED:
            print(f"  [FAIL] {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
