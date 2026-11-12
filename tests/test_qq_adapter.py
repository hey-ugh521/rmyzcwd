r"""tests/test_qq_adapter.py — Core QQ adapter B1 unit tests (standalone).

Run with the Core venv:
  D:\AI\mobius-runtime\python-core\Scripts\python.exe tests\test_qq_adapter.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.qq_adapter import (  # noqa: E402
    MessageDedupe,
    QQAdapter,
    extract_text,
    is_at_self,
    session_for,
    should_handle,
    split_text,
)

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


class FakeAgent:
    def __init__(self, reply: str = "收到，博士。") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        yield "text", {"delta": "收到"}
        yield "done", {"reply": self.reply}


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def __call__(self, kind: str, target: str, text: str) -> None:
        self.calls.append((kind, target, text))


def private_event(**overrides) -> dict:
    event = {
        "post_type": "message",
        "message_type": "private",
        "self_id": 3947356403,
        "user_id": 10001,
        "message_id": 9001,
        "message": [{"type": "text", "data": {"text": "你好"}}],
        "raw_message": "你好",
    }
    event.update(overrides)
    return event


def group_event(**overrides) -> dict:
    event = {
        "post_type": "message",
        "message_type": "group",
        "self_id": 3947356403,
        "user_id": 10001,
        "group_id": 20001,
        "message_id": 9002,
        "message": [
            {"type": "at", "data": {"qq": "3947356403"}},
            {"type": "text", "data": {"text": " 在吗"}},
        ],
        "raw_message": "@苍玄之书 在吗",
    }
    event.update(overrides)
    return event


def test_dedupe() -> None:
    now = [1000.0]
    d = MessageDedupe(ttl=10, max_size=3, clock=lambda: now[0])
    check("dedupe 首次不重复", d.is_duplicate("a") is False)
    check("dedupe 窗口内重复", d.is_duplicate("a") is True)
    now[0] += 6
    check("dedupe TTL 内仍重复", d.is_duplicate("a") is True)
    now[0] += 6
    check("dedupe TTL 过期后放行", d.is_duplicate("a") is False)

    d2 = MessageDedupe(ttl=100, max_size=3, clock=lambda: now[0])
    for key in ("k1", "k2", "k3"):
        d2.is_duplicate(key)
    d2.is_duplicate("k4")
    check("dedupe LRU 淘汰", d2.is_duplicate("k1") is False and d2.stats()["size"] <= 3)
    check("dedupe 空 key 不去重", d2.is_duplicate("") is False)
    check("dedupe ttl=0 关闭", MessageDedupe(ttl=0).is_duplicate("x") is False)


def test_parsing() -> None:
    msg = [
        {"type": "text", "data": {"text": " 你好"}},
        {"type": "image", "data": {"file": "x.png"}},
        {"type": "text", "data": {"text": "，博士 "}},
    ]
    check("extract_text 拼接文本段", extract_text(msg) == "你好，博士")
    check("extract_text raw string", extract_text(" hi ") == "hi")
    check("extract_text 非文本返回空", extract_text([{"type": "image"}]) == "")
    check("is_at_self 命中", is_at_self(msg + [{"type": "at", "data": {"qq": "42"}}], "42") is True)
    check("is_at_self 未命中", is_at_self(msg, "42") is False)


def test_mapping_and_policy() -> None:
    uid, sid, target = session_for(private_event())
    check("私聊 user_id", uid == "qq:10001")
    check("私聊 session_id", sid == "qq:private:10001")
    check("私聊 reply target", target == ("private", "10001"))

    uid, sid, target = session_for(group_event())
    check("群聊 session_id", sid == "qq:group:20001")
    check("群聊 reply target", target == ("group", "20001"))

    cfg = SimpleNamespace(group_require_at=True, allowed_groups=set())
    check("群聊无 @ 忽略", should_handle(group_event(message=[{"type": "text", "data": {"text": "hi"}}]), cfg, "3947356403") is False)
    check("群聊 @ 处理", should_handle(group_event(), cfg, "3947356403") is True)
    cfg2 = SimpleNamespace(group_require_at=False, allowed_groups={"20001"})
    check("群白名单命中", should_handle(group_event(message=[]), cfg2, "3947356403") is True)
    cfg3 = SimpleNamespace(group_require_at=False, allowed_groups={"999"})
    check("群白名单未命中忽略", should_handle(group_event(), cfg3, "3947356403") is False)


def test_split() -> None:
    check("短文本不分割", split_text("hello", 80) == ["hello"])
    text = "a" * 250
    parts = split_text(text, 80)
    check("长文本分割", len(parts) == 4 and all(len(p) <= 80 for p in parts))
    check("空文本不发送", split_text("  ", 80) == [])
    check("优先换行", split_text("a" * 70 + "\n" + "b" * 70, 100)[0] == "a" * 70)


async def test_handle() -> None:
    agent = FakeAgent("测试回复")
    sender = FakeSender()
    adapter = QQAdapter(agent, {"enabled": True, "max_reply_chars": 80}, send_action=sender)
    reply = await adapter.handle_event(private_event())
    check("私聊产生回复", reply == "测试回复")
    check("调用 Agent 参数", agent.calls and agent.calls[0]["source"] == "qq"
          and agent.calls[0]["user_id"] == "qq:10001"
          and agent.calls[0]["session_id"] == "qq:private:10001")
    check("发送私聊 action", sender.calls == [("private", "10001", "测试回复")])

    await adapter.handle_event(private_event(message_id=9001))
    check("重复 message_id 不再回复", len(sender.calls) == 1)

    await adapter.handle_event(private_event(message_id=9003, message=[{"type": "text", "data": {"text": "  "}}]))
    check("空文本忽略", len(sender.calls) == 1)

    await adapter.handle_event(group_event(message_id=9004, message=[{"type": "text", "data": {"text": "没 @"}}]))
    check("群聊无 @ 不回复", len(sender.calls) == 1)

    await adapter.handle_event(group_event(message_id=9005))
    check("群聊 @ 回复", sender.calls[-1] == ("group", "20001", "测试回复"))

    disabled = QQAdapter(agent, {"enabled": False}, send_action=sender)
    check("disabled 忽略", await disabled.handle_event(private_event(message_id=9010)) is None)


def test_auth() -> None:
    adapter = QQAdapter(FakeAgent(), {"enabled": True, "token": "secret"})
    req = lambda host, headers: SimpleNamespace(client=SimpleNamespace(host=host), headers=headers)
    check("无 token 拒绝", adapter.authorized(req("127.0.0.1", {})) is False)
    check("Bearer token 通过", adapter.authorized(req("127.0.0.1", {"authorization": "Bearer secret"})) is True)
    check("X-OneBot-Token 通过", adapter.authorized(req("127.0.0.1", {"x-onebot-token": "secret"})) is True)
    raw = b'{"post_type":"message"}'
    sig = "sha1=" + hmac.new(b"secret", raw, hashlib.sha1).hexdigest()
    check("NapCat X-Signature 通过",
          adapter.authorized(req("127.0.0.1", {"x-signature": sig}), raw) is True)
    check("X-Signature 坏签名拒绝",
          adapter.authorized(req("127.0.0.1", {"x-signature": "sha1=bad"}), raw) is False)
    check("非回环拒绝", adapter.authorized(req("192.168.1.10", {"authorization": "Bearer secret"})) is False)
    open_adapter = QQAdapter(FakeAgent(), {"enabled": True, "token": "",
                                            "allow_unauthenticated_loopback": True})
    check("显式允许无 token 回环", open_adapter.authorized(req("127.0.0.1", {})) is True)


def main() -> int:
    print("[1] dedupe")
    test_dedupe()
    print("[2] parsing")
    test_parsing()
    print("[3] mapping/policy")
    test_mapping_and_policy()
    print("[4] split")
    test_split()
    print("[5] handle")
    asyncio.run(test_handle())
    print("[6] auth")
    test_auth()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
