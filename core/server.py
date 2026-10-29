"""Möbius Core —— HTTP 服务。

一张进程三张脸：
- /v1/*       模型侧（OpenAI 兼容 + 原生 SSE），给 LangBot / Open WebUI Pipe
- /api/*      控制台侧，给 Möbius Console 与运维脚本
- /console    内嵌单页控制台
"""
from __future__ import annotations

import asyncio
import contextlib
import json
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
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import store
from .agent import Agent
from .services import GatewayClient, MediaDisabled, ServiceError, Supervisor

CFG = store.CFG
CORE = CFG.get("core", {})
API_KEY = CORE.get("api_key", "")
START_TS = time.time()

sup = Supervisor()
gw = GatewayClient()
agent = Agent(sup, gw)

CONSOLE_DIR = Path(__file__).resolve().parent / "console"

app = FastAPI(title="Möbius Core", version="1.0.0")


# ---------------------------------------------------------------- 鉴权

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


@app.middleware("http")
async def auth_mw(request: Request, call_next):
    path = request.url.path
    if path.startswith("/v1") or path.startswith("/api"):
        host = request.client.host if request.client else ""
        if host not in LOOPBACK:
            auth = request.headers.get("authorization", "")
            if not API_KEY or auth != f"Bearer {API_KEY}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)


# ---------------------------------------------------------------- 生命周期

@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    store.audit("core", "core.start", {"port": CORE.get("port")})
    task = asyncio.create_task(sup.idle_loop())
    # 网关是轻进程（不占显存）：无论什么媒体模式都随 Core 起来，
    # 否则 LangBot 开机时连不上 MCP，要等它自己重连（实测会一直连不上）。
    asyncio.create_task(_boot())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(Exception):
            await task
        store.audit("core", "core.stop", {})


async def _boot():
    try:
        await sup.ensure_gateway()
    except Exception as e:
        store.audit("supervisor", "gateway.boot.fail", {"error": str(e)}, result="fail")
    if CORE.get("prewarm_on_start"):
        await _prewarm()


async def _prewarm():
    """预热 ComfyUI。注意：不在这里改用户的媒体模式，预热只是"现在就拉起来"。"""
    try:
        await sup.ensure_gateway()
        await sup.start("comfyui")
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
    """把网关里已经结束、但本地还是 running 的任务同步过来。"""
    for t in store.tasks_active():
        rid = t.get("remote_id")
        if not rid:
            continue
        try:
            r = await gw.task(rid)
        except Exception:
            continue
        obj = r.get("task") or r
        st = obj.get("status")
        if st in ("succeeded", "failed", "canceled"):
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

@app.get("/api/tasks")
async def api_tasks(limit: int = 50, status: str | None = None, source: str | None = None):
    await _sync_tasks()
    return {"tasks": store.tasks_list(limit=limit, status=status, source=source)}


@app.get("/api/tasks/{task_id}")
async def api_task(task_id: str):
    t = store.task_get(task_id)
    if not t:
        raise HTTPException(404, "task not found")
    return t


@app.post("/api/tasks/{task_id}/cancel")
async def api_task_cancel(task_id: str):
    t = store.task_get(task_id)
    if not t:
        raise HTTPException(404, "task not found")
    if t["status"] in ("succeeded", "failed", "canceled"):
        return {"ok": True, "task": t, "note": "已结束，无需取消"}
    if t.get("remote_id"):
        with contextlib.suppress(Exception):
            await gw.cancel(t["remote_id"])
    store.task_update(task_id, status="canceled", finished_at=store.now())
    store.audit("console", "task.cancel", {}, task_id=task_id, source="console")
    return {"ok": True, "task": store.task_get(task_id)}


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


# ---------------------------------------------------------------- 控制台静态

if CONSOLE_DIR.exists():
    app.mount("/console", StaticFiles(directory=str(CONSOLE_DIR), html=True), name="console")
else:
    @app.get("/console")
    async def console_placeholder():
        return JSONResponse({"error": "console 尚未安装", "expected": str(CONSOLE_DIR)}, 503)


def main() -> None:
    host = CORE.get("host", "0.0.0.0")
    port = int(CORE.get("port", 8900))
    print(f"Möbius Core 启动于 http://{host}:{port}  （控制台 /console）", flush=True)
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
