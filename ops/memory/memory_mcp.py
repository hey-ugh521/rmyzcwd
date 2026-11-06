"""Möbius Memory Service（MCP，:8902）

自建长期记忆服务：不依赖 LangBot 的 LTM 插件与知识库校验。
- 存储：PostgreSQL mobius 库（memory_facts / memory_candidates / memory_events）
- 语义召回：本地 bge-m3 (:8903) 嵌入 + 余弦；失败回退关键词匹配
- 通过 MCP 暴露给 LangBot，bot 直接调用工具写入/召回

运行：D:\\AI\\qqbot-mcp\\Scripts\\python.exe ops\\memory\\memory_mcp.py --port 8902
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
from typing import Any

import httpx
import psycopg
from mcp.server.mcpserver import MCPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import credentials  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mobius.memory")

DSN = os.environ.get("MOBIUS_MEMORY_DSN") or credentials.pg_dsn("mobius")
EMBED_URL = os.environ.get("MOBIUS_EMBED_URL", "http://127.0.0.1:8903/v1/embeddings")
EMBED_MODEL = os.environ.get("MOBIUS_EMBED_MODEL", "bge-m3")

DDL = """
create extension if not exists pgcrypto;
create table if not exists memory_facts (
    id uuid primary key default gen_random_uuid(),
    scope text not null default 'user',
    user_id text not null default '',
    group_id text not null default '',
    kind text not null default 'profile',
    content text not null,
    embedding jsonb,
    source text not null default '',
    confidence real not null default 1.0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz
);
create index if not exists idx_memory_facts_scope on memory_facts(scope, user_id, group_id) where deleted_at is null;

create table if not exists memory_candidates (
    id uuid primary key default gen_random_uuid(),
    scope text not null default 'user',
    user_id text not null default '',
    group_id text not null default '',
    kind text not null default 'profile',
    content text not null,
    embedding jsonb,
    reason text not null default '',
    status text not null default 'pending',
    created_at timestamptz not null default now(),
    reviewed_at timestamptz
);
create index if not exists idx_memory_candidates_status on memory_candidates(status, user_id, group_id);

create table if not exists memory_events (
    id bigserial primary key,
    ts timestamptz not null default now(),
    action text not null,
    scope text not null default '',
    user_id text not null default '',
    detail jsonb
);
"""


def db() -> psycopg.Connection:
    return psycopg.connect(DSN, autocommit=True)


def ensure_schema() -> None:
    with db() as conn:
        conn.execute(DDL)


def _audit(action: str, scope: str = "", user_id: str = "", detail: Any = None) -> None:
    try:
        with db() as conn:
            conn.execute(
                "insert into memory_events(action, scope, user_id, detail) values (%s,%s,%s,%s)",
                (action, scope, user_id, json.dumps(detail, ensure_ascii=False) if detail is not None else None),
            )
    except Exception:
        log.exception("audit 写入失败")


def embed(text: str) -> list[float] | None:
    try:
        with httpx.Client(timeout=20) as c:
            r = c.post(EMBED_URL, json={"model": EMBED_MODEL, "input": [text]})
            r.raise_for_status()
            data = r.json()["data"][0]["embedding"]
            return [float(x) for x in data]
    except Exception as exc:  # noqa: BLE001
        log.warning("嵌入失败，回退关键词: %s", exc)
        return None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _scope_where(scope: str, user_id: str, group_id: str) -> tuple[str, list[Any]]:
    where = "deleted_at is null"
    args: list[Any] = []
    if scope == "group" and group_id:
        where += " and scope='group' and group_id=%s"
        args.append(group_id)
    elif user_id:
        where += " and scope='user' and user_id=%s"
        args.append(user_id)
    return where, args


def _insert_fact(
    content: str, kind: str, scope: str, user_id: str, group_id: str, source: str
) -> str:
    emb = embed(content)
    with db() as conn:
        row = conn.execute(
            """insert into memory_facts(scope,user_id,group_id,kind,content,embedding,source)
               values (%s,%s,%s,%s,%s,%s::jsonb,%s) returning id""",
            (scope, user_id, group_id, kind, content, json.dumps(emb) if emb else None, source),
        ).fetchone()
    return str(row[0])


server = MCPServer(
    "mobius-memory",
    title="Möbius 长期记忆服务",
    instructions=(
        "长期记忆服务。用法：\n"
        "- 用户给出稳定、低变化的事实/偏好（称呼、喜好、身份、计划）时才调用 memory_remember；"
        "闲聊、一次性信息、敏感信息（密码/密钥/验证码）不要写。\n"
        "- 回复前若与用户历史相关，先 memory_recall 查一下，把命中的记忆自然融入回复，不要逐条复述。\n"
        "- 默认 scope=user、kind=profile；事件/计划用 kind=episode。\n"
        "- 写入默认进入候选区（candidate=true 时），重要事实才直接写；候选由 memory_list_candidates/memory_review_candidate 审核。"
    ),
)


@server.tool(name="memory_remember", description="写入一条长期记忆（事实/偏好/事件）。candidate=true 时进入候选待审核。")
def memory_remember(
    content: str,
    kind: str = "profile",
    scope: str = "user",
    user_id: str = "",
    group_id: str = "",
    candidate: bool = False,
    source: str = "qq",
) -> str:
    content = (content or "").strip()
    if not content:
        return json.dumps({"ok": False, "error": "content 不能为空"}, ensure_ascii=False)
    kind = kind if kind in ("profile", "episode") else "profile"
    scope = scope if scope in ("user", "group") else "user"
    if candidate:
        emb = embed(content)
        with db() as conn:
            row = conn.execute(
                """insert into memory_candidates(scope,user_id,group_id,kind,content,embedding,reason,status)
                   values (%s,%s,%s,%s,%s,%s::jsonb,'bot',%s) returning id""",
                (scope, user_id, group_id, kind, content, json.dumps(emb) if emb else None, "pending"),
            ).fetchone()
        _audit("candidate", scope, user_id, {"id": str(row[0]), "content": content})
        return json.dumps({"ok": True, "mode": "candidate", "id": str(row[0])}, ensure_ascii=False)
    fid = _insert_fact(content, kind, scope, user_id, group_id, source)
    _audit("remember", scope, user_id, {"id": fid, "content": content})
    return json.dumps({"ok": True, "mode": "fact", "id": fid}, ensure_ascii=False)


@server.tool(name="memory_recall", description="按语义/关键词召回当前用户的长期记忆（默认 user 作用域）。")
def memory_recall(
    query: str,
    scope: str = "user",
    user_id: str = "",
    group_id: str = "",
    top_k: int = 5,
) -> str:
    query = (query or "").strip()
    where, args = _scope_where(scope, user_id, group_id)
    with db() as conn:
        rows = conn.execute(
            f"select id, kind, content, embedding, created_at from memory_facts where {where} "
            f"order by created_at desc limit 300",
            args,
        ).fetchall()
    if not rows:
        return json.dumps({"ok": True, "memories": [], "count": 0}, ensure_ascii=False)
    qemb = embed(query) if query else None
    scored = []
    for r in rows:
        fid, kind, content, emb_json, created = r
        emb = emb_json if isinstance(emb_json, list) else (json.loads(emb_json) if emb_json else None)
        if qemb and emb:
            score = cosine(qemb, emb)
        else:
            score = 1.0 if query and query in content else 0.3
        scored.append((score, fid, kind, content, created))
    scored.sort(key=lambda x: (-x[0], x[4]))
    out = [
        {"id": str(fid), "kind": kind, "content": content, "score": round(score, 3)}
        for score, fid, kind, content, _ in scored[: max(1, min(20, top_k))]
    ]
    _audit("recall", scope, user_id, {"query": query, "count": len(out)})
    return json.dumps({"ok": True, "memories": out, "count": len(out)}, ensure_ascii=False)


@server.tool(name="memory_forget", description="软删除一条记忆（按 id）。")
def memory_forget(memory_id: str, reason: str = "") -> str:
    with db() as conn:
        row = conn.execute(
            "update memory_facts set deleted_at=now(), updated_at=now() where id=%s and deleted_at is null returning id",
            (memory_id,),
        ).fetchone()
    if not row:
        return json.dumps({"ok": False, "error": "memory 不存在或已删除"}, ensure_ascii=False)
    _audit("forget", "", "", {"id": memory_id, "reason": reason})
    return json.dumps({"ok": True, "id": memory_id}, ensure_ascii=False)


@server.tool(name="memory_list_candidates", description="列出待审核的记忆候选。")
def memory_list_candidates(user_id: str = "", group_id: str = "", status: str = "pending", limit: int = 20) -> str:
    args: list[Any] = [status]
    where = "status=%s"
    if user_id:
        where += " and user_id=%s"
        args.append(user_id)
    if group_id:
        where += " and group_id=%s"
        args.append(group_id)
    with db() as conn:
        rows = conn.execute(
            f"select id, kind, content, status, created_at from memory_candidates where {where} order by created_at limit %s",
            args + [max(1, min(50, limit))],
        ).fetchall()
    out = [
        {"id": str(r[0]), "kind": r[1], "content": r[2], "status": r[3]}
        for r in rows
    ]
    return json.dumps({"ok": True, "candidates": out, "count": len(out)}, ensure_ascii=False)


@server.tool(name="memory_review_candidate", description="审核候选：accept=true 写入长期记忆，false 拒绝。")
def memory_review_candidate(candidate_id: str, accept: bool, reason: str = "") -> str:
    with db() as conn:
        row = conn.execute(
            "select scope,user_id,group_id,kind,content,status from memory_candidates where id=%s",
            (candidate_id,),
        ).fetchone()
        if not row:
            return json.dumps({"ok": False, "error": "候选不存在"}, ensure_ascii=False)
        scope, user_id, group_id, kind, content, status = row
        if status != "pending":
            return json.dumps({"ok": False, "error": f"候选已是 {status}"}, ensure_ascii=False)
        new_status = "accepted" if accept else "rejected"
        conn.execute(
            "update memory_candidates set status=%s, reviewed_at=now() where id=%s",
            (new_status, candidate_id),
        )
    if accept:
        fid = _insert_fact(content, kind, scope, user_id, group_id, "candidate")
        _audit("candidate_accept", scope, user_id, {"candidate_id": candidate_id, "fact_id": fid})
        return json.dumps({"ok": True, "status": new_status, "fact_id": fid}, ensure_ascii=False)
    _audit("candidate_reject", scope, user_id, {"candidate_id": candidate_id, "reason": reason})
    return json.dumps({"ok": True, "status": new_status}, ensure_ascii=False)


@server.tool(name="memory_stats", description="记忆统计（事实数/候选数/最近写入）。")
def memory_stats(user_id: str = "", group_id: str = "") -> str:
    where = "deleted_at is null"
    args: list[Any] = []
    if user_id:
        where += " and user_id=%s"
        args.append(user_id)
    with db() as conn:
        facts = conn.execute(f"select count(*) from memory_facts where {where}", args).fetchone()[0]
        cands = conn.execute(
            "select status, count(*) from memory_candidates group by status"
        ).fetchall()
        last = conn.execute(
            "select action, ts from memory_events order by id desc limit 5"
        ).fetchall()
    return json.dumps(
        {
            "ok": True,
            "facts": facts,
            "candidates": {r[0]: r[1] for r in cands},
            "recent_events": [{"action": r[0], "ts": str(r[1])} for r in last],
        },
        ensure_ascii=False,
    )


mcp_app = server.streamable_http_app(
    streamable_http_path="/mcp",
    host="0.0.0.0",
    json_response=False,
    stateless_http=False,
    session_idle_timeout=None,
)


async def app(scope, receive, send):
    """在 MCP ASGI 应用外包一层 /health。"""
    if scope["type"] == "http" and scope["path"] == "/health":
        body = b'{"ok":true,"service":"mobius-memory"}'
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": body})
        return
    await mcp_app(scope, receive, send)


def port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Möbius Memory MCP")
    parser.add_argument("--port", type=int, default=int(os.environ.get("MOBIUS_MEMORY_PORT", 8902)))
    parser.add_argument("--host", default=os.environ.get("MOBIUS_MEMORY_HOST", "127.0.0.1"))
    args = parser.parse_args()
    if port_in_use(args.host, args.port):
        log.warning("已有记忆服务在监听 %s:%s，本实例退出", args.host, args.port)
        return 0
    ensure_schema()
    import uvicorn

    log.info("Möbius Memory Service 启动于 http://%s:%s (MCP=/mcp)", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
