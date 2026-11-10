"""B4 强化：真三进程执行 E2E —— 真 Core + 真 Gateway 服务 + mock ComfyUI。

client 级 test_m1_e2e.py 已证 gateway↔Core 的 HTTP 契约；本用例进一步把**真实网关
服务进程**拉起来（qqbot venv，core 模式），让它自己的 worker 对着 **mock ComfyUI**
（stdlib HTTP，不碰真 GPU/生产）跑完整执行链，事件经事务性 outbox 回写**真 Core**
（Anaconda venv，lifespan=off + 显式收敛循环，不拉起任何生产服务）。覆盖 Lead 退回项 B4
要求的「提交→Core 分配 id→执行事件→查询→取消→崩溃恢复」全链路：

  1. 提交 → Core 分配唯一 task_id（网关本地账本复用该 id，不再双建）
  2. worker 执行 mock ComfyUI → completed 事件经 outbox 回写 Core（result.filename）
  3. 幂等命中：同 key 同 payload 重提 → 同 task_id，不重复执行
  4. 网关 /api/tasks/{id} 代理 == Core 直查（owner=core，三处一致的两处）
  5. 取消 running 任务 → 网关中断 mock ComfyUI → cancelled 回写 Core（单一终态）
  6. 崩溃恢复：真杀网关进程 → Core 收不到续约 → 租约过期收敛 failed(lease_expired)
  7. fail-closed：Core 宕机 → 网关 submit 拒绝 503，绝不落本地业务状态

运行（gateway venv 编排，自动拉起 Core/网关子进程 + 进程内 mock ComfyUI）：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe tests\\test_m1_e2e_exec.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
GW_DIR = ROOT / "media_gateway"
WORKFLOWS = ROOT / "configs" / "workflows"
ANA = r"D:\Anaconda3\envs\open-webui\python.exe"      # Core venv（fastapi/uvicorn/psycopg）
QQBOT = r"D:\AI\qqbot-mcp\Scripts\python.exe"        # Gateway venv（mcp/starlette/uvicorn）

# httpx 0.28.1 遇 NO_PROXY 含 [::1] 会崩；测试环境统一剥掉 *_proxy
for _k in list(os.environ):
    if _k.lower().endswith("_proxy"):
        os.environ.pop(_k, None)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""), flush=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# --------------------------------------------------------------------------- mock ComfyUI
class _MockState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.counter = 0
        self.completed: set[str] = set()   # 有产物的 prompt_id
        self.slow: set[str] = set()        # 仍在跑（history 返回空）的 prompt_id
        self.prompts: dict[str, str] = {}
        self.interrupts = 0


STATE = _MockState()
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)


class _ComfyHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # 静默，避免刷屏
        pass

    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/system_stats":
            self._send(200, {"system": {"comfyui_version": "0.0-mock", "python_version": "3"},
                             "devices": []})
        elif path.startswith("/history/"):
            pid = path[len("/history/"):]
            with STATE.lock:
                done = pid in STATE.completed
            if done:
                self._send(200, {pid: {
                    "outputs": {"7": {"images": [
                        {"filename": "e2e_exec.png", "subfolder": "", "type": "output"}]}},
                    "status": {"status_str": "success", "completed": True}}})
            else:
                self._send(200, {})
        elif path == "/queue":
            with STATE.lock:
                running = [[i + 1, p, {}] for i, p in enumerate(sorted(STATE.slow))]
            self._send(200, {"queue_running": running, "queue_pending": []})
        elif path == "/view":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(_PNG)))
            self.end_headers()
            self.wfile.write(_PNG)
        else:
            self._send(200, {})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            body = {}
        if path == "/prompt":
            nodes = body.get("prompt") or {}
            text = ""
            for n in nodes.values():
                if isinstance(n, dict) and n.get("class_type") == "CLIPTextEncode":
                    text = str((n.get("inputs") or {}).get("text", ""))
                    break
            with STATE.lock:
                STATE.counter += 1
                pid = f"p_mock_{STATE.counter}"
                STATE.prompts[pid] = text
                if "SLOW" in text:
                    STATE.slow.add(pid)
                else:
                    STATE.completed.add(pid)
            self._send(200, {"prompt_id": pid, "number": STATE.counter, "node_errors": {}})
        elif path == "/interrupt":
            with STATE.lock:
                STATE.interrupts += 1
                STATE.completed |= STATE.slow
                STATE.slow.clear()
            self._send(200, {})
        elif path == "/queue":
            with STATE.lock:
                for pid in body.get("delete") or []:
                    STATE.slow.discard(pid)
            self._send(200, {})
        else:
            self._send(200, {})


# --------------------------------------------------------------------------- 子进程
def start_core(port: int, tmp: Path) -> subprocess.Popen:
    """真 Core 子进程：lifespan=off（不拉起生产服务）+ 显式启动任务收敛循环。"""
    cfg = tmp / "core-config.json"
    cfg.write_text(json.dumps({
        "db": (tmp / "core.db").as_posix(), "db_backend": "sqlite",
        "core": {"port": port, "task_convergence_interval_sec": 1}, "services": {},
    }), encoding="utf-8")
    runner = (
        "import asyncio, uvicorn\n"
        "from core.server import app, _task_convergence_loop\n"
        "async def _run():\n"
        "    cfg = uvicorn.Config(app, host='127.0.0.1', port=%d, lifespan='off', log_level='warning')\n"
        "    srv = uvicorn.Server(cfg)\n"
        "    t = asyncio.create_task(_task_convergence_loop())\n"
        "    await srv.serve()\n"
        "asyncio.run(_run())\n" % port
    )
    env = dict(os.environ)
    env["MOBIUS_CONFIG"] = str(cfg)
    env["MOBIUS_SECRETS_FILE"] = str(tmp / "absent.json")
    env["CORE_DB_BACKEND"] = "sqlite"
    env["MOBIUS_TASK_OWNER"] = "core"
    env.pop("CORE_DB_DSN", None)
    env.pop("MOBIUS_TASK_DB", None)
    return subprocess.Popen([ANA, "-c", runner], cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def start_gateway(port: int, core_base: str, mock_base: str, tmp: Path, tag: str) -> subprocess.Popen:
    """真网关服务子进程（core 模式，短租约，指向 mock ComfyUI）。"""
    cfg = tmp / f"gw-config-{tag}.json"
    cfg.write_text(json.dumps({
        "gateway": {"host": "127.0.0.1", "port": port},
        "core": {"host": "127.0.0.1", "port": core_base.rsplit(":", 1)[-1]},
        "services": {},
    }), encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "MOBIUS_CONFIG": str(cfg),
        "MOBIUS_SECRETS_FILE": str(tmp / "absent.json"),
        "MOBIUS_GATEWAY_HOST": "127.0.0.1",
        "MOBIUS_GATEWAY_PORT": str(port),
        "MOBIUS_GATEWAY_PUBLIC_URL": f"http://127.0.0.1:{port}",
        "MOBIUS_COMFYUI_URL": mock_base,
        "MOBIUS_CORE_URL": core_base,
        "MOBIUS_CORE_API_KEY": "",
        "MOBIUS_CORE_TASK_TIMEOUT": "5",
        "MOBIUS_WORKFLOWS_DIR": str(WORKFLOWS),
        "MOBIUS_CHECKPOINTS_DIR": str(tmp / "ckpt"),
        "MOBIUS_LORAS_DIR": str(tmp / "lora"),
        "MOBIUS_TASK_DB": str(tmp / f"gw-tasks-{tag}.db"),
        "MOBIUS_TASK_OWNER": "core",
        # 租约 TTL 必须明显大于 outbox 投递间隔(5s)：否则本地取消会先停续约，
        # 而 running/cancelled 事件还在 outbox 里排队，Core 会先把任务收敛成
        # failed(lease_expired)，随后 cancelled 事件撞 409 卡死 outbox。生产 TTL=60s
        # 远大于投递延迟，本用例取 15s（renew 3s）既保证取消安全又让崩溃收敛可观测。
        "MOBIUS_LEASE_RENEW_SEC": "3",
        "MOBIUS_LEASE_TTL_SEC": "15",
        "MOBIUS_POLL_INTERVAL": "0.3",
    })
    env.pop("CORE_DB_DSN", None)
    return subprocess.Popen([QQBOT, str(GW_DIR / "server.py"), "--port", str(port),
                             "--host", "127.0.0.1"], cwd=str(GW_DIR), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def wait_http(url: str, proc: subprocess.Popen, timeout: float = 45) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            if httpx.get(url, timeout=2.0).status_code == 200:
                return True
        except Exception:
            time.sleep(0.5)
    return False


def kill_tree(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True, timeout=20)
    except Exception:
        with __import__("contextlib").suppress(Exception):
            proc.kill()


def core_task(core_base: str, tid: str) -> dict | None:
    try:
        r = httpx.get(f"{core_base}/v1/tasks/{tid}", timeout=5.0)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def poll_core(core_base: str, tid: str, pred, timeout: float = 25) -> dict | None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = core_task(core_base, tid)
        if last is not None and pred(last):
            return last
        time.sleep(0.5)
    return last


def submit(gw_base: str, body: dict, timeout: float = 30) -> httpx.Response:
    return httpx.post(f"{gw_base}/api/generate", json=body, timeout=timeout)


# --------------------------------------------------------------------------- 主流程
def main() -> int:
    print("==> B4 强化：真三进程执行 E2E（真 Core + 真 Gateway 服务 + mock ComfyUI）")
    if not WORKFLOWS.is_dir() or not (WORKFLOWS / "comic-portrait.json").is_file():
        check("portrait 工作流模板存在", False, str(WORKFLOWS))
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="m1-e2e-exec-"))
    mock_port, core_port = free_port(), free_port()
    gw_port, gw2_port = free_port(), free_port()
    mock_base = f"http://127.0.0.1:{mock_port}"
    core_base = f"http://127.0.0.1:{core_port}"
    gw_base = f"http://127.0.0.1:{gw_port}"
    gw2_base = f"http://127.0.0.1:{gw2_port}"

    httpd = ThreadingHTTPServer(("127.0.0.1", mock_port), _ComfyHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    core_proc = gw_proc = gw2_proc = None
    try:
        core_proc = start_core(core_port, tmp)
        if not wait_http(core_base + "/health", core_proc):
            check("真 Core 子进程启动（lifespan=off + 收敛循环）", False, "Core 未就绪")
            return 1
        check("真 Core 子进程启动（lifespan=off + 收敛循环）", True, core_base)

        gw_proc = start_gateway(gw_port, core_base, mock_base, tmp, "gw1")
        if not wait_http(gw_base + "/health", gw_proc):
            check("真 Gateway 服务启动（core 模式 + mock ComfyUI）", False, "网关未就绪")
            return 1
        check("真 Gateway 服务启动（core 模式 + mock ComfyUI）", True, gw_base)

        # 1) 提交 → Core 分配唯一 id → worker 执行 mock ComfyUI → completed 回写 Core
        body_a = {"prompt": "e2e-happy cat", "workflow": "portrait", "async": True,
                  "source": "qq", "user_id": "u", "session_id": "s",
                  "idempotency_key": "e2e-exec-A"}
        ra = submit(gw_base, body_a)
        ja = ra.json() if ra.status_code == 200 else {}
        tid_a = str(ja.get("task_id") or "")
        check("网关 /api/generate 提交 → Core 分配 task_id", ra.status_code == 200 and bool(tid_a),
              f"http={ra.status_code} task_id={tid_a} body={str(ja)[:160]}")
        if not tid_a:
            return 1
        got_a = poll_core(core_base, tid_a, lambda t: t.get("status") == "completed", timeout=30)
        check("worker 执行 mock ComfyUI → completed 经 outbox 回写真 Core",
              got_a is not None and got_a.get("status") == "completed"
              and (got_a.get("result") or {}).get("filename") == "e2e_exec.png",
              f"status={got_a and got_a.get('status')} result={got_a and got_a.get('result')}")
        check("Core 记录 execution_id（Comfy prompt_id）",
              bool(got_a and got_a.get("execution_id")), f"exec_id={got_a and got_a.get('execution_id')}")

        # 2) 幂等命中：同 key 同 payload 重提 → 同 task_id，不重复执行
        ra2 = submit(gw_base, body_a)
        ja2 = ra2.json() if ra2.status_code == 200 else {}
        check("同 key 同 payload 重提 → 幂等命中同 task_id",
              ra2.status_code == 200 and ja2.get("task_id") == tid_a and ja2.get("idempotent_hit") is True,
              f"task_id={ja2.get('task_id')} hit={ja2.get('idempotent_hit')}")

        # 3) 网关 /api/tasks/{id} 代理 == Core 直查
        rp = httpx.get(f"{gw_base}/api/tasks/{tid_a}", timeout=10)
        jp = rp.json() if rp.status_code == 200 else {}
        pt = jp.get("task") or {}
        check("网关 /api/tasks/{id} 代理真 Core → 与直查一致（owner=core）",
              rp.status_code == 200 and jp.get("owner") == "core"
              and pt.get("task_id") == tid_a and pt.get("status") == "completed"
              and (pt.get("result") or {}).get("filename") == "e2e_exec.png",
              f"owner={jp.get('owner')} status={pt.get('status')}")

        # 4) 取消 running 任务 → 网关中断 mock ComfyUI → cancelled 回写 Core
        body_b = {"prompt": "e2e SLOW cancel", "workflow": "portrait", "async": True,
                  "source": "qq", "idempotency_key": "e2e-exec-B"}
        rb = submit(gw_base, body_b)
        tid_b = str((rb.json() if rb.status_code == 200 else {}).get("task_id") or "")
        run_b = poll_core(core_base, tid_b, lambda t: t.get("status") == "running", timeout=20)
        check("慢任务进入 running（Core 权威）", run_b is not None and run_b.get("status") == "running",
              f"tid={tid_b} status={run_b and run_b.get('status')}")
        rc = httpx.post(f"{gw_base}/api/tasks/{tid_b}/cancel", timeout=20)
        canc_b = poll_core(core_base, tid_b, lambda t: t.get("status") == "cancelled", timeout=25)
        check("取消 running → 网关中断 ComfyUI → cancelled 回写 Core（单一终态）",
              rc.status_code == 200 and canc_b is not None and canc_b.get("status") == "cancelled",
              f"http={rc.status_code} status={canc_b and canc_b.get('status')}")
        with STATE.lock:
            interrupts = STATE.interrupts
        check("网关确实向 ComfyUI 发了 /interrupt", interrupts >= 1, f"interrupts={interrupts}")

        # 5) 崩溃恢复：真杀网关 → Core 租约过期收敛 failed(lease_expired)
        body_c = {"prompt": "e2e SLOW crash", "workflow": "portrait", "async": True,
                  "source": "qq", "idempotency_key": "e2e-exec-C"}
        rcr = submit(gw_base, body_c)
        tid_c = str((rcr.json() if rcr.status_code == 200 else {}).get("task_id") or "")
        run_c = poll_core(core_base, tid_c,
                          lambda t: t.get("status") == "running" and t.get("lease_expires_at"),
                          timeout=20)
        check("崩溃前：任务 running 且 Core 持有执行租约",
              run_c is not None and run_c.get("status") == "running" and bool(run_c.get("lease_expires_at")),
              f"tid={tid_c} status={run_c and run_c.get('status')} lease={run_c and run_c.get('lease_expires_at')}")
        kill_tree(gw_proc)
        gw_proc = None
        print("  · 已真杀网关进程（模拟崩溃），等待 Core 租约过期收敛…", flush=True)
        fail_c = poll_core(core_base, tid_c,
                           lambda t: t.get("status") == "failed" and t.get("error_code") == "lease_expired",
                           timeout=35)
        check("网关崩溃 → Core 租约过期收敛 failed(lease_expired)（禁止永久 running）",
              fail_c is not None and fail_c.get("status") == "failed"
              and fail_c.get("error_code") == "lease_expired",
              f"status={fail_c and fail_c.get('status')} code={fail_c and fail_c.get('error_code')}")

        # 6) fail-closed：Core 宕机 → 新网关 submit 拒绝 503，绝不落本地业务状态
        kill_tree(core_proc)
        core_proc = None
        gw2_proc = start_gateway(gw2_port, core_base, mock_base, tmp, "gw2")
        if not wait_http(gw2_base + "/health", gw2_proc):
            check("Core 宕机后网关仍能启动（轻进程，不占显存）", False, "网关#2 未就绪")
        else:
            check("Core 宕机后网关仍能启动（轻进程，不占显存）", True, gw2_base)
            rf = submit(gw2_base, {"prompt": "e2e fail-closed", "workflow": "portrait",
                                   "async": True, "source": "qq", "idempotency_key": "e2e-exec-F"},
                        timeout=30)
            jf = rf.json() if rf.status_code in (200, 503) else {}
            check("Core 宕机 → 网关 submit fail-closed 503(core_unavailable)",
                  rf.status_code == 503 and jf.get("error") == "core_unavailable",
                  f"http={rf.status_code} error={jf.get('error')}")
    finally:
        kill_tree(gw2_proc)
        kill_tree(gw_proc)
        kill_tree(core_proc)
        with __import__("contextlib").suppress(Exception):
            httpd.shutdown()
            httpd.server_close()
        with __import__("contextlib").suppress(Exception):
            shutil.rmtree(tmp, ignore_errors=True)
        print("  · 子进程与 mock ComfyUI 已清理", flush=True)

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    for n, d in FAILED:
        print(f"  FAIL: {n} {d}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())