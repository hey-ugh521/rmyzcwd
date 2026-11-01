"""媒体预热：ComfyUI 启动后先跑一次"假任务"，把模型初始化/编译代价提前付掉。

排查背景（2026-11-02）：
- 千问工作流本身未改动（与原始模板一致，13 节点）
- 冷启动后首次千问执行包含 "Model Initializing ... 2:25"（aimdo/编译初始化）
  以及显存换页停顿（单步 97s），首次耗时可到 12~14 分钟
- 预热后再跑：44~56 秒

用法：
  python ops/warmup_media.py --model qwen_image_2_1
  python ops/warmup_media.py --model anime_illustrious
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

GATEWAY = "http://127.0.0.1:8899/mcp"


async def _run(model: str, prompt: str, timeout_sec: int) -> int:
    from mcp import ClientSession
    try:
        from mcp.client.streamable_http import streamablehttp_client as connect
    except ImportError:
        from mcp.client.streamable_http import streamable_http_client as connect

    t0 = time.time()
    async with connect(GATEWAY) as c:
        async with ClientSession(c[0], c[1]) as s:
            await s.initialize()

            async def call(tool: str, **kw):
                r = await s.call_tool(tool, kw)
                text = "".join(getattr(b, "text", "") for b in (r.content or []))
                return json.loads(text)

            sub = await call(
                "comfyui_task_submit", prompt=prompt, model=model,
                idempotency_key=f"warmup-{model}-{int(t0)}",
            )
            if not sub.get("task_id"):
                print(f"预热失败：submit 无 task_id（{json.dumps(sub, ensure_ascii=False)[:200]}）")
                return 1
            task_id = sub["task_id"]
            print(f"预热任务已提交：{task_id} model={model} workflow={sub.get('workflow')}")
            while time.time() - t0 < timeout_sec:
                st = await call("comfyui_task_status", task_id=task_id)
                task = st.get("task") or {}
                status = task.get("status")
                if status in ("completed", "failed", "cancelled", "submission_unknown"):
                    seconds = (task.get("result") or {}).get("seconds")
                    print(f"预热结束：{status} 用时 {seconds}s（墙钟 {time.time()-t0:.0f}s）")
                    return 0 if status == "completed" else 1
                await asyncio.sleep(6)
    print(f"预热超时（{timeout_sec}s），任务 {task_id} 仍在后台")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen_image_2_1")
    ap.add_argument("--prompt", default="warmup, a simple still life")
    ap.add_argument("--timeout-sec", type=int, default=900)
    args = ap.parse_args()
    try:
        return asyncio.run(_run(args.model, args.prompt, args.timeout_sec))
    except Exception as exc:  # noqa: BLE001
        print(f"预热失败：{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
