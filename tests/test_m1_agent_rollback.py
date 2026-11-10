"""M1 回滚模式（MOBIUS_TASK_OWNER=gateway）Agent 任务记录测试（Core venv）。

验证：回滚时 Agent 恢复本地任务记录（网关本地任务作为 remote），语音同理；
避免"回滚后 Core 任务中心无记录"的回归。隔离临时 sqlite，不连生产。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="m1-agent-rollback-"))
_cfg = _TMP / "config.json"
_cfg.write_text(json.dumps({"db": (_TMP / "core.db").as_posix(), "db_backend": "sqlite",
                            "services": {}}), encoding="utf-8")
os.environ["MOBIUS_CONFIG"] = str(_cfg)
os.environ["MOBIUS_SECRETS_FILE"] = str(_TMP / "absent-secrets.json")
os.environ["CORE_DB_BACKEND"] = "sqlite"
os.environ["MOBIUS_TASK_OWNER"] = "gateway"
os.environ.pop("CORE_DSN", None)
os.environ.pop("CORE_DB_DSN", None)
sys.path.insert(0, str(ROOT))

from core import store  # noqa: E402
from core.agent import Agent  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def run(coro):
    return asyncio.run(coro)


def count_tasks() -> int:
    with store.conn() as c:
        return int(c.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"])


def new_ctx():
    return {"source": "qq", "user_id": "u1", "session_id": "s1", "character": None,
            "task_ids": [], "images": [], "audio": [], "pending_events": []}


class FakeSup:
    async def ensure(self, what):
        return None

    def get_mode(self):
        return "prewarm"


class FakeGw:
    """gateway 回滚模式：本地返回 g_local，不创建 Core 权威任务。"""

    last_id = "g_local"

    async def generate(self, **payload):
        return {"task_id": "g_local", "status": "pending"}

    async def wait_task(self, task_id, timeout=240):
        assert task_id == "g_local"
        return {"status": "completed", "result": {"url": "http://x/i.png", "filename": "i.png"}}

    async def tts(self, text, voice="", engine="kokoro", speed=1.0):
        return {"ok": True, "audio_url": "http://x/a.wav", "filename": "a.wav", "seconds": 1.0}


def main() -> int:
    agent = Agent(FakeSup(), FakeGw())
    try:
        # B1 回滚：生图本地任务
        before = count_tasks()
        res = run(agent._do_image({"prompt": "cat"}, new_ctx()))
        after = count_tasks()
        check("回滚生图返回 ok/completed", res.get("ok") is True and res.get("status") == "completed", str(res)[:100])
        check("回滚生图新增 1 行本地任务", after - before == 1, f"{before}->{after}")
        tid = res.get("task_id")
        t = store.task_get(tid) if tid else None
        check("回滚生图 task_id 为本地任务（非网关 id）", bool(tid) and tid != "g_local", str(tid))
        check("回滚生图本地状态 succeeded", bool(t) and t.get("status") == "succeeded", str((t or {}).get("status")))
        check("回滚生图 remote_id 指向网关 task", bool(t) and t.get("remote_id") == "g_local", str((t or {}).get("remote_id")))

        # B2 回滚：语音本地任务
        before = count_tasks()
        res2 = run(agent._do_voice({"text": "hello"}, new_ctx()))
        after = count_tasks()
        check("回滚语音返回 ok", res2.get("ok") is True, str(res2)[:100])
        check("回滚语音新增 1 行本地任务", after - before == 1, f"{before}->{after}")
        t2 = store.task_get(res2.get("task_id")) if res2.get("task_id") else None
        check("回滚语音本地任务 kind=voice 且 succeeded",
              bool(t2) and t2.get("kind") == "voice" and t2.get("status") == "succeeded",
              str(t2)[:120])
        check("回滚语音 task_id 为本地任务（非网关 id）", bool(res2.get("task_id")) and res2["task_id"] != "g_local")
    finally:
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
