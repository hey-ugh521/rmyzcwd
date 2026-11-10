"""M1 返工 B1/B2/B3 契约测试（ADR-007 方案A）。

B1（P0）Agent 生图不再预建本地任务 → 一次生图只 1 行权威任务，且用规范词判成功
        （不再把 completed 误判为失败）；失败/超时状态正确。
B2（P0）Agent 语音不再预建本地任务 → 走网关回传的 Core 权威 task_id（kind=speech）。
B3（P0）旧消费方适配规范词（方案A）：tasks_today_counts 规范桶（含历史行归一）、
        tasks_active 含 pending、取消不覆盖终态（task_request_cancel）、/api/tasks 读时归一。

隔离：临时 sqlite，不连生产 PG/Core/ComfyUI。core venv 运行。

运行：
  D:\\Anaconda3\\envs\\open-webui\\python.exe tests\\test_m1_agent_migration.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="m1-agent-"))
_cfg = _TMP / "config.json"
_cfg.write_text(json.dumps({"db": (_TMP / "core.db").as_posix(), "db_backend": "sqlite",
                            "services": {}}), encoding="utf-8")
os.environ["MOBIUS_CONFIG"] = str(_cfg)
os.environ["MOBIUS_SECRETS_FILE"] = str(_TMP / "absent-secrets.json")
os.environ["CORE_DB_BACKEND"] = "sqlite"
os.environ.pop("CORE_DB_DSN", None)
sys.path.insert(0, str(ROOT))

from core import store  # noqa: E402
from core.agent import Agent  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def run(c):
    return asyncio.run(c)


def count_tasks() -> int:
    with store.conn() as c:
        return int(c.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"])


def new_ctx() -> dict:
    return {"source": "qq", "user_id": "u1", "session_id": "s1", "character": None,
            "task_ids": [], "images": [], "audio": [], "pending_events": []}


class FakeSup:
    async def ensure(self, what):
        return None

    def get_mode(self):
        return "prewarm"


class FakeGw:
    """模拟 core 模式网关：generate/tts 经 Core 创建权威任务并回传 task_id。"""

    def __init__(self, final_status="completed", final_result=None):
        self.final_status = final_status
        self.final_result = final_result if final_result is not None else {"url": "http://x/img.png", "filename": "x.png"}
        self.last_id = None

    async def generate(self, **payload):
        t, _ = store.task_create_authoritative(
            kind="image", payload={"prompt": payload.get("prompt")},
            idempotency_key="img-" + str(payload.get("prompt")), source="qq",
            user_id=payload.get("user_id"), session_id=payload.get("session_id"),
            character=payload.get("character"))
        self.last_id = t["id"]
        return {"task_id": t["id"], "status": "pending"}

    async def wait_task(self, task_id, timeout=240):
        store.task_apply_authoritative_event(task_id, event="running", seq=1)
        store.task_apply_authoritative_event(task_id, event=self.final_status, seq=2,
                                             result=self.final_result if self.final_status == "completed" else None,
                                             error_code=None if self.final_status == "completed" else "gen_failed",
                                             error_message=None if self.final_status == "completed" else "boom")
        return store.task_get(task_id)

    async def tts(self, text, voice="", engine="kokoro", speed=1.0):
        t, _ = store.task_create_authoritative(
            kind="speech", payload={"text": text}, idempotency_key="tts-" + text[:16],
            source="qq")
        store.task_apply_authoritative_event(t["id"], event="completed", seq=1,
                                             result={"url": "http://x/a.wav", "filename": "a.wav"})
        self.last_id = t["id"]
        return {"ok": True, "audio_url": "http://x/a.wav", "filename": "a.wav",
                "seconds": 1.0, "task_id": t["id"]}


# ---------------------------------------------------------------- B1 生图
def test_b1_image_single_task() -> None:
    print("\n[B1] Agent 生图：不预建本地任务，一次生图仅 1 行权威任务")
    orig_create = store.task_create
    calls = []
    store.task_create = lambda *a, **k: calls.append((a, k)) or orig_create(*a, **k)
    try:
        before = count_tasks()
        gw = FakeGw(final_status="completed")
        agent = Agent(FakeSup(), gw)
        res = run(agent._do_image({"prompt": "cat"}, new_ctx()))
        after = count_tasks()
        check("Agent 未调用 store.task_create（无本地任务）", not calls, f"calls={len(calls)}")
        check("一次生图只新增 1 行任务", after - before == 1, f"{before}->{after}")
        check("返回规范状态 completed 且 ok", res.get("ok") is True and res.get("status") == "completed",
              str(res)[:100])
        check("task_id 即网关回传的权威 id", res.get("task_id") == gw.last_id)
        g = store.task_get(gw.last_id)
        check("权威任务终态 completed、无第二行", g["status"] == "completed" and count_tasks() == after)
    finally:
        store.task_create = orig_create


def test_b1_image_failure() -> None:
    print("\n[B1] Agent 生图失败/超时状态正确")
    gw = FakeGw(final_status="failed")
    agent = Agent(FakeSup(), gw)
    before = count_tasks()
    res = run(agent._do_image({"prompt": "dog"}, new_ctx()))
    check("失败 → ok=False, error=generate_failed", res.get("ok") is False and res.get("error") == "generate_failed",
          str(res)[:100])
    check("失败仍只 1 行任务", count_tasks() - before == 1)
    check("权威任务终态 failed", store.task_get(gw.last_id)["status"] == "failed")


# ---------------------------------------------------------------- B2 语音
def test_b2_voice_authoritative() -> None:
    print("\n[B2] Agent 语音：走 Core 权威任务（kind=speech），不预建本地任务")
    orig_create = store.task_create
    calls = []
    store.task_create = lambda *a, **k: calls.append((a, k)) or orig_create(*a, **k)
    try:
        before = count_tasks()
        gw = FakeGw()
        agent = Agent(FakeSup(), gw)
        res = run(agent._do_voice({"text": "你好"}, new_ctx()))
        after = count_tasks()
        check("语音未调用 store.task_create", not calls, f"calls={len(calls)}")
        check("语音只新增 1 行权威任务", after - before == 1, f"{before}->{after}")
        check("返回 task_id 即权威 speech 任务", res.get("task_id") == gw.last_id and res.get("ok") is True,
              f"res={res} last_id={gw.last_id}")
        g = store.task_get(gw.last_id) if gw.last_id else None
        check("权威语音任务 kind=speech 且 completed",
              bool(g) and g["kind"] == "speech" and g["status"] == "completed",
              f"g={g and (g['kind'], g['status'])}")
    finally:
        store.task_create = orig_create


# ---------------------------------------------------------------- B3 旧消费方
def test_b3_counters_active() -> None:
    print("\n[B3] tasks_today_counts 规范桶 + tasks_active 含 pending")
    # 权威规范词任务
    store.task_create_authoritative(kind="image", payload={"p": "c1"}, idempotency_key="b3-c1")
    t2, _ = store.task_create_authoritative(kind="image", payload={"p": "c2"}, idempotency_key="b3-c2")
    store.task_apply_authoritative_event(t2["id"], event="running", seq=1)
    store.task_apply_authoritative_event(t2["id"], event="completed", seq=2)
    # 历史词行（模拟旧数据 succeeded）
    legacy = store.task_create("image", "qq", "u", "s", None, "old", status="queued")
    store.task_update(legacy["id"], status="succeeded")
    counts = store.tasks_today_counts()
    check("计数用规范桶键", all(k in counts for k in ("completed", "failed", "cancelled", "running", "pending", "total")),
          str(sorted(counts)))
    check("历史 succeeded 归一计入 completed 桶", counts["completed"] >= 2, f"completed={counts['completed']}")
    check("无旧词 succeeded/queued 桶", "succeeded" not in counts and "queued" not in counts)
    active = store.tasks_active()
    pend = [t for t in store.tasks_list(limit=200) if store.normalize_task_status(t["status"]) == "pending"]
    check("tasks_active 含规范 pending", all(any(a["id"] == p["id"] for a in active) for p in pend),
          f"active={len(active)} pending={len(pend)}")


def test_b3_cancel_no_overwrite() -> None:
    print("\n[B3] 取消不覆盖终态（task_request_cancel）")
    t, _ = store.task_create_authoritative(kind="image", payload={"p": "cancel"}, idempotency_key="b3-cancel")
    store.task_apply_authoritative_event(t["id"], event="running", seq=1)
    store.task_apply_authoritative_event(t["id"], event="completed", seq=2)
    res = store.task_request_cancel(t["id"])
    g = store.task_get(t["id"])
    check("已完成任务取消 → 仍 completed（不被覆盖成 canceled）",
          g["status"] == "completed" and "note" in res, f"status={g['status']}")
    t2, _ = store.task_create_authoritative(kind="image", payload={"p": "c"}, idempotency_key="b3-cancel2")
    store.task_apply_authoritative_event(t2["id"], event="running", seq=1)
    store.task_request_cancel(t2["id"])
    g2 = store.task_get(t2["id"])
    check("运行中取消 → cancel_requested=1 且状态未强改", int(g2["cancel_requested"]) == 1 and g2["status"] == "running")


def test_b3_api_normalize() -> None:
    print("\n[B3] /api/tasks 与 /api/tasks/{id} 读时归一为规范词")
    from fastapi.testclient import TestClient
    from core import server as coreserver
    legacy = store.task_create("image", "qq", "u", "s", None, "old2", status="queued")
    store.task_update(legacy["id"], status="canceled")  # 历史词
    client = TestClient(coreserver.app)
    r = client.get(f"/api/tasks/{legacy['id']}")
    check("GET /api/tasks/{id} 历史 canceled → 规范 cancelled",
          r.status_code == 200 and r.json()["status"] == "cancelled", r.json().get("status"))
    rl = client.get("/api/tasks", params={"limit": 200})
    statuses = {t["id"]: t["status"] for t in rl.json()["tasks"]}
    check("GET /api/tasks 列表历史词已归一", statuses.get(legacy["id"]) == "cancelled")
    check("列表不含旧词 succeeded/canceled/queued",
          not any(s in ("succeeded", "canceled", "queued") for s in statuses.values()))


def main() -> int:
    print("==> M1 返工 B1/B2/B3 契约测试（方案A 规范词）")
    test_b1_image_single_task()
    test_b1_image_failure()
    test_b2_voice_authoritative()
    test_b3_counters_active()
    test_b3_cancel_no_overwrite()
    test_b3_api_normalize()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
