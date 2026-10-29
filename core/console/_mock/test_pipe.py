#!/usr/bin/env python3
"""Möbius Pipe 独立测试（不依赖 Open WebUI / Core 真实进程）。

跑法：
    python mobius/core/console/_mock/test_pipe.py

覆盖：
1. 正常 SSE：meta / text×N / task / done → 逐段 yield 文本、事件头与请求头正确；
2. SSE error 事件 → 友好中文提示而不是抛栈；
3. HTTP 500 → 友好提示（含状态码与服务端 message）；
4. Core 未启动（连接拒绝）→ 友好提示（含 CORE_URL 与启动建议）；
5. 空消息 → 友好提示，且不发起请求；
6. pipes() 返回模型显示名 Möbius。
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MOBIUS_DIR = BASE_DIR.parents[2]  # _mock -> console -> core -> mobius
PIPE_PATH = MOBIUS_DIR / "openwebui" / "mobius_pipe.py"

RECORD: dict = {}
STUB_STATE: dict = {"mode": "ok"}

PASSED = 0
FAILED = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print("PASS  %s%s" % (name, ("  — " + detail) if detail else ""))
    else:
        FAILED += 1
        print("FAIL  %s%s" % (name, ("  — " + detail) if detail else ""))


class StubHandler(BaseHTTPRequestHandler):
    """模拟 Core 的 POST /v1/chat。"""

    protocol_version = "HTTP/1.0"  # 流式响应靠连接关闭定界

    def log_message(self, *args):  # 静默
        return

    def _write_event(self, event: str, data: dict) -> None:
        chunk = ("event: %s\ndata: %s\n\n" % (event, json.dumps(data, ensure_ascii=False))).encode("utf-8")
        self.wfile.write(chunk)
        self.wfile.flush()

    def _start_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {}
        RECORD.clear()
        RECORD.update(
            path=self.path,
            headers={k.lower(): v for k, v in self.headers.items()},
            body=body,
        )
        mode = STUB_STATE["mode"]

        if mode == "http_error":
            payload = json.dumps({"error": "boom", "message": "模组炸了"}, ensure_ascii=False).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        if mode == "sse_error":
            self._start_sse()
            self._write_event("meta", {"reply_id": "r_1"})
            self._write_event("error", {
                "error": "media_disabled",
                "message": "媒体模式为 off",
                "hint": "在 Console 概览切换媒体模式",
            })
            self.wfile.flush()
            return

        # 正常流式
        self._start_sse()
        self._write_event("meta", {"reply_id": "r_1"})
        for chunk in ("你好", "，", "博士。"):
            self._write_event("text", {"delta": chunk})
            time.sleep(0.03)
        self._write_event("task", {"id": "t_1", "kind": "image", "status": "queued"})
        self._write_event("done", {"reply": "你好，博士。", "task_ids": ["t_1"]})
        self.wfile.flush()


def start_stub(mode: str):
    handler = type("ModeHandler", (StubHandler,), {})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    STUB_STATE["mode"] = mode
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def load_pipe_module():
    spec = importlib.util.spec_from_file_location("mobius_pipe", PIPE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 %s" % PIPE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["mobius_pipe"] = module
    spec.loader.exec_module(module)
    return module


BODY = {
    "model": "mobius",
    "stream": True,
    "messages": [
        {"role": "system", "content": "old system prompt"},
        {"role": "user", "content": "你好，布洛妮娅"},
    ],
    "metadata": {"chat_id": "c1"},
}


def collect(pipe, body, user=None):
    chunks = []
    events = []
    emitter = events.append

    def iterator():
        for chunk in pipe.pipe(body, __user__=user, __event_emitter__=emitter):
            chunks.append(chunk)
            yield chunk

    gen = iterator()
    while True:
        try:
            next(gen)
        except StopIteration:
            break
        except Exception as exc:  # noqa: BLE001 - 测试断言：不应抛异常
            return chunks, events, exc
    return chunks, events, None


def main() -> int:
    print("加载 Pipe:", PIPE_PATH)
    module = load_pipe_module()
    check("模块加载", hasattr(module, "Pipe"))

    pipe = module.Pipe()
    check("模型显示名 Möbius", pipe.name == "Möbius", pipe.name)
    pipes = pipe.pipes()
    check(
        "pipes() 返回 Möbius",
        isinstance(pipes, list) and pipes and pipes[0].get("name") == "Möbius",
        json.dumps(pipes, ensure_ascii=False),
    )

    # 1. 正常流式
    server, url = start_stub("ok")
    try:
        pipe = module.Pipe()
        pipe.valves.CORE_URL = url
        chunks, events, exc = collect(pipe, BODY, {"id": "u1"})
        out = "".join(chunks)
        check("正常流式：无异常", exc is None, "" if exc is None else repr(exc))
        check("正常流式：正文拼接正确", out == "你好，博士。", repr(out))
        check("正常流式：逐段 yield（≥3 段）", len(chunks) >= 3, "chunks=%d %r" % (len(chunks), chunks))
        check("正常流式：请求路径 /v1/chat", RECORD.get("path") == "/v1/chat", str(RECORD.get("path")))
        check(
            "正常流式：请求头 X-Mobius-Client=openwebui",
            RECORD.get("headers", {}).get("x-mobius-client") == "openwebui",
            str(RECORD.get("headers", {}).get("x-mobius-client")),
        )
        check(
            "正常流式：身份头 X-Mobius-User/Session",
            RECORD.get("headers", {}).get("x-mobius-user") == "web:u1"
            and RECORD.get("headers", {}).get("x-mobius-session") == "web:c1",
            "%s / %s" % (RECORD.get("headers", {}).get("x-mobius-user"),
                         RECORD.get("headers", {}).get("x-mobius-session")),
        )
        sent = RECORD.get("body", {})
        check(
            "正常流式：请求体符合契约 §4.2",
            sent.get("source") == "web" and sent.get("user_id") == "web:u1"
            and sent.get("session_id") == "web:c1" and sent.get("text") == "你好，布洛妮娅",
            json.dumps(sent, ensure_ascii=False),
        )
        check(
            "正常流式：task/done 转成 event_emitter 状态",
            any(e.get("data", {}).get("description", "").find("t_1") >= 0 for e in events)
            and any(e.get("data", {}).get("done") is True for e in events),
            "events=%d" % len(events),
        )
    finally:
        server.shutdown()
        server.server_close()

    # 2. SSE error 事件
    server, url = start_stub("sse_error")
    try:
        pipe = module.Pipe()
        pipe.valves.CORE_URL = url
        chunks, events, exc = collect(pipe, BODY, {"id": "u1"})
        out = "".join(chunks)
        check("SSE error：无异常", exc is None)
        check("SSE error：友好中文提示", out.startswith("\n\n⚠️") and "媒体模式为 off" in out, repr(out))
        check("SSE error：带 hint", "切换媒体模式" in out, repr(out))
    finally:
        server.shutdown()
        server.server_close()

    # 3. HTTP 500
    server, url = start_stub("http_error")
    try:
        pipe = module.Pipe()
        pipe.valves.CORE_URL = url
        chunks, events, exc = collect(pipe, BODY, {"id": "u1"})
        out = "".join(chunks)
        check("HTTP 500：无异常", exc is None)
        check("HTTP 500：友好提示含状态码与 message",
              "HTTP 500" in out and "模组炸了" in out, repr(out))
    finally:
        server.shutdown()
        server.server_close()

    # 4. Core 未启动
    pipe = module.Pipe()
    pipe.valves.CORE_URL = "http://127.0.0.1:%d" % free_port()
    chunks, events, exc = collect(pipe, BODY, {"id": "u1"})
    out = "".join(chunks)
    check("连接失败：无异常", exc is None)
    check("连接失败：友好提示含 CORE_URL / 启动建议",
          "无法连接 Möbius Core" in out and "CORE_URL" in out and "8900" in out, repr(out))

    # 5. 空消息
    pipe = module.Pipe()
    pipe.valves.CORE_URL = "http://127.0.0.1:%d" % free_port()
    chunks, events, exc = collect(pipe, {"messages": [{"role": "assistant", "content": "x"}]})
    out = "".join(chunks)
    check("空消息：无异常且不发请求", exc is None and "没有可发送" in out, repr(out))

    # 6. 多模态 content 数组
    pipe = module.Pipe()
    pipe.valves.CORE_URL = "http://127.0.0.1:%d" % free_port()
    text = module._extract_user_text([
        {"role": "user", "content": [{"type": "text", "text": "你好"}, {"type": "image_url", "image_url": {"url": "x"}}]}
    ])
    check("多模态 content 数组取文本", text == "你好", repr(text))

    print("\n结果: %d 通过, %d 失败" % (PASSED, FAILED))
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
