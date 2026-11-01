"""P0.3 验收测试（对真实运行中的网关做端到端验证）。

运行前提：网关在 127.0.0.1:8899 且 ComfyUI 可用（会真的出一张图）。

    D:\\AI\\qqbot-mcp\\Scripts\\python.exe tests\\test_p0_3_acceptance.py

覆盖验收：
  1. submit 1~3 秒内返回 task_id
  2. 状态机 pending/running -> completed
  3. 幂等：同 key 同 payload 返回原 task_id；同 key 异 payload 报冲突
  4. outbox 事件已落库（Core 未配置时保留待投递）
  5. 任务在 SQLite 中持久化（直接读库验证）
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time

GW = "http://127.0.0.1:8899/mcp"
TASK_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "media_gateway", "data", "tasks.db")

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} {detail}")


STORE_BACKEND = os.environ.get("MOBIUS_TASK_STORE", "sqlite").lower()
PG_DSN = os.environ.get("MOBIUS_TASK_DSN", "")


def db_count(sql: str, args: tuple = ()) -> int:
    """任务库计数：按后端选择 SQLite 或 PostgreSQL。"""
    if STORE_BACKEND == "pg":
        import psycopg
        pg_sql = sql.replace("?", "%s")
        with psycopg.connect(PG_DSN) as con:
            return int(con.execute(pg_sql, args).fetchone()[0])
    con = sqlite3.connect(f"file:{TASK_DB}?mode=ro", uri=True, timeout=20)
    try:
        return int(con.execute(sql, args).fetchone()[0])
    finally:
        con.close()


async def main() -> int:
    from mcp import ClientSession
    try:
        from mcp.client.streamable_http import streamablehttp_client as connect
    except ImportError:
        from mcp.client.streamable_http import streamable_http_client as connect

    async with connect(GW) as c:
        async with ClientSession(c[0], c[1]) as session:
            await session.initialize()

            async def call(tool: str, **args):
                res = await session.call_tool(tool, args)
                text = "".join(
                    getattr(block, "text", "") for block in (res.content or [])
                )
                return json.loads(text) if text.strip().startswith("{") else {"raw": text}

            run_key = f"acc-{int(time.time())}"
            # ---- 1. submit 时延 ----
            t0 = time.time()
            sub = await call(
                "comfyui_task_submit",
                prompt="test p0.3 acceptance, 1girl, snow",
                model="anime_illustrious" if False else "",
                workflow="portrait",
                idempotency_key=run_key,
            )
            submit_ms = (time.time() - t0) * 1000
            task_id = sub.get("task_id")
            check("1.1 submit 返回 task_id", bool(task_id), json.dumps(sub)[:200])
            check(f"1.2 submit 时延 {submit_ms:.0f}ms < 1000ms（原设计阈值）", submit_ms < 1000)

            # ---- 2. 状态机观察 ----
            seen: list[str] = []
            status = sub.get("status")
            seen.append(status)
            deadline = time.time() + 420
            final = None
            while time.time() < deadline:
                st = await call("comfyui_task_status", task_id=task_id)
                rec = st.get("task") or {}
                cur = rec.get("status")
                if cur and (not seen or seen[-1] != cur):
                    seen.append(cur)
                if cur in ("completed", "failed", "cancelled", "submission_unknown"):
                    final = rec
                    break
                await asyncio.sleep(2)
            check("2.1 观察到 pending 或 running", any(s in ("pending", "running") for s in seen), str(seen))
            check("2.2 最终进入终态", final is not None, str(seen))
            if final:
                check("2.3 终态为 completed", final.get("status") == "completed",
                      f"{final.get('status')} {final.get('error_code') or ''} {final.get('error') or ''}")
                check("2.4 结果含图片文件名", bool((final.get("result") or {}).get("filename")),
                      str(final.get("result"))[:200])

            # ---- 3. 幂等 ----
            again = await call(
                "comfyui_task_submit",
                prompt="test p0.3 acceptance, 1girl, snow",
                workflow="portrait",
                idempotency_key=run_key,
            )
            check("3.1 同 key 同 payload 返回原任务", again.get("task_id") == task_id,
                  f"{again.get('task_id')} vs {task_id}")
            check("3.2 命中标记为 true", again.get("idempotent_hit") is True, str(again.get("idempotent_hit")))

            conflict = await call(
                "comfyui_task_submit",
                prompt="完全不同的内容",
                workflow="portrait",
                idempotency_key=run_key,
            )
            check("3.3 同 key 异 payload 报冲突",
                  ("conflict" in json.dumps(conflict).lower())
                  or conflict.get("ok") is False,
                  json.dumps(conflict)[:200])

    # ---- 4/5. 落库与 outbox ----
    check("4.1 任务已持久化（后端=%s）" % STORE_BACKEND,
          db_count("SELECT COUNT(*) FROM task_snapshot WHERE task_id=?", (task_id,)) == 1)
    check("4.2 outbox 事件已写入（待投递 Core）",
          db_count("SELECT COUNT(*) FROM outbox") >= 1,
          str(db_count("SELECT COUNT(*) FROM outbox")))
    total = db_count("SELECT COUNT(*) FROM task_snapshot")
    check("5.1 任务表可查询（总数 %d）" % total, total >= 1)

    print(f"\n结果：{len(PASSED)} 通过，{len(FAILED)} 失败")
    for name, detail in FAILED:
        print(f"  [FAIL] {name} {detail}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))



