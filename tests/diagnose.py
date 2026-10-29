"""一键诊断「机器人为什么不回消息」。

沿消息链路逐段检查，直接告诉你断在哪一段：

    QQ 用户 → NapCat → (WS) → LangBot → (MCP) → Media Gateway → ComfyUI/TTS
                                    └→ LLM

用法：
    D:\\Anaconda3\\envs\\open-webui\\python.exe tests\\diagnose.py
    ... --verbose
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB = Path(r"C:\Users\lenovo\Desktop\_说明文档\qqbot\langbot\data\langbot.db")

ROWS: list[tuple[str, bool, str]] = []


def row(name: str, ok: bool, detail: str = "") -> None:
    ROWS.append((name, ok, detail))
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""), flush=True)


def get(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.parse_args()

    # 1. NapCat
    try:
        d = get("http://127.0.0.1:13000/get_status", 6)["data"]
        row("NapCat 在线", bool(d.get("online")), f"online={d.get('online')}")
    except Exception as e:
        row("NapCat 在线", False, f"13000 不可达: {e}")

    # 2. LangBot
    try:
        with urllib.request.urlopen("http://127.0.0.1:5300/healthz", timeout=5) as r:
            row("LangBot 健康", r.status == 200, f"HTTP {r.status}")
    except Exception as e:
        row("LangBot 健康", False, str(e))

    # 3. Core
    try:
        st = get("http://127.0.0.1:8900/api/status", 15)
        row("Möbius Core", bool(st["core"]["ok"]),
            f"媒体模式={st['media']['mode']}")
    except Exception as e:
        row("Möbius Core", False, str(e))

    # 4. Media Gateway
    try:
        p = get("http://127.0.0.1:8899/api/pulse", 5)
        row("Media Gateway", bool(p.get("ok")), f"uptime={int(p.get('uptime_sec', 0))}s")
    except Exception as e:
        row("Media Gateway", False, str(e))

    # 5. LangBot 的 MCP 是否成功初始化（本轮故障的主因）
    try:
        started = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.StartedAt}}", "langbot"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        logs = subprocess.run(["docker", "logs", "langbot", "--since", started],
                              capture_output=True, text=True, timeout=25)
        out = (logs.stdout or "") + (logs.stderr or "")
        failed = "Failed to start MCP server" in out
        row("LangBot↔MCP 工具集", not failed,
            "日志里有 MCP 初始化失败，生图/语音工具不可用" if failed else "已连上")
    except Exception as e:
        row("LangBot↔MCP 工具集", False, str(e))

    # 6. 入站消息是否还在流动（近 15 分钟）
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        n = con.execute(
            "select count(*) from monitoring_messages where timestamp > datetime('now','-15 minutes')"
        ).fetchone()[0]
        last = con.execute(
            "select timestamp,status from monitoring_messages order by rowid desc limit 1"
        ).fetchone()
        con.close()
        ok = n > 0
        row("最近 15 分钟入站消息", ok,
            f"{n} 条" + (f"，最后一条 {last[0]} ({last[1]})" if last else ""))
        if not ok:
            print("      → 没消息进来：检查 NapCat 是否登录、机器人是否在群里/被 @")
    except Exception as e:
        row("最近 15 分钟入站消息", False, str(e))

    # 7. 最近失败的运行（只看最近 20 分钟，避免把修好之前的历史脏数据当成现症）
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        rows = con.execute(
            "select created_at,status,status_reason from agent_run "
            "where created_at > datetime('now','-20 minutes') order by id desc limit 5"
        ).fetchall()
        con.close()
        bad = [r for r in rows if r[1] != "completed"]
        if not rows:
            row("最近 20 分钟运行", True, "无运行记录（这段时间没人触发回复）")
        else:
            row("最近 20 分钟运行", not bad,
                "；".join(f"{r[0][:19]} {r[1]} {(r[2] or '')[:40]}" for r in rows))
    except Exception as e:
        row("最近 20 分钟运行", False, str(e))

    fails = [r for r in ROWS if not r[1]]
    print("\n" + "=" * 66)
    if not fails:
        print("链路健康。如果 QQ 仍不回，问题在「这条消息没触发回复规则」（如群里没 @ 机器人）。")
        return 0
    print(f"发现 {len(fails)} 处异常，优先看第一处：")
    for name, _, detail in fails:
        print(f"  - {name}: {detail}")
    print("=" * 66)
    return 1


if __name__ == "__main__":
    sys.exit(main())
