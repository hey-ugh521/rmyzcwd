"""MCP 协议面探针：用官方 SDK 真实连一次，确认 LangBot 能怎么用就怎么用。

    D:\\AI\\qqbot-mcp\\Scripts\\python.exe tests\\probe_mcp.py http://127.0.0.1:8899/mcp
"""
from __future__ import annotations

import asyncio
import json
import sys


async def main(url: str) -> int:
    from mcp import ClientSession
    try:
        from mcp.client.streamable_http import streamablehttp_client as _connect
    except ImportError:  # mcp SDK 2.x 改名
        from mcp.client.streamable_http import streamable_http_client as _connect

    async with _connect(url) as _conn:
        read, write = _conn[0], _conn[1]
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            info = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
            print("initialize OK:", getattr(info, "name", "?"), getattr(info, "version", "?"))
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"tools ({len(names)}): {names}")

            async def call(name: str, args: dict):
                r = await session.call_tool(name, args)
                txt = "".join(c.text for c in r.content if getattr(c, "text", None))
                return txt

            out = await call("comfyui_list_loras", {"query": "Bronya"})
            print("\ncomfyui_list_loras(query=Bronya) ->", out[:400])

            out = await call("comfyui_list_characters", {})
            print("\ncomfyui_list_characters ->", out[:400])

            out = await call("media_status", {})
            print("\nmedia_status ->", out[:400])

            # 真实提交一个带角色的异步任务，确认 MCP 工具也能注入 LoRA
            out = await call("comfyui_task_submit", {
                "character": "bronya",
                "prompt": "在雪地里看雪，微笑着",
            })
            print("\ncomfyui_task_submit ->", out[:500])
            try:
                tid = json.loads(out).get("task_id")
            except Exception:
                tid = None
            if tid:
                for _ in range(60):
                    st = await call("comfyui_task_status", {"task_id": tid})
                    try:
                        d = json.loads(st)
                    except Exception:
                        d = {}
                    obj = d.get("task") or d
                    if obj.get("status") in ("completed", "failed", "cancelled",
                                             "submission_unknown"):
                        print("task final:", json.dumps(obj, ensure_ascii=False)[:500])
                        break
                    await asyncio.sleep(2)
    return 0


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899/mcp"
    sys.exit(asyncio.run(main(url)))
