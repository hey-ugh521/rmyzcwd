"""M1b 契约测试：Gateway 提交/查询切换到 Core（ADR-007 阶段2）。

跨 venv 限制：gateway venv 无 fastapi、Core venv 无 mcp，无法同进程真集成；
Core 侧 API 契约由 tests/test_m1_task_owner.py（Core venv）覆盖，本测试用 mock Core HTTP
验证 gateway 侧：
  1. _core_task_to_public：Core 任务 → 网关历史 public() 结构（task_id/status/result/error 兼容）
  2. CoreTaskClient：create_task(200/409/5xx/异常)、send_event(ok/duplicate/失败)、
     get_task(200/404/5xx)、list_tasks、request_cancel —— 用假 httpx.AsyncClient 驱动
  3. 查询代理：route_task/route_tasks 在 core 模式代理 Core 并映射；Core 不可达迁移期回退本地
  4. submit_generate：core 模式用 Core 分配的 task_id；CoreUnavailable → fail closed(503) 且
     不落本地；CoreTaskConflict → 409
  5. 回滚开关 MOBIUS_TASK_OWNER=gateway：不调用 Core，走本地权威（旧行为）

隔离：临时 config + 不存在的 secrets；不连生产 PG/Core/ComfyUI。

运行：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe tests\\test_m1_gateway_proxy.py
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

_TMP = Path(tempfile.mkdtemp(prefix="m1b-proxy-"))
_cfg = _TMP / "config.json"
_cfg.write_text(json.dumps({
    "services": {"gateway": {"host": "127.0.0.1", "port": 18899},
                 "core": {"host": "127.0.0.1", "port": 8900, "api_key": "<unset>"}},
}), encoding="utf-8")
os.environ["MOBIUS_CONFIG"] = str(_cfg)
os.environ["MOBIUS_SECRETS_FILE"] = str(_TMP / "absent-secrets.json")
os.environ["MOBIUS_TASK_DB"] = str(_TMP / "tasks.db")
os.environ.pop("MOBIUS_GATEWAY_TOKEN", None)
os.environ.pop("MOBIUS_GATEWAY_HOST", None)

sys.path.insert(0, str(GW_DIR))
sys.path.insert(0, str(ROOT))
import server as gw  # noqa: E402
from starlette.requests import Request  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 假 httpx
class _FakeResp:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._p = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._p is None:
            raise ValueError("no json")
        return self._p


_ROUTER: dict = {"handler": None}


class _FakeAsyncClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None, **k):
        return _ROUTER["handler"]("POST", url, json, None)

    async def get(self, url, params=None, headers=None, **k):
        return _ROUTER["handler"]("GET", url, None, params)


def _install_fake_httpx():
    gw.httpx.AsyncClient = _FakeAsyncClient  # type: ignore[assignment]


# ---------------------------------------------------------------- 1. 映射
def test_public_mapping() -> None:
    print("\n[1] _core_task_to_public：Core → 网关 public() 兼容")
    ct = {"id": "t_1", "kind": "image", "status": "completed", "source": "qq",
          "user_id": "u", "session_id": "s", "character": "bronya", "prompt": "cat",
          "params": {"workflow": "portrait"}, "result": {"filename": "x.png", "path": "/img?f=x.png"},
          "error": None, "execution_id": "prompt_9", "error_code": None,
          "created_at": 1.0, "started_at": 2.0, "finished_at": 3.0,
          "cancel_requested": 0, "attempts": 1, "version": 3}
    pub = gw._core_task_to_public(ct)
    check("task_id/id 兼容", pub["task_id"] == "t_1" and pub["id"] == "t_1")
    check("status 透传（规范词）", pub["status"] == "completed")
    check("result 透传", pub["result"] == {"filename": "x.png", "path": "/img?f=x.png"})
    check("remote_id ← execution_id（Comfy prompt_id）", pub["remote_id"] == "prompt_9")
    check("error/error_code 字段在", "error" in pub and pub.get("error_code") is None)

    unk = gw._core_task_to_public({"id": "t_2", "status": "submission_unknown"})
    check("submission_unknown 带人工确认 hint", "hint" in unk and unk["status"] == "submission_unknown")


# ---------------------------------------------------------------- 2. CoreTaskClient
def test_core_client() -> None:
    print("\n[2] CoreTaskClient（假 httpx 驱动）")
    _install_fake_httpx()
    client = gw.CoreTaskClient(gw.settings)

    def h_create_ok(method, url, body, params):
        assert url.endswith("/v1/tasks") and method == "POST"
        return _FakeResp(200, {"ok": True, "task_id": "t_core_1", "status": "pending",
                               "idempotent_hit": False})
    _ROUTER["handler"] = h_create_ok
    res = run(client.create_task(kind="image", payload={"p": 1}, idempotency_key="k",
                                 source="qq"))
    check("create_task 200 → task_id", res["task_id"] == "t_core_1" and res["idempotent_hit"] is False)

    def h_create_hit(method, url, body, params):
        return _FakeResp(200, {"ok": True, "task_id": "t_core_1", "status": "completed",
                               "idempotent_hit": True})
    _ROUTER["handler"] = h_create_hit
    res2 = run(client.create_task(kind="image", payload={"p": 1}, idempotency_key="k", source="qq"))
    check("create_task 幂等命中", res2["idempotent_hit"] is True)

    def h_conflict(method, url, body, params):
        return _FakeResp(409, {"ok": False, "error": "idempotency_conflict", "task_id": "t_other"})
    _ROUTER["handler"] = h_conflict
    try:
        run(client.create_task(kind="image", payload={"p": 2}, idempotency_key="k", source="qq"))
        check("create_task 409 → CoreTaskConflict", False, "未抛")
    except gw.CoreTaskConflict as exc:
        check("create_task 409 → CoreTaskConflict", exc.task_id == "t_other")

    def h_500(method, url, body, params):
        return _FakeResp(503, {"ok": False, "error": "x"})
    _ROUTER["handler"] = h_500
    try:
        run(client.create_task(kind="image", payload={}, idempotency_key="k", source="qq"))
        check("create_task 5xx → CoreUnavailable", False, "未抛")
    except gw.CoreUnavailable:
        check("create_task 5xx → CoreUnavailable（fail closed 依据）", True)

    def h_raise(method, url, body, params):
        raise OSError("connection refused")
    _ROUTER["handler"] = h_raise
    try:
        run(client.create_task(kind="image", payload={}, idempotency_key="k", source="qq"))
        check("create_task 连接异常 → CoreUnavailable", False, "未抛")
    except gw.CoreUnavailable:
        check("create_task 连接异常 → CoreUnavailable", True)

    # send_event
    def h_event_ok(method, url, body, params):
        assert url.endswith("/events") and body["event"] == "completed"
        return _FakeResp(200, {"ok": True, "status": "completed"})
    _ROUTER["handler"] = h_event_ok
    check("send_event ok → True",
          run(client.send_event("t1", event="completed", seq=5, execution_id="p1",
                                result={"f": "x"})) is True)

    def h_event_dup(method, url, body, params):
        return _FakeResp(200, {"duplicate": True, "seq": 5})
    _ROUTER["handler"] = h_event_dup
    check("send_event duplicate → True（不无限重试）",
          run(client.send_event("t1", event="completed", seq=5)) is True)

    def h_event_fail(method, url, body, params):
        return _FakeResp(500, {"ok": False})
    _ROUTER["handler"] = h_event_fail
    check("send_event 5xx → False（outbox 保留重试）",
          run(client.send_event("t1", event="completed", seq=6)) is False)

    def h_event_gone(method, url, body, params):
        return _FakeResp(404, {"ok": False, "error": "task not found"})
    _ROUTER["handler"] = h_event_gone
    check("send_event 404 → True（死信丢弃，不堵队头）",
          run(client.send_event("t1", event="completed", seq=7)) is True)

    def h_event_conflict(method, url, body, params):
        return _FakeResp(409, {"ok": False, "error": "invalid_transition"})
    _ROUTER["handler"] = h_event_conflict
    check("send_event 409 → True（死信丢弃）",
          run(client.send_event("t1", event="completed", seq=8)) is True)

    def h_event_429(method, url, body, params):
        return _FakeResp(429, {"ok": False, "error": "rate_limited"})
    _ROUTER["handler"] = h_event_429
    check("send_event 429 → False（保留重试）",
          run(client.send_event("t1", event="completed", seq=9)) is False)

    # get_task
    def h_get_ok(method, url, body, params):
        return _FakeResp(200, {"id": "t1", "status": "running"})
    _ROUTER["handler"] = h_get_ok
    check("get_task 200 → dict", run(client.get_task("t1"))["status"] == "running")

    def h_get_404(method, url, body, params):
        return _FakeResp(404, {"error": "task not found"})
    _ROUTER["handler"] = h_get_404
    check("get_task 404 → None", run(client.get_task("nope")) is None)

    # list_tasks + request_cancel
    def h_list(method, url, body, params):
        return _FakeResp(200, {"tasks": [{"id": "t1", "status": "completed"}], "count": 1})
    _ROUTER["handler"] = h_list
    check("list_tasks → 列表", len(run(client.list_tasks(limit=5))) == 1)

    def h_cancel(method, url, body, params):
        assert url.endswith("/cancel")
        return _FakeResp(200, {"ok": True, "cancel_requested": True})
    _ROUTER["handler"] = h_cancel
    check("request_cancel → ok", run(client.request_cancel("t1")).get("cancel_requested") is True)


# ---------------------------------------------------------------- 假 Core 客户端（路由/submit 用）
class _FakeCore:
    def __init__(self):
        self.tasks = {}
        self.created = []
        self.cancelled = []
        self.events = []
        self.unavailable = False

    async def create_task(self, **kw):
        if self.unavailable:
            raise gw.CoreUnavailable("core down")
        key = kw.get("idempotency_key")
        for t in self.tasks.values():
            if t.get("_key") == key:
                return {"task_id": t["id"], "status": t["status"], "idempotent_hit": True}
        tid = f"t_core_{len(self.tasks)+1}"
        self.tasks[tid] = {"id": tid, "status": "pending", "_key": key, "kind": kw["kind"],
                           "result": None, "execution_id": None}
        self.created.append(kw)
        return {"task_id": tid, "status": "pending", "idempotent_hit": False}

    async def get_task(self, task_id):
        if self.unavailable:
            raise gw.CoreUnavailable("core down")
        return self.tasks.get(task_id)

    async def list_tasks(self, limit=50, status=None, source=None):
        if self.unavailable:
            raise gw.CoreUnavailable("core down")
        return list(self.tasks.values())

    async def request_cancel(self, task_id):
        if self.unavailable:
            raise gw.CoreUnavailable("core down")
        self.cancelled.append(task_id)
        return {"ok": True, "cancel_requested": True}

    async def send_event(self, task_id, **kw):
        self.events.append((task_id, kw))
        return True


def _fake_request(path="/api/tasks/t1", path_params=None, query=""):
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": query.encode(),
             "root_path": "", "server": ("testserver", 80), "client": ("127.0.0.1", 5000),
             "headers": [(b"host", b"testserver")], "path_params": path_params or {}}
    return Request(scope)


# ---------------------------------------------------------------- 3. 查询代理
def test_query_proxy() -> None:
    print("\n[3] 查询路由代理 Core（core 模式）")
    os.environ["MOBIUS_TASK_OWNER"] = "core"
    fake = _FakeCore()
    fake.tasks["t_core_1"] = {"id": "t_core_1", "status": "completed", "kind": "image",
                              "result": {"filename": "x.png"}, "execution_id": "p9"}
    old = gw._CORE_TASKS
    gw._CORE_TASKS = fake
    try:
        r = run(gw.route_task(_fake_request("/api/tasks/t_core_1", {"task_id": "t_core_1"})))
        body = json.loads(r.body)
        check("route_task 代理 Core → public 结构",
              body["ok"] and body["task"]["task_id"] == "t_core_1"
              and body["task"]["status"] == "completed" and body["owner"] == "core",
              str(body)[:120])
        rl = run(gw.route_tasks(_fake_request("/api/tasks", query="limit=5")))
        bl = json.loads(rl.body)
        check("route_tasks 代理 Core → 列表", bl["ok"] and bl["count"] >= 1 and bl["owner"] == "core")

        # Core 不可达 → 迁移期回退本地账本（不报错）
        fake.unavailable = True
        rf = run(gw.route_task(_fake_request("/api/tasks/t_core_1", {"task_id": "t_core_1"})))
        # 本地无此任务 → 404（回退到 store.get），证明未硬失败
        check("Core 不可达时读路径回退本地（不 5xx 崩溃）", rf.status_code in (200, 404),
              str(rf.status_code))
        fake.unavailable = False

        # 未知任务 → 404
        r404 = run(gw.route_task(_fake_request("/api/tasks/nope", {"task_id": "nope"})))
        check("代理 Core 未知任务 → 404", r404.status_code == 404)
    finally:
        gw._CORE_TASKS = old


# ---------------------------------------------------------------- 4. submit fail-closed
def test_submit_fail_closed() -> None:
    print("\n[4] submit_generate：Core 分配 task_id + fail closed")
    os.environ["MOBIUS_TASK_OWNER"] = "core"
    fake = _FakeCore()
    old_core = gw._CORE_TASKS
    gw._CORE_TASKS = fake
    # 打桩：绕开 ComfyUI/spec，仅验证 Core 交互与 task_id 传递
    orig = {k: getattr(gw, k) for k in ("require_comfy", "resolve_spec", "derive_idempotency_key")}
    submitted = {}

    async def _noop_require_comfy(cfg):
        return None

    def _fake_resolve_spec(cfg, body, kind="image"):
        return {"workflow": "w", "checkpoint": "c", "loras": [], "warnings": [],
                "character": "bronya", "timeout_sec": 10}

    def _fake_derive(body):
        return (str(body.get("idempotency_key") or "k"), False)

    class _FakeStore:
        def submit(self, **kw):
            submitted.update(kw)
            t = types.SimpleNamespace(id=kw.get("task_id") or "g_local", status="pending",
                                      idempotency_key=kw.get("idempotency_key"))
            return t, False

    orig_store = gw.store
    gw.require_comfy = _noop_require_comfy
    gw.resolve_spec = _fake_resolve_spec
    gw.derive_idempotency_key = _fake_derive
    gw.store = _FakeStore()
    try:
        out = run(gw.submit_generate(gw.settings, {"prompt": "cat", "idempotency_key": "k1",
                                                   "source": "qq"}, True))
        check("core 模式：Core 分配 task_id 并透传给本地账本",
              submitted.get("task_id") == out["task_id"] and out["task_id"].startswith("t_core_"),
              f"task_id={out['task_id']} submitted={submitted.get('task_id')}")
        check("create_task 收到 idempotency_key/payload/source",
              fake.created and fake.created[0]["idempotency_key"] == "k1"
              and fake.created[0]["source"] == "qq")

        # P1：Core 已建任务但本地账本失败 → 补偿 Core failed
        class _FailingStore:
            def submit(self, **kw):
                raise RuntimeError("sqlite write down")

        fake.events.clear()
        prev_store = gw.store
        gw.store = _FailingStore()
        try:
            try:
                run(gw.submit_generate(gw.settings, {"prompt": "comp", "idempotency_key": "k-comp"},
                                       True))
                check("本地落账失败 → 抛错", False, "未抛")
            except gw.ApiError as exc:
                check("本地落账失败 → gateway_local_submit_failed 503",
                      exc.error == "gateway_local_submit_failed" and exc.status == 503,
                      f"{exc.error}/{exc.status}")
            failed = [e for e in fake.events if (e[1] or {}).get("event") == "failed"]
            check("本地落账失败 → Core 收到 failed 补偿", len(failed) == 1, str(fake.events)[:160])
            if failed:
                check("补偿 error_code=gateway_local_submit_failed",
                      failed[0][1].get("error_code") == "gateway_local_submit_failed",
                      str(failed[0][1]))
        finally:
            gw.store = prev_store

        # Core 不可用 → fail closed(503)，且不落本地
        fake.unavailable = True
        submitted.clear()
        try:
            run(gw.submit_generate(gw.settings, {"prompt": "dog", "idempotency_key": "k2"}, True))
            check("CoreUnavailable → fail closed", False, "未抛")
        except gw.ApiError as exc:
            check("CoreUnavailable → core_unavailable 503 且不落本地",
                  exc.error == "core_unavailable" and exc.status == 503 and not submitted,
                  f"error={exc.error} status={exc.status} submitted={bool(submitted)}")
        fake.unavailable = False
    finally:
        for k, v in orig.items():
            setattr(gw, k, v)
        gw.store = orig_store
        gw._CORE_TASKS = old_core


# ---------------------------------------------------------------- 5. 回滚开关
def test_rollback_switch() -> None:
    print("\n[5] 回滚开关 MOBIUS_TASK_OWNER=gateway → 本地权威（不调用 Core）")
    os.environ["MOBIUS_TASK_OWNER"] = "gateway"
    check("task_owner_mode 读 env", gw.task_owner_mode() == "gateway")
    fake = _FakeCore()
    old_core = gw._CORE_TASKS
    gw._CORE_TASKS = fake
    orig = {k: getattr(gw, k) for k in ("require_comfy", "resolve_spec", "derive_idempotency_key")}
    submitted = {}

    async def _noop(cfg):
        return None

    class _FakeStore:
        def submit(self, **kw):
            submitted.update(kw)
            return types.SimpleNamespace(id="g_local", status="pending",
                                         idempotency_key=kw.get("idempotency_key")), False

    orig_store = gw.store
    gw.require_comfy = _noop
    gw.resolve_spec = lambda cfg, body, kind="image": {"workflow": "w", "checkpoint": "c",
                                                       "loras": [], "warnings": [], "character": None}
    gw.derive_idempotency_key = lambda body: ("k", False)
    gw.store = _FakeStore()
    try:
        out = run(gw.submit_generate(gw.settings, {"prompt": "x"}, True))
        check("gateway 模式：不调用 Core create_task", not fake.created)
        check("gateway 模式：本地分配 task_id（task_id=None 传入 store）",
              submitted.get("task_id") is None and out["task_id"] == "g_local",
              f"task_id={out['task_id']}")
    finally:
        for k, v in orig.items():
            setattr(gw, k, v)
        gw.store = orig_store
        gw._CORE_TASKS = old_core
        os.environ.pop("MOBIUS_TASK_OWNER", None)


def main() -> int:
    print("==> M1b Gateway→Core 代理契约测试")
    test_public_mapping()
    test_core_client()
    test_query_proxy()
    test_submit_fail_closed()
    test_rollback_switch()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
