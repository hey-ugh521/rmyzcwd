"""Möbius Core —— HTTP 服务。

一张进程三张脸：
- /v1/*       模型侧（OpenAI 兼容 + 原生 SSE），给 LangBot / Open WebUI Pipe
- /api/*      控制台侧，给 Möbius Console 与运维脚本
- /console    内嵌单页控制台
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import store
from .agent import Agent
from .qq_adapter import QQAdapter
from .registry import RegistryError, init_schema as init_registry_schema
from .registry_api import registry_error_handler, router as registry_router
from .registry_index_api import router as registry_index_router
from .services import GatewayClient, MediaDisabled, ServiceError, Supervisor

CFG = store.CFG
CORE = CFG.get("core", {})
API_KEY = CORE.get("api_key", "")
START_TS = time.time()

sup = Supervisor()
gw = GatewayClient()
agent = Agent(sup, gw)

CONSOLE_DIR = Path(__file__).resolve().parent / "console"
STUDIO_DIR = Path(__file__).resolve().parent.parent / "web" / "studio" / "dist"

app = FastAPI(title="Möbius Core", version="1.0.0")
qq_adapter = QQAdapter(agent, CFG.get("qq") or {})
app.add_exception_handler(RegistryError, registry_error_handler)
app.include_router(registry_index_router)
app.include_router(registry_router)

# 后台任务引用集合（防止 asyncio 任务被 GC；不等待完成）
_BACKGROUND: set = set()


# ---------------------------------------------------------------- 鉴权

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOCAL_TOKEN_COOKIE = "mobius_local_token"


def _bearer_ok(request: Request) -> bool:
    if not API_KEY:
        return False
    auth = request.headers.get("authorization", "")
    scheme, _, credential = auth.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(
        credential.encode("utf-8"), API_KEY.encode("utf-8")
    )


def _local_cookie_ok(request: Request) -> bool:
    if not API_KEY:
        return False
    token = request.cookies.get(LOCAL_TOKEN_COOKIE, "")
    return bool(token) and hmac.compare_digest(
        token.encode("utf-8"), API_KEY.encode("utf-8")
    )


@app.middleware("http")
async def auth_mw(request: Request, call_next):
    """M3a 本机边界：
    - 回环：只读保持免鉴权；变更类 /api、/v1 需 Bearer API_KEY 或 Console 同源 HttpOnly cookie。
    - 非回环：所有 /api、/v1 需 Bearer API_KEY。
    - /mcp 属网关，不在 Core 此中间件范围。
    """
    path = request.url.path
    host = request.client.host if request.client else ""
    if path.rstrip("/") in ("/console", "/studio") and host not in LOOPBACK:
        # 不允许网络客户端打开本机控制面（否则同源 cookie 会泄露 API key）
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    if path.startswith("/v1") or path.startswith("/api"):
        if host in LOOPBACK:
            if API_KEY and request.method.upper() in MUTATING_METHODS:
                if not (_bearer_ok(request) or _local_cookie_ok(request)):
                    return JSONResponse({"error": "unauthorized",
                                         "hint": "本机变更接口需要 Authorization: Bearer <api_key> 或 /studio 同源会话"},
                                        status_code=401)
            if API_KEY and request.method.upper() in MUTATING_METHODS:
                actor = "api-key" if _bearer_ok(request) else "local-session"
            else:
                actor = "local-read"
        else:
            if not _bearer_ok(request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            actor = "remote-api"
        request.state.actor = actor
    response = await call_next(request)
    # Console/Studio 首屏下发 HttpOnly + SameSite=Strict cookie，供同源 fetch 自动携带；
    # 跨站页面无法读取该 cookie，普通跨站表单也不会在 SameSite=Strict 下携带。
    if API_KEY and host in LOOPBACK and path.rstrip("/") in ("/console", "/studio"):
        response.set_cookie(
            LOCAL_TOKEN_COOKIE, API_KEY, httponly=True, samesite="strict", path="/"
        )
    return response


# ---------------------------------------------------------------- 生命周期

async def _task_convergence_loop() -> None:
    """M1c：Core 作为任务唯一 owner 的收敛循环（ADR-007 §9），禁止永久 running/unknown。

    - 租约过期的 running → failed(lease_expired)
    - submission_unknown 超 24h 未人工确认 → failed(submission_unknown_timeout)
    间隔 core.task_convergence_interval_sec（默认 30s）；MOBIUS_TASK_OWNER=gateway 回滚时跳过。
    """
    interval = float(CORE.get("task_convergence_interval_sec", 30))
    while True:
        try:
            if os.environ.get("MOBIUS_TASK_OWNER", "core").strip().lower() != "gateway":
                expired = store.task_expire_stale_leases()
                if expired:
                    store.audit("core", "task.lease_expired",
                                {"count": len(expired), "task_ids": expired[:20]})
                stale = store.task_expire_stale_unknown()
                if stale:
                    store.audit("core", "task.submission_unknown_timeout",
                                {"count": len(stale), "task_ids": stale[:20]})
        except Exception as exc:  # noqa: BLE001
            with contextlib.suppress(Exception):
                store.audit("core", "task.converge.error", {"error": str(exc)}, result="fail")
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    store.audit("core", "core.start", {"port": CORE.get("port")})
    init_registry_schema()
    task = asyncio.create_task(sup.idle_loop())
    converge = asyncio.create_task(_task_convergence_loop())
    # 网关是轻进程（不占显存）：无论什么媒体模式都随 Core 起来，
    # 否则 LangBot 开机时连不上 MCP，要等它自己重连（实测会一直连不上）。
    asyncio.create_task(_boot())
    try:
        yield
    finally:
        task.cancel()
        converge.cancel()
        with contextlib.suppress(Exception):
            await task
        with contextlib.suppress(Exception):
            await converge
        # L2 P1：Core 退出时优雅停掉自己拉起的全部服务（外部实例会被跳过）
        with contextlib.suppress(Exception):
            await asyncio.wait_for(sup.stop_all(), timeout=30)
        store.audit("core", "core.stop", {})


async def _boot():
    try:
        await sup.ensure_gateway()
    except Exception as e:
        store.audit("supervisor", "gateway.boot.fail", {"error": str(e)}, result="fail")
    # L2 P1：Core 启动即拉起 autostart 服务（可由 core.autostart_services 关闭）
    if bool(CORE.get("autostart_services", True)):
        task = asyncio.create_task(sup.start_all())
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
    if CORE.get("prewarm_on_start"):
        await _prewarm()


async def _prewarm():
    """预热 ComfyUI。注意：不在这里改用户的媒体模式，预热只是"现在就拉起来"。"""
    try:
        await sup.ensure_gateway()
        await sup.start("comfyui")
        if "gsv" in getattr(sup, "services", {}):
            await sup.start("gsv")
    except Exception as e:
        store.audit("supervisor", "prewarm.fail", {"error": str(e)}, result="fail")


app.router.lifespan_context = lifespan


# ---------------------------------------------------------------- 工具

def _identity(request: Request, body: dict | None = None) -> tuple[str, str]:
    body = body or {}
    user_id = (request.headers.get("x-mobius-user")
               or body.get("user")
               or "qq:default")
    session_id = (request.headers.get("x-mobius-session")
                  or body.get("session_id")
                  or f"session:{user_id}")
    return str(user_id), str(session_id)


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _sync_tasks() -> None:
    """gateway 回滚模式：把网关里已结束、本地仍 running 的任务拉回来。

    core 模式下 Core 是唯一权威、网关经 /v1/tasks/{id}/events 主动上报，无需拉取（跳过）。
    """
    if os.environ.get("MOBIUS_TASK_OWNER", "core").strip().lower() != "gateway":
        return
    for t in store.tasks_active():
        rid = t.get("remote_id")
        if not rid:
            continue
        try:
            r = await gw.task(rid)
        except Exception:
            continue
        obj = r.get("task") or r
        st = store.normalize_task_status(obj.get("status"))
        if st in store.TASK_TERMINAL:
            result = obj.get("result") or {}
            store.task_update(t["id"], status=st, result=result,
                              error=obj.get("error"), finished_at=store.now())


# ---------------------------------------------------------------- 基础

@app.get("/health")
async def health():
    return {"ok": True, "service": "mobius-core", "uptime_sec": round(time.time() - START_TS, 1)}


@app.get("/v1/models")
async def models():
    return {"object": "list",
            "data": [{"id": "mobius-core", "object": "model", "owned_by": "mobius"}]}


@app.get("/")
async def root():
    return RedirectResponse("/console")


# ---------------------------------------------------------------- OpenAI 兼容

def _openai_chunk(cid: str, created: int, delta: dict, finish: str | None = None) -> str:
    return "data: " + json.dumps({
        "id": cid, "object": "chat.completion.chunk", "created": created,
        "model": "mobius-core",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }, ensure_ascii=False) + "\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, body: dict = Body(...)):
    messages = body.get("messages") or []
    stream = bool(body.get("stream"))
    user_id, session_id = _identity(request, body)
    created = int(time.time())
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

    async def gen_events():
        async for ev, payload in agent.run(source="qq", user_id=user_id,
                                           session_id=session_id, messages=messages):
            if ev == "pause":
                await asyncio.sleep(0.012)
                continue
            yield ev, payload

    if not stream:
        text_parts: list[str] = []
        async for ev, payload in gen_events():
            if ev == "text":
                text_parts.append(payload["delta"])
            elif ev == "error":
                text_parts.append(f"（系统错误：{payload.get('message')}）")
        content = "".join(text_parts)
        return JSONResponse({
            "id": cid, "object": "chat.completion", "created": created,
            "model": "mobius-core",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    async def stream_gen():
        yield _openai_chunk(cid, created, {"role": "assistant", "content": ""})
        try:
            async for ev, payload in gen_events():
                if ev == "text":
                    yield _openai_chunk(cid, created, {"content": payload["delta"]})
                elif ev == "error":
                    yield _openai_chunk(cid, created,
                                        {"content": f"（系统错误：{payload.get('message')}）"})
        finally:
            yield _openai_chunk(cid, created, {}, "stop")
            yield "data: [DONE]\n\n"

    return StreamingResponse(stream_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- 原生 SSE

@app.post("/v1/chat")
async def native_chat(request: Request, body: dict = Body(...)):
    source = body.get("source") or "web"
    user_id = str(body.get("user_id") or "web:anonymous")
    session_id = str(body.get("session_id") or f"session:{user_id}")
    text = str(body.get("text") or "")
    character = body.get("character")

    async def gen():
        yield _sse("meta", {"reply_id": f"r_{uuid.uuid4().hex[:8]}", "source": source})
        try:
            async for ev, payload in agent.run(source=source, user_id=user_id,
                                               session_id=session_id, text=text,
                                               character=character):
                if ev == "pause":
                    await asyncio.sleep(0.012)
                    continue
                yield _sse(ev, payload)
        except Exception as e:
            yield _sse("error", {"error": "core_error", "message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- 媒体

@app.post("/v1/media/ensure")
async def media_ensure(body: dict = Body(default={})):
    """给 Media Gateway 反查用：确保 ComfyUI 被唤醒。"""
    try:
        st = await sup.ensure((body or {}).get("kind") or "image")
        return {"ok": True, "state": st.get("state"), "port": st.get("port")}
    except MediaDisabled:
        return JSONResponse({"ok": False, "error": "media_off",
                             "hint": "媒体能力已关闭，请在控制台切换模式"}, status_code=409)
    except Exception as e:
        return JSONResponse({"ok": False, "error": "wake_failed", "hint": str(e)}, status_code=503)


@app.get("/api/status")
async def api_status():
    await sup.probe_all()
    await _sync_tasks()
    langbot = False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=2) as c:
            langbot = (await c.get("http://127.0.0.1:5300/healthz")).status_code == 200
    except Exception:
        langbot = False
    return {
        "core": {"ok": True, "uptime_sec": round(time.time() - START_TS, 1),
                 "config": str(store.CONFIG_PATH)},
        "media": await sup.status(),
        "counters": store.tasks_today_counts(),
        "langbot": {"reachable": langbot},
    }


# ---------------------------------------------------------------- 服务管理（L2 P1）


@app.post("/api/shutdown")
async def api_shutdown():
    """优雅退出：先停掉由 Core 拉起的全部服务，再退出进程（启动器用）。"""
    async def _do() -> None:
        with contextlib.suppress(Exception):
            await sup.stop_all()
        store.audit("core", "core.shutdown_requested", {})
        await asyncio.sleep(0.5)
        os._exit(0)

    task = asyncio.create_task(_do())
    _BACKGROUND.add(task)
    return {"ok": True, "shutting_down": True}


@app.get("/api/services")
async def api_services():
    """全栈服务状态（含 autostart 列表）。"""
    await sup.probe_all()
    return {
        "ok": True,
        "autostart": [s.name for s in sup.services.values() if s.enabled and s.autostart],
        "services": sup.list_services(),
    }


@app.get("/api/services/history")
async def api_services_history(limit: int = 30, service: str | None = None):
    """最近的服务状态/登录态变化（Console 状态历史）。"""
    return {"ok": True, "history": store.audit_state_history(limit=limit, service=service)}


@app.get("/api/services/{name}")
async def api_service_get(name: str):
    try:
        return {"ok": True, "service": await sup.probe(name)}
    except ServiceError as e:
        raise HTTPException(404, str(e))


@app.post("/api/services/{name}/start")
async def api_service_start(name: str, wait: int = 0):
    if name not in sup.services:
        raise HTTPException(404, f"未知服务 {name}")
    if wait:
        return {"ok": True, "service": await sup.start(name, wait=True)}
    asyncio.create_task(sup.start(name, wait=True))
    return {"ok": True, "started": True, "background": True, "service": sup.services[name].public()}


@app.post("/api/services/{name}/stop")
async def api_service_stop(name: str, force: bool = False):
    if name not in sup.services:
        raise HTTPException(404, f"未知服务 {name}")
    return {"ok": True, "service": await sup.stop(name, force=force)}


@app.post("/api/services/{name}/restart")
async def api_service_restart(name: str):
    if name not in sup.services:
        raise HTTPException(404, f"未知服务 {name}")
    asyncio.create_task(sup.restart(name, wait=True))
    return {"ok": True, "restarting": True, "background": True}


@app.post("/api/services/start-all")
async def api_services_start_all():
    task = asyncio.create_task(sup.start_all())
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return {"ok": True, "starting": True, "background": True,
            "autostart": [s.name for s in sup.services.values() if s.enabled and s.autostart]}


@app.post("/api/services/stop-all")
async def api_services_stop_all():
    return {"ok": True, "services": await sup.stop_all()}


@app.get("/api/services/{name}/logs")
async def api_service_logs(name: str, lines: int = 100):
    try:
        return {"ok": True, **sup.logs(name, lines)}
    except ServiceError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------- 媒体


@app.put("/api/media/mode")
async def api_set_mode(body: dict = Body(...)):
    try:
        mode = sup.set_mode(str(body.get("mode") or ""))
    except ServiceError as e:
        raise HTTPException(400, str(e))
    if mode == "prewarm":
        asyncio.create_task(_prewarm())
    elif mode == "off":
        asyncio.create_task(sup.stop("comfyui"))
    return {"ok": True, "mode": mode}


@app.get("/api/media/providers")
async def api_media_providers():
    """M2d：Provider 能力清单（代理 Gateway 内部只读接口）。"""
    try:
        return await gw.providers()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Gateway Provider 清单不可用：{exc}") from exc


@app.get("/api/media/resources")
async def api_media_resources():
    """M2d：媒体资源租约/队列摘要（代理 Gateway）。"""
    try:
        return await gw.resources()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"Gateway 资源摘要不可用：{exc}") from exc


@app.get("/api/media/config")
async def api_media_config():
    return sup.config()


@app.put("/api/media/config")
async def api_media_config_put(body: dict = Body(...)):
    return sup.update_config(body.get("idle_sleep_sec"), body.get("prewarm_on_start"))


@app.post("/api/media/wake")
async def api_wake():
    try:
        st = await sup.wake()
        return {"ok": st.get("state") in ("ready", "busy"), "state": st}
    except MediaDisabled:
        raise HTTPException(409, "media_disabled：媒体能力已关闭，请先切换模式")
    except Exception as e:
        raise HTTPException(503, f"唤醒失败: {e}")


@app.post("/api/media/sleep")
async def api_sleep():
    return {"ok": True, "state": await sup.stop("comfyui")}


# ---------------------------------------------------------------- 任务



@app.post("/v1/tasks/events")
async def api_task_event(request: Request):
    """接收 Gateway outbox 事件（幂等，按 seq 去重）。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    try:
        res = store.task_apply_event(body if isinstance(body, dict) else {})
        return {"ok": True, **res}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                            status_code=500)


# ------------------------------------------------- M1a：Core 权威任务 API（ADR-007）
# 受上方 auth_mw 保护（/v1* 非回环需 Bearer API_KEY），与现有 Core 鉴权一致。
# 状态词统一 ADR-002 规范词；历史词经 normalize_task_status 归一（读取兼容）。

@app.post("/v1/tasks")
async def api_create_task(request: Request):
    """创建任务 / 幂等命中。task_id 由 Core 分配；调用方只带 idempotency_key + payload。

    同 key 同 payload → 返回原任务（idempotent_hit=true）；同 key 异 payload → 409。
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid_body"}, status_code=400)
    kind = str(body.get("kind") or "").strip()
    payload = body.get("payload")
    if kind not in ("image", "speech") or not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "invalid_kind_or_payload",
                             "hint": "kind ∈ image|speech，payload 必须是对象"}, status_code=400)
    source = str(body.get("source") or "console")
    try:
        task, hit = store.task_create_authoritative(
            kind=kind, payload=payload,
            idempotency_key=str(body.get("idempotency_key") or ""),
            source=source, user_id=body.get("user_id"), session_id=body.get("session_id"),
            character=body.get("character"), prompt=str(body.get("prompt") or ""),
        )
    except store.TaskConflictError as exc:
        return JSONResponse({"ok": False, "error": "idempotency_conflict",
                             "task_id": exc.task_id}, status_code=409)
    store.audit(source, "task.create", {"kind": kind, "idempotent_hit": hit},
                task_id=task["id"], source=source)
    return {"ok": True, "task_id": task["id"],
            "status": store.normalize_task_status(task["status"]), "idempotent_hit": hit}


@app.post("/v1/tasks/{task_id}/events")
async def api_task_auth_event(task_id: str, request: Request):
    """执行事件写入（seq 幂等 + 状态机校验）。Core 是唯一状态写入者（ADR-007 §2.2）。"""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "invalid_body"}, status_code=400)
    event = str(body.get("event") or "").strip()
    if not event:
        return JSONResponse({"ok": False, "error": "missing_event"}, status_code=400)
    try:
        res = store.task_apply_authoritative_event(
            task_id, event=event, seq=int(body.get("seq") or 0),
            execution_id=body.get("execution_id"), error_code=body.get("error_code"),
            error_message=body.get("error_message") or body.get("error"),
            result=body.get("result"), lease_owner=body.get("lease_owner"),
            lease_expires_at=body.get("lease_expires_at"), attempts=body.get("attempts"),
        )
    except KeyError:
        return JSONResponse({"ok": False, "error": "task_not_found"}, status_code=404)
    except store.IllegalTransitionError as exc:
        return JSONResponse({"ok": False, "error": "illegal_transition",
                             "from": exc.src, "to": exc.dst}, status_code=409)
    return {"ok": True, **res}


@app.get("/v1/tasks")
async def api_list_tasks_v1(limit: int = 50, status: str | None = None,
                            source: str | None = None):
    tasks = store.tasks_list(limit=limit, status=status, source=source)
    for t in tasks:
        t["status"] = store.normalize_task_status(t.get("status"))
    return {"tasks": tasks, "count": len(tasks)}


@app.get("/v1/tasks/{task_id}")
async def api_get_task_v1(task_id: str):
    t = store.task_get(task_id)
    if not t:
        raise HTTPException(404, "task not found")
    t["status"] = store.normalize_task_status(t.get("status"))
    return t


@app.post("/v1/tasks/{task_id}/cancel")
async def api_cancel_task_v1(task_id: str):
    """写 cancel_requested；终态由执行端确认（ADR-007 §2.4，不引入第二终态）。"""
    try:
        res = store.task_request_cancel(task_id)
    except KeyError:
        raise HTTPException(404, "task not found")
    store.audit("console", "task.cancel_requested", {}, task_id=task_id, source="console")
    return res


@app.get("/api/tasks")
async def api_tasks(limit: int = 50, status: str | None = None, source: str | None = None):
    await _sync_tasks()
    tasks = store.tasks_list(limit=limit, status=status, source=source)
    for t in tasks:  # 方案A：对外统一规范词（历史行读时归一）
        t["status"] = store.normalize_task_status(t.get("status"))
    return {"tasks": tasks}


@app.get("/api/tasks/{task_id}")
async def api_task(task_id: str):
    t = store.task_get(task_id)
    if not t:
        raise HTTPException(404, "task not found")
    t["status"] = store.normalize_task_status(t.get("status"))
    return t


@app.post("/api/tasks/{task_id}/cancel")
async def api_task_cancel(task_id: str):
    t = store.task_get(task_id)
    if not t:
        raise HTTPException(404, "task not found")
    # ADR-007 §2.4 / B3 修复：写 cancel_requested，不直接覆盖终态；通知执行端中断，
    # 终态由执行确认（完成先到则 completed + cancel_requested=true，单一终态）。
    res = store.task_request_cancel(task_id)
    if store.normalize_task_status(t["status"]) not in store.TASK_TERMINAL:
        with contextlib.suppress(Exception):
            await gw.cancel(task_id)
    store.audit("console", "task.cancel_requested", {}, task_id=task_id, source="console")
    out = store.task_get(task_id)
    if out:
        out["status"] = store.normalize_task_status(out.get("status"))
    return {"ok": True, "task": out, "note": res.get("note")}


# ---------------------------------------------------------------- 角色

@app.get("/api/loras")
async def api_loras(q: str = ""):
    """代理网关的 LoRA 列表，给 Console 的角色编辑器和调试用。"""
    try:
        r = await gw.loras(q)
        return r
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "loras": []}, status_code=503)


@app.get("/api/models")
async def api_models():
    try:
        return await gw.models()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "models": []}, status_code=503)


@app.get("/api/voices")
async def api_voices():
    try:
        return await gw.voices()
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "voices": []}, status_code=503)


@app.get("/api/characters")
async def api_characters():
    return {"characters": list(store.load_characters().values())}


@app.get("/api/characters/{char_id}")
async def api_character(char_id: str):
    ch = store.get_character(char_id)
    if not ch:
        raise HTTPException(404, "character not found")
    return ch


@app.put("/api/characters/{char_id}")
async def api_character_put(char_id: str, body: dict = Body(...)):
    if not char_id.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(400, "非法角色 id")
    saved = store.save_character(char_id, body)
    store.audit("console", "character.save", {"id": char_id}, character=char_id, source="console")
    return {"ok": True, "character": saved}


@app.get("/api/characters/{char_id}/constitution")
async def api_constitution(char_id: str):
    return {"active": store.constitution_get(char_id),
            "versions": store.constitution_versions(char_id),
            "fallback": store.constitution_text(char_id)}


@app.put("/api/characters/{char_id}/constitution")
async def api_constitution_put(char_id: str, body: dict = Body(...)):
    text = str(body.get("text") or "").strip()
    if len(text) < 10:
        raise HTTPException(400, "宪法文本太短")
    row = store.constitution_set(char_id, text, str(body.get("note") or ""))
    store.audit("console", "constitution.save", {"version": row.get("version")},
                character=char_id, source="console")
    return {"ok": True, "constitution": row}


# ---------------------------------------------------------------- 记忆

@app.get("/api/memory/users")
async def api_mem_users():
    return {"users": store.mem_users()}


@app.get("/api/memory/user/{user_id}")
async def api_mem_user(user_id: str):
    return {
        "user_id": user_id,
        "profile": store.mem_list(user_id),
        "timeline": store.timeline_list(user_id),
        "candidates": [c for c in store.cand_list("pending", 200) if c.get("user_id") == user_id],
    }


@app.get("/api/memory/candidates")
async def api_candidates(status: str = "pending", limit: int = 100):
    return {"candidates": store.cand_list(status, limit)}


@app.post("/api/memory/candidates/{cid}/approve")
async def api_cand_approve(cid: str, body: dict = Body(default={})):
    c = store.cand_get(cid)
    if not c:
        raise HTTPException(404, "candidate not found")
    out = store.cand_decide(cid, True, (body or {}).get("value"))
    store.audit("console", "memory.approve", {"key": c["key"], "value": c["value"]},
                source="console")
    return {"ok": True, "candidate": out}


@app.post("/api/memory/candidates/{cid}/reject")
async def api_cand_reject(cid: str):
    c = store.cand_get(cid)
    if not c:
        raise HTTPException(404, "candidate not found")
    out = store.cand_decide(cid, False)
    store.audit("console", "memory.reject", {"key": c["key"]}, source="console")
    return {"ok": True, "candidate": out}


@app.post("/api/memory/forget")
async def api_mem_forget(body: dict = Body(...)):
    user_id = str(body.get("user_id") or "")
    key = str(body.get("key") or "")
    if not user_id or not key:
        raise HTTPException(400, "user_id 与 key 必填")
    ok = store.mem_forget(user_id, key)
    store.audit("console", "memory.forget", {"user_id": user_id, "key": key}, source="console")
    return {"ok": ok}


@app.post("/api/memory/timeline")
async def api_timeline_add(body: dict = Body(...)):
    tid = store.timeline_add(str(body.get("user_id") or ""), str(body.get("text") or ""),
                             kind=str(body.get("kind") or "event"),
                             character=body.get("character"))
    return {"ok": True, "id": tid}


# ---------------------------------------------------------------- 对话上下文（LangBot）
# QQ 走的是 LangBot 自己的 transcript / runner_state，跟 Core 的 memory 是两套。
# 这里只做「看」和「重置」：读取 LangBot 的 SQLite（只读），重置走它官方 HTTP 接口。
# 重置语义（源自 LangBot context_reset.py）：插入一条 generation 标记，
# 之后 transcript 里 created_at 早于该标记的消息不再进入模型上下文（历史仍保留）。

LB = (CFG.get("integrations") or {}).get("langbot") or {}
LB_BASE = str(LB.get("base_url") or "http://127.0.0.1:5300").rstrip("/")
LB_KEY = str(LB.get("api_key") or "")
LB_BOT = str(LB.get("bot_id") or "")
LB_DB = str(LB.get("db_path") or "")
_SID_RE = re.compile(r"^[A-Za-z0-9_:.\-]{1,160}$")


def _ts_ms(value: Any) -> int | None:
    """LangBot 存的是 UTC 的 'YYYY-MM-DD HH:MM:SS.ffffff' 字符串。"""
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
    return None


def _lb_query(sql: str, args: tuple = ()) -> list[dict]:
    if not LB_DB or not Path(LB_DB).exists():
        raise HTTPException(503, f"LangBot 数据库不可用: {LB_DB or '(未配置)'}")
    con = sqlite3.connect(f"file:{LB_DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _lb_bot_map() -> dict[str, str]:
    """会话 → bot_id。同一个 LangBot 里可能挂着多个 bot：
    QQ 机器人是 UUID，Open WebUI / WebChat 的 websocket 会话属于 'websocket-proxy-bot'。
    重置时必须用它自己的 bot_id，否则 LangBot 会按 bot 过滤后报「资源不存在」。
    """
    try:
        rows = _lb_query(
            "SELECT session_id, bot_id FROM monitoring_sessions WHERE bot_id IS NOT NULL"
        )
    except Exception:
        return {}
    return {r["session_id"]: r["bot_id"] for r in rows}


def _lb_bot_for(session_id: str) -> str:
    return _lb_bot_map().get(session_id) or LB_BOT


def _lb_sessions() -> list[dict]:
    rows = _lb_query(
        "SELECT conversation_id AS session_id, COUNT(*) AS total,"
        " MIN(created_at) AS first_at, MAX(created_at) AS last_at"
        " FROM transcript GROUP BY conversation_id"
    )
    resets = {
        r["conversation_id"]: r["at"]
        for r in _lb_query(
            "SELECT conversation_id, MAX(created_at) AS at FROM runner_state"
            " WHERE runner_id='__langbot_context_reset__' GROUP BY conversation_id"
        )
    }
    out: list[dict] = []
    bots = _lb_bot_map()
    for r in rows:
        sid = r["session_id"]
        reset_at = resets.get(sid)
        active = r["total"]
        if reset_at:
            active = _lb_query(
                "SELECT COUNT(*) AS n FROM transcript WHERE conversation_id=? AND created_at > ?",
                (sid, reset_at),
            )[0]["n"]
        out.append({
            "session_id": sid,
            "bot_id": bots.get(sid) or LB_BOT,
            "platform": "group" if sid.startswith("group_") else "person",
            "total_messages": int(r["total"]),
            "active_messages": int(active),
            "first_at": _ts_ms(r["first_at"]),
            "last_at": _ts_ms(r["last_at"]),
            "last_reset_at": _ts_ms(reset_at),
        })
    out.sort(key=lambda x: x["last_at"] or 0, reverse=True)
    return out


@app.get("/api/context/sessions")
async def api_context_sessions():
    try:
        sessions = _lb_sessions()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"读取 LangBot 会话失败: {exc}")
    return {"ok": True, "bot_id": LB_BOT, "sessions": sessions}


@app.get("/api/context/sessions/{session_id}/messages")
async def api_context_messages(session_id: str, limit: int = 60):
    if not _SID_RE.match(session_id):
        raise HTTPException(400, "session_id 非法")
    limit = max(1, min(int(limit), 200))
    rows = _lb_query(
        "SELECT role, item_type, content, created_at FROM transcript"
        " WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    )
    msgs = [
        {
            "role": r["role"],
            "type": r["item_type"],
            "content": (r["content"] or "")[:4000],
            "at": _ts_ms(r["created_at"]),
        }
        for r in reversed(rows)
    ]
    return {"ok": True, "session_id": session_id, "messages": msgs}


@app.post("/api/context/sessions/{session_id}/reset")
async def api_context_reset(session_id: str):
    if not _SID_RE.match(session_id):
        raise HTTPException(400, "session_id 非法")
    if not LB_KEY:
        raise HTTPException(503, "未配置 LangBot api_key")
    try:
        bot_id = _lb_bot_for(session_id)
    except Exception:
        bot_id = LB_BOT
    if not bot_id:
        raise HTTPException(503, "无法确定该会话的 bot_id")
    url = f"{LB_BASE}/api/v1/monitoring/sessions/reset-context"
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(url, json={"bot_id": bot_id, "session_id": session_id},
                             headers={"Authorization": f"Bearer {LB_KEY}"})
    except Exception as exc:
        store.audit("console", "context.reset", {"session_id": session_id, "bot_id": bot_id},
                    result="error", error=str(exc), source="console")
        raise HTTPException(502, f"LangBot 不可达: {exc}")
    try:
        data = r.json()
    except Exception:
        data = {}
    ok = r.status_code == 200 and data.get("code") == 0
    store.audit("console", "context.reset",
                {"session_id": session_id, "bot_id": bot_id, "status": r.status_code},
                result="ok" if ok else "error",
                error=None if ok else str(data)[:300], source="console")
    if not ok:
        raise HTTPException(502 if r.status_code < 400 else r.status_code, f"重置失败: {data}")
    return {"ok": True, "session_id": session_id, "bot_id": bot_id}


# ---------------------------------------------------------------- 审计

@app.get("/api/audit")
async def api_audit(limit: int = 100, kind: str | None = None):
    return {"audit": store.audit_list(limit=limit, kind=kind)}


# ---------------------------------------------------------------- QQ OneBot

@app.post("/onebot/event")
async def onebot_event(request: Request):
    """OneBot v11 HTTP webhook（B1）。仅本机、需 token；处理放到后台，尽快返回。"""
    if not qq_adapter.cfg.enabled:
        return JSONResponse({"error": "qq adapter disabled"}, status_code=404)
    raw = await request.body()
    if not qq_adapter.authorized(request, raw):
        return JSONResponse({"error": "unauthorized"}, status_code=403)
    try:
        event = json.loads(raw.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    if not isinstance(event, dict):
        return JSONResponse({"error": "invalid event"}, status_code=400)
    task = asyncio.create_task(qq_adapter.handle_event(event))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return Response(status_code=204)


@app.get("/api/qq/status")
async def api_qq_status():
    return {"ok": True, **qq_adapter.status()}


# ---------------------------------------------------------------- 控制台静态

if CONSOLE_DIR.exists():
    app.mount("/console", StaticFiles(directory=str(CONSOLE_DIR), html=True), name="console")
else:
    @app.get("/console")
    async def console_placeholder():
        return JSONResponse({"error": "console 尚未安装", "expected": str(CONSOLE_DIR)}, 503)

if STUDIO_DIR.exists():
    app.mount("/studio", StaticFiles(directory=str(STUDIO_DIR), html=True), name="studio")
else:
    @app.get("/studio")
    async def studio_placeholder():
        return JSONResponse({"error": "Studio 尚未构建", "expected": str(STUDIO_DIR)}, 503)


def main() -> None:
    host = CORE.get("host", "0.0.0.0")
    port = int(CORE.get("port", 8900))
    print(f"Möbius Core 启动于 http://{host}:{port}  （控制台 /console）", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
