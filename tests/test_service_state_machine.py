"""tests/test_service_state_machine.py — 服务状态机 / 登录态 / login_probe（独立脚本）。

覆盖：
1. 显式服务状态转移表（合法/非法/同态）
2. 登录态转移表
3. readiness_mode = login_probe 的回退
4. probe_onebot_login 对 OneBot 返回值的判定（本地假 HTTP 服务）
5. _Service.public() 暴露 state_since / login_state 字段
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = b"{}"
        if self.path == "/ok":
            body = json.dumps({"retcode": 0, "data": {"user_id": "3947356403", "nickname": "bot"}}).encode()
        elif self.path == "/fail":
            body = json.dumps({"retcode": 1403, "data": None}).encode()
        elif self.path == "/wrong":
            body = json.dumps({"retcode": 0, "data": {"user_id": "111111"}}).encode()
        elif self.path == "/nonjson":
            body = b"not-json"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静音
        pass


def main() -> int:
    import core.services as svc

    # 1) 服务状态转移表
    for old in svc.SERVICE_STATES:
        allowed = svc.STATE_TRANSITIONS.get(old, set())
        check(f"{old} 同态合法", svc.transition_allowed(old, old))
        for new in svc.SERVICE_STATES:
            expect = new == old or new in allowed
            got = svc.transition_allowed(old, new)
            if got != expect:
                check(f"{old}->{new} 表一致", False, f"expect={expect} got={got}")
    check("非法转移被识别", svc.transition_allowed("disabled", "ready") is False)
    check("合法转移被识别", svc.transition_allowed("down", "ready") is True)

    # 2) 登录态转移表
    for old in svc.LOGIN_STATES:
        check(f"login {old} 同态合法", svc.login_transition_allowed(old, old))
    check("login unknown->logged_in", svc.login_transition_allowed("unknown", "logged_in"))
    check("login awaiting->logged_in", svc.login_transition_allowed("awaiting_login", "logged_in"))
    check("login logged_in->awaiting", svc.login_transition_allowed("logged_in", "awaiting_login"))

    # 3) readiness_mode
    check("login_probe 模式", svc._readiness_mode({"ready_when": "login_probe", "login_probe": {"url": "http://x"}}) == "login_probe")
    check("login_probe 缺 url 回退 ports_all",
          svc._readiness_mode({"ready_when": "login_probe", "ports": [3000, 3001]}) == "ports_all")

    # 4) probe_onebot_login（本地假服务）
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{port}"
    try:
        r = asyncio.run(svc.probe_onebot_login(base + "/ok", "tok", "3947356403"))
        check("登录成功判定", r["ok"] is True and r["login_state"] == "logged_in", str(r))
        r = asyncio.run(svc.probe_onebot_login(base + "/fail", "tok"))
        check("retcode 非 0 → awaiting_login", r["ok"] is False and r["login_state"] == "awaiting_login", str(r))
        r = asyncio.run(svc.probe_onebot_login(base + "/wrong", "tok", "3947356403"))
        check("账号不匹配 → error", r["ok"] is False and r["login_state"] == "error", str(r))
        r = asyncio.run(svc.probe_onebot_login(base + "/nonjson", "tok"))
        check("非 JSON → awaiting_login", r["login_state"] == "awaiting_login", str(r))
        r = asyncio.run(svc.probe_onebot_login("http://127.0.0.1:1/none", "tok", timeout=1))
        check("连接失败 → awaiting_login", r["login_state"] == "awaiting_login", str(r))
    finally:
        srv.shutdown()

    # 5) public 字段
    s = svc._Service("napcat", {"port": 3000, "ready_when": "login_probe",
                                "login_probe": {"url": "http://x"}})
    pub = s.public()
    check("public 含 login_state", pub.get("login_state") == "unknown")
    check("public 含 state_since", "state_since" in pub)
    check("public 含 ready_when", pub.get("ready_when") == "login_probe")

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name in FAILED:
            print("  FAIL:", name)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
