"""tests/test_qq_webhook.py — Core /onebot/event wiring test (standalone).

Uses FastAPI TestClient and a fake adapter agent/sender; no NapCat required.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import core.server as coreserver  # noqa: E402
from core.qq_adapter import QQAdapter  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


class FakeAgent:
    async def run(self, **kwargs):
        yield "done", {"reply": "webhook 测试回复"}


class FakeSender:
    def __init__(self) -> None:
        self.calls = []

    async def __call__(self, kind, target, text) -> None:
        self.calls.append((kind, target, text))


def main() -> int:
    sender = FakeSender()
    adapter = QQAdapter(
        FakeAgent(),
        {"enabled": True, "token": "test-token", "allow_unauthenticated_loopback": False},
        send_action=sender,
    )
    old = coreserver.qq_adapter
    coreserver.qq_adapter = adapter
    client = TestClient(coreserver.app)  # no lifespan: do not start the stack

    event = {
        "post_type": "message",
        "message_type": "private",
        "self_id": 3947356403,
        "user_id": 10001,
        "message_id": 99001,
        "message": [{"type": "text", "data": {"text": "webhook 测试"}}],
    }

    r = client.post("/onebot/event", json=event)
    check("无 token 拒绝", r.status_code == 403, str(r.status_code))

    r = client.post("/onebot/event", json=event,
                    headers={"Authorization": "Bearer test-token"})
    check("合法事件 204", r.status_code == 204, str(r.status_code))
    for _ in range(40):
        if sender.calls:
            break
        time.sleep(0.05)
    check("后台完成发送", sender.calls == [("private", "10001", "webhook 测试回复")], str(sender.calls))

    r = client.get("/api/qq/status")
    check("状态接口", r.status_code == 200 and r.json().get("transport") == "http_webhook",
          str(r.status_code))

    adapter.cfg.enabled = False
    r = client.post("/onebot/event", json=event,
                    headers={"Authorization": "Bearer test-token"})
    check("禁用后 404", r.status_code == 404, str(r.status_code))

    coreserver.qq_adapter = old
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
