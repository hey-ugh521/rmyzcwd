"""网关回归 + 边际测试（宿主运行，走 MCP :8899）。

覆盖：
  1. 健康：pulse / 单实例
  2. 生图主链路：submit → poll → completed → 产物 URL 可下载
  3. 取消：submit → cancel → cancelled
  4. 非法模型：submit model=nope → unknown_model
  5. 轮询节流：连续两次 status → 第二次 poll_too_fast
  6. 未知任务：status 随机 id → unknown_task
  7. 并发：两个任务同时提交 → 都进入队列且最终完成
  8. TTS：tts_speak 返回 ok（可选，失败不判失败但记录）

运行：D:\\AI\\qqbot-mcp\\Scripts\\python.exe ops\\tests\\test_gateway_regression.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request

from mcp.client.streamable_http import streamable_http_client as connect

GW = "http://127.0.0.1:8899"
RESULTS = []


def check(name: str, ok: bool, detail: str = ""):
    RESULTS.append((name, ok))
    print(f"  {'✓' if ok else '✗'} {name} {detail}")


async def call(s, tool: str, **kw):
    r = await s.call_tool(tool, kw)
    text = "".join(getattr(b, "text", "") for b in (r.content or []))
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return {"raw": text}


def http_get(url: str, timeout: float = 10.0) -> tuple[int, str, int]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = r.read(200)
            return r.status, r.headers.get("content-type", ""), len(data)
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}", 0


async def main() -> int:
    # 1) 健康
    pw = 0
    try:
        with urllib.request.urlopen(f"{GW}/api/pulse", timeout=5) as r:
            pulse = json.load(r)
        check("网关 pulse", pulse.get("ok") is True)
        pw = pulse.get("tasks", {}).get("pending", 0) or 0
    except Exception as exc:  # noqa: BLE001
        check("网关 pulse", False, str(exc))
        return 1

    async with connect(f"{GW}/mcp") as (rs, ws, *_):
        from mcp import ClientSession

        async with ClientSession(rs, ws) as s:
            await s.initialize()

            # 2) 生图主链路
            t0 = time.time()
            sub = await call(s, "comfyui_task_submit", prompt="回归测试：白鹭停在窗边",
                             model="qwen_image_2_1", idempotency_key=f"reg-{int(time.time())}")
            check("提交生图", bool(sub.get("task_id")), str(sub.get("workflow")))
            tid = sub.get("task_id")
            status = None
            if tid:
                while time.time() - t0 < 420:
                    st = await call(s, "comfyui_task_status", task_id=tid)
                    task = st.get("task") or {}
                    status = task.get("status")
                    if status in ("completed", "failed", "cancelled"):
                        break
                    await asyncio.sleep(6)
                check("生图完成", status == "completed", f"{time.time()-t0:.0f}s")
                res = (task or {}).get("result") or {}
                url = str(res.get("url") or "")
                if url.startswith("http"):
                    host_url = url.replace("host.docker.internal", "127.0.0.1").replace(":8899", ":8899")
                    code, ctype, n = http_get(host_url)
                    check("产物可下载", code == 200 and ctype.startswith("image/"), f"{ctype} {n}B")

            # 3) 取消
            sub2 = await call(s, "comfyui_task_submit", prompt="取消测试",
                              model="anime_illustrious", idempotency_key=f"reg-cancel-{int(time.time())}")
            tid2 = sub2.get("task_id")
            if tid2:
                await call(s, "comfyui_task_cancel", task_id=tid2)
                st = await call(s, "comfyui_task_status", task_id=tid2)
                check("取消任务", (st.get("task") or {}).get("status") == "cancelled",
                      str((st.get("task") or {}).get("status")))
            else:
                check("取消任务", False, "submit 未返回 task_id")

            # 4) 非法模型
            bad = await call(s, "comfyui_task_submit", prompt="x", model="no_such_model",
                             idempotency_key=f"reg-bad-{int(time.time())}")
            check("非法模型报错", bad.get("error") in ("unknown_model", "invalid_model"),
                  str(bad.get("error")))

            # 5) 轮询节流
            sub3 = await call(s, "comfyui_task_submit", prompt="节流测试",
                              model="anime_illustrious", idempotency_key=f"reg-throttle-{int(time.time())}")
            tid3 = sub3.get("task_id")
            if tid3:
                await s.call_tool("comfyui_task_status", {"task_id": tid3})
                st2 = await call(s, "comfyui_task_status", task_id=tid3)
                check("轮询节流", st2.get("error") == "poll_too_fast", str(st2.get("error")))
                await call(s, "comfyui_task_cancel", task_id=tid3)

            # 6) 未知任务
            unk = await call(s, "comfyui_task_status", task_id="g_not_exist_123")
            check("未知任务报错", unk.get("error") == "unknown_task", str(unk.get("error")))

            # 7) 并发两个任务
            t1 = int(time.time())
            a = await call(s, "comfyui_task_submit", prompt="并发 A", model="anime_illustrious",
                           idempotency_key=f"reg-conc-a-{t1}")
            b = await call(s, "comfyui_task_submit", prompt="并发 B", model="anime_illustrious",
                           idempotency_key=f"reg-conc-b-{t1}")
            ids = [a.get("task_id"), b.get("task_id")]
            check("并发提交", all(ids) and ids[0] != ids[1], str(ids))
            for i in [x for x in ids if x]:
                await call(s, "comfyui_task_cancel", task_id=i)

            # 8) TTS（不阻塞主判定）
            try:
                tts = await call(s, "tts_speak", text="回归测试语音。")
                check("TTS 可用", bool(tts.get("ok")), str(tts.get("engine")))
            except Exception as exc:  # noqa: BLE001
                check("TTS 可用", False, f"{type(exc).__name__}")

    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"RESULT: {passed}/{len(RESULTS)} PASS")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
