"""通过 LangBot 的 WebChat 调试通道跑一次完整流水线。

这不是模拟：它和 QQ 消息进入的是同一条 pipeline（同一人格 prompt、同一 LLM、
同一组 MCP 工具），只是入口换成了 WebSocket。用来在没有第二台 QQ 账号的情况下
验证「消息进来 → 角色回复（含工具调用）」。

用法：
    D:\\Anaconda3\\envs\\open-webui\\python.exe tests\\probe_langbot_chat.py
    ... --text "用布洛妮娅的 LoRA 画一张雪地图" --seconds 180
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

import websockets

PIPELINE = "2e939868-ac11-42c9-aa13-ad96208edb18"
WORKSPACE = "d55a23c6-eae9-4e84-b1e8-06bad3eab716"
# 不写死密钥：从环境变量取（本机运行前 set LANGBOT_API_KEY=lbk_xxx）
API_KEY = os.environ.get("LANGBOT_API_KEY", "")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="ws://127.0.0.1:5300")
    ap.add_argument("--text", default="用一句话说说你现在是谁")
    ap.add_argument("--seconds", type=float, default=60)
    args = ap.parse_args()

    url = f"{args.base}/api/v1/pipelines/{PIPELINE}/ws/connect?session_type=person"
    print(f"连接 {url}")
    text_seen: list[str] = []
    headers = {
        "Origin": "http://127.0.0.1:5300",   # 少了这个 LangBot/hypercorn 会直接 400
        "User-Agent": "mobius-probe/1.0",
        "X-Request-Id": "mobius-probe",
    }
    try:
        async with websockets.connect(url, additional_headers=headers,
                                      max_size=8 * 1024 * 1024,
                                      open_timeout=10) as ws:
            await ws.send(json.dumps({
                "type": "authenticate",
                "token": API_KEY,
                "workspace_uuid": WORKSPACE,
            }))
            first = await asyncio.wait_for(ws.recv(), timeout=15)
            print("auth ->", first[:300])
            if '"connected"' not in first:
                print("认证失败（token 不是 user token？）")
                return 2

            await ws.send(json.dumps({
                "type": "message",
                "stream": True,
                "message": [{"type": "Plain", "text": args.text}],
            }))
            print(f"已发送：{args.text}\n--- 流式回复 ---")

            async def collect():
                while True:
                    raw = await ws.recv()
                    try:
                        d = json.loads(raw)
                    except Exception:
                        continue
                    t = d.get("type")
                    if t == "message":
                        data = d.get("data") or {}
                        c = data.get("content") or ""
                        if c:
                            text_seen.append(c)
                            print(c, end="", flush=True)
                        if data.get("is_final"):
                            print("\n--- 回复结束 ---")
                            return
                    elif t == "error":
                        print("错误帧：", raw[:400])
                        return

            try:
                await asyncio.wait_for(collect(), timeout=args.seconds)
            except asyncio.TimeoutError:
                print(f"\n（{args.seconds:.0f}s 内未收到结束标记，已收到 {len(text_seen)} 段）")
    except Exception as e:
        print(f"连接失败: {type(e).__name__}: {e}")
        return 1
    print(f"\n合计收到 {len(''.join(text_seen))} 字")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
