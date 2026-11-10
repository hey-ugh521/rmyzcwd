"""P0.4 遗留场景验证：submission_unknown 端到端 + 轮询耗尽不取消。

做法：起一个"黑洞"HTTP 服务模拟 ComfyUI：
- /system_stats 正常返回（让 ensure 通过）
- /prompt 接受连接后永不响应（触发读超时 → 提交结果不确定）

运行：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe media_gateway\\test_submission_unknown.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

tmp = tempfile.mkdtemp(prefix="mobius-su-")
os.environ["MOBIUS_TASK_DB"] = os.path.join(tmp, "tasks.db")
os.environ["MOBIUS_COMFY_SUBMIT_TIMEOUT"] = "3"
os.environ["MOBIUS_COMFY_START_TIMEOUT"] = "5"
os.environ["MOBIUS_MEDIA_MODE"] = "off"  # 不走 Core 唤醒
# 本用例验证网关本地执行账本的 P0.4 语义（store.submit / comfyui_task_status 读本地）。
# M1(ADR-007) 后 Core 为默认 owner 且 submit 对 Core fail-closed，故显式声明 gateway 回滚模式。
os.environ["MOBIUS_TASK_OWNER"] = "gateway"

import server  # noqa: E402

BLACKHOLE_PORT = 8199


async def blackhole() -> None:
    async def handle(reader, writer):
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            first = request.split(b"\r\n")[0].decode(errors="replace")
            if "/system_stats" in first:
                body = json.dumps({"system": {"comfyui_version": "test"}, "devices": []}).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
                )
                await writer.drain()
            # /prompt：保持连接、永不响应
            await asyncio.sleep(300)
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    srv = await asyncio.start_server(handle, "127.0.0.1", BLACKHOLE_PORT)
    async with srv:
        await srv.serve_forever()


async def run() -> int:
    bh = asyncio.create_task(blackhole())
    await asyncio.sleep(0.3)

    settings = server.Settings()
    settings.comfy_url = f"http://127.0.0.1:{BLACKHOLE_PORT}"
    store = server.TaskStore(settings)
    server.store = store
    store.ensure_worker()
    ok = True

    # 场景 1：提交超时 → submission_unknown（不是 failed、不自动重提）
    res = await server.submit_generate(
        settings,
        {"prompt": "p0.4 submission unknown test", "model": "anime_illustrious",
         "idempotency_key": f"su-{int(time.time())}"},
        async_mode=True,
    )
    tid = res.get("task_id")
    print(f"提交: task_id={tid}")
    deadline = time.time() + 30
    rec = None
    while time.time() < deadline:
        rec = store.get(tid)
        if rec and rec.status in server.TERMINAL:
            break
        await asyncio.sleep(0.5)
    status = getattr(rec, "status", None)
    print(f"场景1 最终状态: {status} error_code={getattr(rec, 'error_code', None)}")
    ok &= status == server.STATUS_UNKNOWN
    # 状态可被 task_status 读出且带说明
    out = await server.comfyui_task_status(tid)
    data = json.loads(out)
    task = data.get("task") or data
    print(f"场景1 查询: status={task.get('status')} code={task.get('error_code')}")
    ok &= task.get("status") == server.STATUS_UNKNOWN and task.get("error_code") == "submission_unknown"

    # 场景 2：轮询 61 次不改变任务状态、不取消
    task2, _ = store.submit(
        kind="image", payload={"prompt": "poll budget"}, spec={"prompt": "poll budget"},
        source="test", user_id="u", session_id="s",
    )
    tid2 = task2.id if hasattr(task2, "id") else task2["id"]
    before = store.get(tid2).status
    for _ in range(61):
        store.get(tid2)
    after = store.get(tid2).status
    print(f"场景2 轮询 61 次: {before} -> {after}（未取消={after != 'cancelled'}）")
    ok &= before == after and after != server.STATUS_CANCELLED
    claimed = store.claim_next()
    if claimed is not None and getattr(claimed, "id", None) == tid2:
        store.finish(tid2, status=server.STATUS_COMPLETED, result={"filename": "x.png", "seconds": 1})
    print(f"场景2 之后可正常完成: {store.get(tid2).status}")
    ok &= store.get(tid2).status == server.STATUS_COMPLETED

    # 场景 3：并发两个提交（都走黑洞）→ 互不干扰，都为 submission_unknown
    t0 = time.time()
    async def one(i):
        return await server.submit_generate(
            settings,
            {"prompt": f"concurrent {i}", "model": "anime_illustrious",
             "idempotency_key": f"su-conc-{i}-{int(time.time())}"},
            async_mode=True,
        )
    rs = await asyncio.gather(one(1), one(2))
    ids = [r.get("task_id") for r in rs]
    deadline = time.time() + 40
    statuses = {}
    while time.time() < deadline:
        statuses = {i: getattr(store.get(i), "status", None) for i in ids}
        if all(x in server.TERMINAL for x in statuses.values()):
            break
        await asyncio.sleep(0.5)
    print(f"场景3 并发: {ids} -> {statuses} 墙钟={time.time()-t0:.0f}s")
    ok &= all(v == server.STATUS_UNKNOWN for v in statuses.values()) and len(set(ids)) == 2

    bh.cancel()
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
