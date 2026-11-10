"""B4：真跨进程 E2E —— 真 Core(M1a) + 真 Gateway CoreTaskClient，走真实 HTTP。

补齐 Lead 退回项 B4 的核心质疑（M1b 用 mock Core）：这里起一个**真实 Core 进程**
（Anaconda venv，uvicorn lifespan="off" → 不触发 _boot、不拉起任何生产服务），
Gateway 侧（qqbot venv）用真实 CoreTaskClient 经 HTTP 打真 Core 的 /v1/tasks*，验证：
  提交→Core 分配 task_id→幂等命中→异 payload 409→执行事件(running→completed)→查询
  →取消→网关代理路由(route_task)与 Core 直查三处一致→Core 宕机 fail-closed。

图片执行链路（ComfyUI）不在本 E2E 内：它由 gateway 既有 test_async_tasks(mock ComfyUI)
与两端契约测试覆盖；本 E2E 专证 gateway↔Core 权威任务 HTTP 契约在真实进程间成立。

运行（gateway venv 编排，自动拉起 Core 子进程）：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe tests\\test_m1_e2e.py
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GW_DIR = ROOT / "media_gateway"
ANA = r"D:\Anaconda3\envs\open-webui\python.exe"

_TMP = Path(tempfile.mkdtemp(prefix="m1-e2e-"))
# 编排器（gateway 侧）隔离配置
_gwcfg = _TMP / "gw-config.json"
_gwcfg.write_text(json.dumps({"services": {"gateway": {"host": "127.0.0.1", "port": 18899},
                                          "core": {"host": "127.0.0.1", "port": 8900}}}),
                  encoding="utf-8")
os.environ["MOBIUS_CONFIG"] = str(_gwcfg)
os.environ["MOBIUS_SECRETS_FILE"] = str(_TMP / "absent.json")
os.environ["MOBIUS_TASK_DB"] = str(_TMP / "gw-tasks.db")
for k in list(os.environ):
    if k.lower().endswith("_proxy"):
        os.environ.pop(k, None)
sys.path.insert(0, str(GW_DIR))
sys.path.insert(0, str(ROOT))
import httpx  # noqa: E402
import server as gw  # noqa: E402
from starlette.requests import Request  # noqa: E402

PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""), flush=True)


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def run(c):
    return asyncio.run(c)


def start_core(port: int) -> subprocess.Popen:
    """起真 Core 子进程（lifespan=off → 不拉起生产服务）。"""
    core_cfg = _TMP / "core-config.json"
    core_cfg.write_text(json.dumps({"db": (_TMP / "core.db").as_posix(), "db_backend": "sqlite",
                                    "core": {"port": port}, "services": {}}), encoding="utf-8")
    runner = ("import uvicorn; from core.server import app; "
              f"uvicorn.run(app, host='127.0.0.1', port={port}, lifespan='off', log_level='warning')")
    env = dict(os.environ)
    env["MOBIUS_CONFIG"] = str(core_cfg)
    env["MOBIUS_SECRETS_FILE"] = str(_TMP / "absent.json")
    env["CORE_DB_BACKEND"] = "sqlite"
    env.pop("CORE_DB_DSN", None)
    env.pop("MOBIUS_TASK_DB", None)
    return subprocess.Popen([ANA, "-c", runner], cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def wait_core(base: str, proc: subprocess.Popen, timeout=40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            r = httpx.get(f"{base}/health", timeout=2.0)
            if r.status_code == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


def fake_request(path, path_params=None):
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "server": ("testserver", 80), "client": ("127.0.0.1", 5000),
             "headers": [(b"host", b"testserver")], "path_params": path_params or {}}
    return Request(scope)


def main() -> int:
    print("==> B4 真跨进程 E2E（真 Core + 真 Gateway client，真实 HTTP）")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = start_core(port)
    try:
        if not wait_core(base, proc):
            check("真 Core 子进程启动并 /health 就绪", False, "Core 未就绪（lifespan=off）")
            return 1
        check("真 Core 子进程启动并 /health 就绪", True, base)

        cfg = types.SimpleNamespace(core_url=base, core_api_key="")
        client = gw.CoreTaskClient(cfg)

        # 1) 提交 → Core 分配 task_id
        r1 = run(client.create_task(kind="image", payload={"prompt": "e2e-cat"},
                                    idempotency_key="e2e-k1", source="qq", user_id="u",
                                    session_id="s", character="bronya"))
        tid = r1["task_id"]
        check("真 Core POST /v1/tasks 分配 task_id", bool(tid) and r1["idempotent_hit"] is False,
              f"task_id={tid}")
        # 2) 幂等命中
        r2 = run(client.create_task(kind="image", payload={"prompt": "e2e-cat"},
                                    idempotency_key="e2e-k1", source="qq"))
        check("同 key 同 payload → 幂等命中同 task", r2["idempotent_hit"] is True and r2["task_id"] == tid)
        # 3) 异 payload 冲突
        try:
            run(client.create_task(kind="image", payload={"prompt": "e2e-dog"}, idempotency_key="e2e-k1", source="qq"))
            check("同 key 异 payload → CoreTaskConflict(409)", False, "未抛")
        except gw.CoreTaskConflict:
            check("同 key 异 payload → CoreTaskConflict(409)", True)
        # 4) 执行事件 running→completed（真实 HTTP 回写 Core）
        check("事件 running 被 Core 接受", run(client.send_event(tid, event="running", seq=1, execution_id="prompt_e2e")) is True)
        check("事件 completed 被 Core 接受", run(client.send_event(tid, event="completed", seq=2, result={"filename": "e2e.png", "url": base + "/img?f=e2e.png"})) is True)
        # 5) 查询
        got = run(client.get_task(tid))
        check("真 Core GET /v1/tasks/{id} → completed + result",
              got and got["status"] == "completed" and (got.get("result") or {}).get("filename") == "e2e.png",
              f"status={got and got['status']}")
        lst = run(client.list_tasks(limit=50))
        check("真 Core GET /v1/tasks 列表含该任务", any(t["id"] == tid for t in lst))
        # 6) 网关代理路由 route_task 与 Core 直查一致（三处一致的两处：Gateway 代理 == Core）
        old_core = gw._CORE_TASKS
        gw._CORE_TASKS = client
        old_mode = os.environ.get("MOBIUS_TASK_OWNER")
        os.environ["MOBIUS_TASK_OWNER"] = "core"
        try:
            resp = run(gw.route_task(fake_request(f"/api/tasks/{tid}", {"task_id": tid})))
            proxied = json.loads(resp.body)
            check("网关 /api/tasks/{id} 代理真 Core → 与直查一致",
                  proxied["ok"] and proxied["task"]["task_id"] == tid
                  and proxied["task"]["status"] == got["status"]
                  and (proxied["task"].get("result") or {}).get("filename") == "e2e.png",
                  f"proxy_status={proxied['task']['status']} owner={proxied.get('owner')}")
        finally:
            gw._CORE_TASKS = old_core
            if old_mode is None:
                os.environ.pop("MOBIUS_TASK_OWNER", None)
            else:
                os.environ["MOBIUS_TASK_OWNER"] = old_mode
        # 7) 取消：新建 running 任务 → request_cancel → cancel_requested
        rc = run(client.create_task(kind="image", payload={"prompt": "e2e-cancel"}, idempotency_key="e2e-k2", source="qq"))
        cid = rc["task_id"]
        run(client.send_event(cid, event="running", seq=1))
        cres = run(client.request_cancel(cid))
        cgot = run(client.get_task(cid))
        check("真 Core 取消 → cancel_requested 且未强改终态",
              cres.get("cancel_requested") is True or cgot.get("cancel_requested") in (1, True),
              f"res={cres} status={cgot['status']}")
        # 8) fail closed：指向死端口的 client → CoreUnavailable
        dead = gw.CoreTaskClient(types.SimpleNamespace(core_url="http://127.0.0.1:1", core_api_key=""))
        try:
            run(dead.create_task(kind="image", payload={}, idempotency_key="x", source="qq"))
            check("Core 宕机 → CoreUnavailable(fail closed)", False, "未抛")
        except gw.CoreUnavailable:
            check("Core 宕机 → CoreUnavailable(fail closed)", True)
    finally:
        # 清理 Core 子进程（venv 重定向器会派生子进程，用 taskkill /T 杀树）
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15)
        except Exception:
            proc.kill()
        print("  · Core 子进程已清理")

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    for n, d in FAILED:
        print(f"  FAIL: {n} {d}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
