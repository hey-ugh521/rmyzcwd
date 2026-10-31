"""P0.4-B 轮询限流契约测试（确定性层）。

直接对 Gateway 的 comfyui_task_status 契约做验证，不经过模型：
  1. 首次查询 -> 正常返回
  2. 5 秒内重复查询 -> poll_too_fast + task_id + retry_after_ms
  3. poll_too_fast 不改变任务状态
  4. 被拒绝的查询不重置等待窗口（后续 retry_after 不会越来越大）
  5. retry_after_ms 到期后可正常查询
  6. 终态任务可立即读取，不受间隔限制
  7. 尝试次数与有效次数分别记录

测试自建夹具任务（status=pending 不会被执行），结束时清理。

运行：D:\\AI\\qqbot-mcp\\Scripts\\python.exe tests\\test_p0_4_poll_contract.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
import uuid

TASK_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "media_gateway", "data", "tasks.db")
GW = "http://127.0.0.1:8899/mcp"
FIXTURE = "g_pollcontract01"
TERMINAL_FIXTURE = "g_pollterminal01"

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} {detail}")


def _write_fixtures() -> None:
    con = sqlite3.connect(TASK_DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    now = time.time()
    for tid, status, code in ((FIXTURE, "running", None),
                              (TERMINAL_FIXTURE, "completed", None)):
        con.execute("delete from task_snapshot where task_id=?", (tid,))
        con.execute(
            "INSERT INTO task_snapshot(task_id,idempotency_key,payload_hash,kind,status,source,"
            "prompt,params_json,payload_json,remote_id,result_json,error_code,attempts,"
            "created_at,started_at,finished_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, f"pollcontract-{status}-{int(now)}", "fixture", "image", status, "fixture",
             "poll contract fixture", "{}", "{}", ("fixture-uuid" if status == "running" else None),
             json.dumps({"filename": "x.png"}) if status == "completed" else None,
             code, 1, now, now, now if status == "completed" else None, now),
        )
    con.commit()
    con.close()


def _cleanup() -> None:
    con = sqlite3.connect(TASK_DB, timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("delete from task_snapshot where task_id in (?,?)", (FIXTURE, TERMINAL_FIXTURE))
    con.commit()
    con.close()


async def main() -> int:
    from mcp import ClientSession
    try:
        from mcp.client.streamable_http import streamablehttp_client as connect
    except ImportError:
        from mcp.client.streamable_http import streamable_http_client as connect

    _write_fixtures()
    try:
        async with connect(GW) as c:
            async with ClientSession(c[0], c[1]) as s:
                await s.initialize()

                async def status(tid: str) -> dict:
                    r = await s.call_tool("comfyui_task_status", {"task_id": tid})
                    return json.loads("".join(getattr(b, "text", "") for b in (r.content or [])))

                # 1. 首次查询正常
                first = await status(FIXTURE)
                check("1.1 首次查询正常返回", first.get("ok") is True and bool(first.get("task")),
                      json.dumps(first)[:160])
                status_before = (first.get("task") or {}).get("status")

                # 2. 立即重复查询 -> poll_too_fast
                second = await status(FIXTURE)
                check("2.1 5 秒内重复查询被拒绝", second.get("ok") is False and second.get("error") == "poll_too_fast",
                      json.dumps(second)[:160])
                check("2.2 拒绝响应含 task_id", second.get("task_id") == FIXTURE,
                      str(second.get("task_id")))
                check("2.3 拒绝响应含 retry_after_ms>0",
                      isinstance(second.get("retry_after_ms"), int) and second["retry_after_ms"] > 0,
                      str(second.get("retry_after_ms")))

                # 3. 不改变任务状态
                con = sqlite3.connect(f"file:{TASK_DB}?mode=ro", uri=True, timeout=20)
                status_after = con.execute(
                    "select status from task_snapshot where task_id=?", (FIXTURE,)
                ).fetchone()[0]
                con.close()
                check("3.1 poll_too_fast 不改任务状态",
                      status_after == status_before == "running", f"{status_before} -> {status_after}")

                # 4. 被拒绝不重置窗口：连续两次拒绝的 retry_after 不应增大
                third = await status(FIXTURE)
                check("4.1 连续被拒（窗口未被重置）",
                      third.get("error") == "poll_too_fast", json.dumps(third)[:120])
                check("4.2 retry_after 未增大",
                      third["retry_after_ms"] <= second["retry_after_ms"],
                      f"{second['retry_after_ms']} -> {third['retry_after_ms']}")

                # 5. 等 retry_after 后可正常查询
                wait_s = max(second["retry_after_ms"], third["retry_after_ms"]) / 1000.0 + 0.3
                await asyncio.sleep(wait_s)
                healed = await status(FIXTURE)
                check(f"5.1 等待 {wait_s:.1f}s 后恢复正常",
                      healed.get("ok") is True and bool(healed.get("task")),
                      json.dumps(healed)[:160])

                # 6. 终态任务立即读取（连续两次）
                t1 = await status(TERMINAL_FIXTURE)
                t2 = await status(TERMINAL_FIXTURE)
                check("6.1 终态首次可读", t1.get("ok") is True)
                check("6.2 终态可立即连读（不限流）", t2.get("ok") is True,
                      json.dumps(t2)[:160])

                # 7. 尝试/有效分别记录
                poll = (t2.get("poll") or {})
                check("7.1 返回尝试数与有效数",
                      isinstance(poll.get("attempted_queries"), int)
                      and isinstance(poll.get("valid_queries"), int)
                      and poll["attempted_queries"] >= poll["valid_queries"],
                      json.dumps(poll))
    finally:
        _cleanup()

    print(f"\n结果：{len(PASSED)} 通过，{len(FAILED)} 失败")
    for name, detail in FAILED:
        print(f"  [FAIL] {name} {detail}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

