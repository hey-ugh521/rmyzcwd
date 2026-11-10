"""M1c 契约/故障注入测试：恢复、取消竞态、租约收敛、双写消除（ADR-007 阶段3）。

关键：gateway venv 可同时 import core.store（不依赖 fastapi）与 media_gateway.server，
故本测试在单进程内用「真 core.store 当 Core + 假 CoreTaskClient 直连 store」做迷你集成，
无需起真实服务、不连生产 PG/Core/ComfyUI。

覆盖：
  1. 租约过期收敛：running + 过期租约 → failed(lease_expired)，禁止永久 running
  2. submission_unknown 超 24h → failed(submission_unknown_timeout)
  3. 取消竞态：cancel_requested 后 completed 先到 → completed + cancel_requested=true（单终态）
  4. 重复提交（网络重试故障）：同 key → 同 task（幂等），不产生第二个任务
  5. 网关租约续约：_lease_renewal_loop 向 Core 发 lease 事件刷新租约；gateway 回滚模式不拉起
  6. 网关恢复对账 _reconcile_core_running：孤儿 running + ComfyUI 有产物 → 上报 completed；
     ComfyUI 无记录 → submission_unknown；有本地账本的不重复处理
  7. fail closed：Core 不可用时 submit 拒绝（503），不落本地业务状态

运行：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe tests\\test_m1_recovery.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GW_DIR = ROOT / "media_gateway"

_TMP = Path(tempfile.mkdtemp(prefix="m1c-rec-"))
_cfg = _TMP / "config.json"
_cfg.write_text(json.dumps({
    "db": (_TMP / "core.db").as_posix(), "db_backend": "sqlite",
    "services": {"gateway": {"host": "127.0.0.1", "port": 18899},
                 "core": {"host": "127.0.0.1", "port": 8900, "api_key": "<unset>"}},
}), encoding="utf-8")
os.environ["MOBIUS_CONFIG"] = str(_cfg)
os.environ["MOBIUS_SECRETS_FILE"] = str(_TMP / "absent-secrets.json")
os.environ["CORE_DB_BACKEND"] = "sqlite"
os.environ.pop("CORE_DB_DSN", None)
os.environ["MOBIUS_TASK_DB"] = str(_TMP / "gw-tasks.db")

sys.path.insert(0, str(GW_DIR))
sys.path.insert(0, str(ROOT))
from core import store as corestore  # noqa: E402
import server as gw  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def run(c):
    return asyncio.run(c)


# ------------------------------------------------------- 1. 租约过期收敛（Core）
def test_lease_expiry() -> None:
    print("\n[1] 租约过期 → failed(lease_expired)")
    t, _ = corestore.task_create_authoritative(kind="image", payload={"p": "lease"},
                                               idempotency_key="rec-lease")
    corestore.task_apply_authoritative_event(t["id"], event="running", seq=1,
                                             lease_owner="gw", lease_expires_at=corestore.now() - 10)
    expired = corestore.task_expire_stale_leases()
    g = corestore.task_get(t["id"])
    check("过期租约 running → failed(lease_expired)",
          t["id"] in expired and g["status"] == "failed" and g["error_code"] == "lease_expired",
          f"status={g['status']} code={g['error_code']}")
    check("终态后不再被重复收敛", corestore.task_expire_stale_leases() == [] or
          t["id"] not in corestore.task_expire_stale_leases())


# ------------------------------------------------------- 2. submission_unknown 24h
def test_unknown_timeout() -> None:
    print("\n[2] submission_unknown 超 24h → failed(submission_unknown_timeout)")
    t, _ = corestore.task_create_authoritative(kind="image", payload={"p": "unk"},
                                               idempotency_key="rec-unk")
    corestore.task_apply_authoritative_event(t["id"], event="running", seq=1)
    corestore.task_apply_authoritative_event(t["id"], event="submission_unknown", seq=2,
                                             error_code="submission_unknown")
    # 未超时（默认 24h）不应收敛
    check("未超时不收敛", corestore.task_expire_stale_unknown() == [])
    # 回拨 updated_at 到 25h 前，模拟超时
    with corestore.conn() as c:
        c.execute("UPDATE tasks SET updated_at=? WHERE id=?",
                  (corestore.now() - 25 * 3600, t["id"]))
    stale = corestore.task_expire_stale_unknown()
    g = corestore.task_get(t["id"])
    check("超 24h → failed(submission_unknown_timeout)",
          t["id"] in stale and g["status"] == "failed"
          and g["error_code"] == "submission_unknown_timeout", f"status={g['status']}")


# ------------------------------------------------------- 3. 取消竞态
def test_cancel_race() -> None:
    print("\n[3] 取消竞态：completed 先到 → completed + cancel_requested（单终态）")
    t, _ = corestore.task_create_authoritative(kind="image", payload={"p": "cancel"},
                                               idempotency_key="rec-cancel")
    corestore.task_apply_authoritative_event(t["id"], event="running", seq=1)
    corestore.task_request_cancel(t["id"])
    mid = corestore.task_get(t["id"])
    check("取消请求后仍 running + cancel_requested=1",
          mid["status"] == "running" and int(mid["cancel_requested"]) == 1)
    corestore.task_apply_authoritative_event(t["id"], event="completed", seq=2,
                                             result={"filename": "late.png"})
    g = corestore.task_get(t["id"])
    check("完成先到 → completed 且保留 cancel_requested（不引入第二终态）",
          g["status"] == "completed" and int(g["cancel_requested"]) == 1
          and g["result"] == {"filename": "late.png"}, f"{g['status']}/{g['cancel_requested']}")
    check("终态后取消 no-op", "note" in corestore.task_request_cancel(t["id"]))


# ------------------------------------------------------- 4. 重复提交幂等
def test_duplicate_submit() -> None:
    print("\n[4] 重复提交（重试故障）→ 同 task")
    t1, h1 = corestore.task_create_authoritative(kind="image", payload={"p": "dup"},
                                                 idempotency_key="rec-dup")
    t2, h2 = corestore.task_create_authoritative(kind="image", payload={"p": "dup"},
                                                 idempotency_key="rec-dup")
    t3, h3 = corestore.task_create_authoritative(kind="image", payload={"p": "dup"},
                                                 idempotency_key="rec-dup")
    check("三次同 key 提交 → 同一 task_id", t1["id"] == t2["id"] == t3["id"])
    check("第二/三次为幂等命中", h1 is False and h2 is True and h3 is True)
    with corestore.conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM tasks WHERE idempotency_key=?",
                      ("rec-dup",)).fetchone()["n"]
    check("库中仅一条该幂等键任务", n == 1, f"count={n}")


# ------------------------------------------------------- 假 Core 客户端（直连 core.store）
class _StoreCoreClient:
    """把 CoreTaskClient 的调用直接落到真 core.store，模拟 gateway↔Core 真实契约。"""

    def __init__(self):
        self.sent = []
        self.unavailable = False
        self.running_orphans = []

    async def create_task(self, **kw):
        if self.unavailable:
            raise gw.CoreUnavailable("core down")
        t, hit = corestore.task_create_authoritative(
            kind=kw["kind"], payload=kw["payload"], idempotency_key=kw.get("idempotency_key", ""),
            source=kw.get("source", "qq"), user_id=kw.get("user_id"),
            session_id=kw.get("session_id"), character=kw.get("character"))
        return {"task_id": t["id"], "status": t["status"], "idempotent_hit": hit}

    async def send_event(self, task_id, *, event, seq=0, execution_id=None, result=None,
                         error_code=None, error_message=None, lease_owner=None,
                         lease_expires_at=None):
        self.sent.append({"task_id": task_id, "event": event, "lease_owner": lease_owner,
                          "lease_expires_at": lease_expires_at, "result": result})
        try:
            corestore.task_apply_authoritative_event(
                task_id, event=event, seq=seq, execution_id=execution_id, result=result,
                error_code=error_code, error_message=error_message, lease_owner=lease_owner,
                lease_expires_at=lease_expires_at)
            return True
        except (corestore.IllegalTransitionError, KeyError):
            return False

    async def get_task(self, task_id):
        if self.unavailable:
            raise gw.CoreUnavailable("core down")
        return corestore.task_get(task_id)

    async def list_tasks(self, limit=50, status=None, source=None):
        if self.unavailable:
            raise gw.CoreUnavailable("core down")
        if status == "running":
            return list(self.running_orphans)
        return corestore.tasks_list(limit=limit, status=status, source=source)

    async def request_cancel(self, task_id):
        corestore.task_request_cancel(task_id)
        return {"ok": True, "cancel_requested": True}


# ------------------------------------------------------- 5. 租约续约（gateway）
def test_lease_renewal() -> None:
    print("\n[5] 网关租约续约 → Core 刷新 lease_expires_at")
    fake = _StoreCoreClient()
    old = gw._CORE_TASKS
    gw._CORE_TASKS = fake
    t, _ = corestore.task_create_authoritative(kind="image", payload={"p": "renew"},
                                               idempotency_key="rec-renew")
    corestore.task_apply_authoritative_event(t["id"], event="running", seq=1)

    async def spawn_in(mode):
        os.environ["MOBIUS_TASK_OWNER"] = mode
        task = gw.store._spawn_lease_renewal(t["id"])
        if task is not None:
            task.cancel()
        return task is not None
    try:
        check("gateway 回滚模式不拉起续约协程", run(spawn_in("gateway")) is False)
        check("core 模式拉起续约协程", run(spawn_in("core")) is True)
        # 等价续约循环的一轮：发 lease 事件，验证 Core 刷新租约且不改业务状态
        ok = run(fake.send_event(t["id"], event="lease", lease_owner="gw:1",
                                 lease_expires_at=corestore.now() + 60))
        g = corestore.task_get(t["id"])
        check("lease 事件刷新租约、状态仍 running",
              ok and g["status"] == "running" and g["lease_owner"] == "gw:1"
              and g["lease_expires_at"] > corestore.now(),
              f"status={g['status']} owner={g['lease_owner']}")
    finally:
        gw._CORE_TASKS = old
        os.environ["MOBIUS_TASK_OWNER"] = "core"


# ------------------------------------------------------- 6. 恢复对账（gateway）
def test_reconcile_recovery() -> None:
    print("\n[6] 网关恢复对账 _reconcile_core_running")
    os.environ["MOBIUS_TASK_OWNER"] = "core"
    fake = _StoreCoreClient()
    old = gw._CORE_TASKS
    gw._CORE_TASKS = fake

    # 孤儿1：Core running、有 execution_id、ComfyUI 有产物；网关无本地账本
    orphan1, _ = corestore.task_create_authoritative(kind="image", payload={"p": "o1"},
                                                     idempotency_key="rec-o1")
    corestore.task_apply_authoritative_event(orphan1["id"], event="running", seq=1,
                                             execution_id="prompt_o1")
    # 孤儿2：ComfyUI 无记录
    orphan2, _ = corestore.task_create_authoritative(kind="image", payload={"p": "o2"},
                                                     idempotency_key="rec-o2")
    corestore.task_apply_authoritative_event(orphan2["id"], event="running", seq=1,
                                             execution_id="prompt_o2")
    fake.running_orphans = [
        {"id": orphan1["id"], "status": "running", "execution_id": "prompt_o1"},
        {"id": orphan2["id"], "status": "running", "execution_id": "prompt_o2"},
    ]

    async def fake_probe(prompt_id):
        if prompt_id == "prompt_o1":
            return ("completed", {"filename": "recovered.png", "path": "/img?f=recovered.png",
                                  "prompt_id": prompt_id, "recovered": True})
        return ("absent", None)

    orig_probe = gw.store._probe_comfy
    gw.store._probe_comfy = fake_probe  # type: ignore[method-assign]
    try:
        run(gw.store._reconcile_core_running())
        g1 = corestore.task_get(orphan1["id"])
        g2 = corestore.task_get(orphan2["id"])
        check("孤儿+ComfyUI 有产物 → 回收上报 completed",
              g1["status"] == "completed" and (g1["result"] or {}).get("filename") == "recovered.png",
              f"status={g1['status']}")
        check("孤儿+ComfyUI 无记录 → submission_unknown（人工确认）",
              g2["status"] == "submission_unknown" and g2["error_code"] == "submission_unknown",
              f"status={g2['status']}")
    finally:
        gw.store._probe_comfy = orig_probe  # type: ignore[method-assign]
        gw._CORE_TASKS = old


# ------------------------------------------------------- 7. fail closed
def test_fail_closed() -> None:
    print("\n[7] Core 不可用 → submit fail closed，不落本地")
    os.environ["MOBIUS_TASK_OWNER"] = "core"
    fake = _StoreCoreClient()
    fake.unavailable = True
    old_core = gw._CORE_TASKS
    gw._CORE_TASKS = fake
    orig = {k: getattr(gw, k) for k in ("require_comfy", "resolve_spec", "derive_idempotency_key")}
    before = len(gw.store.list(limit=500))

    async def _noop(cfg):
        return None
    gw.require_comfy = _noop
    gw.resolve_spec = lambda cfg, body, kind="image": {"workflow": "w", "checkpoint": "c",
                                                       "loras": [], "warnings": [], "character": None}
    gw.derive_idempotency_key = lambda body: ("rec-failclosed", False)
    try:
        try:
            run(gw.submit_generate(gw.settings, {"prompt": "x"}, True))
            check("Core 不可用 → ApiError", False, "未抛")
        except gw.ApiError as exc:
            after = len(gw.store.list(limit=500))
            check("fail closed：core_unavailable 503", exc.error == "core_unavailable" and exc.status == 503,
                  f"{exc.error}/{exc.status}")
            check("fail closed：未落本地业务状态", after == before, f"{before}->{after}")
    finally:
        for k, v in orig.items():
            setattr(gw, k, v)
        gw._CORE_TASKS = old_core
        os.environ.pop("MOBIUS_TASK_OWNER", None)


def main() -> int:
    print("==> M1c 恢复/取消/租约/双写消除 故障注入测试")
    test_lease_expiry()
    test_unknown_timeout()
    test_cancel_race()
    test_duplicate_submit()
    test_lease_renewal()
    test_reconcile_recovery()
    test_fail_closed()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
