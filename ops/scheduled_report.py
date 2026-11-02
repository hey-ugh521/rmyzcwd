"""Möbius 定时巡检报告（D3）。

模式：
  daily   —— 每日健康报告（端口/容器/QQ/PG/备份/任务/探针）
  weekly  —— 每周审计（健康 + 仓库状态 + 探针统计 + 最近 incident）
  drill   —— 月度恢复演练（调用 ops/pg_backup.py --drill）

用法：
  python ops/scheduled_report.py --mode daily [--with-dsh]

输出：docs/reports/<mode>-YYYYMMDD-HHMMSS.md
退出码：0 成功（含降级），1 失败。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
REPORT_DIR = BASE / "docs" / "reports"
OPS_MCP = "http://127.0.0.1:8901/mcp"
PG_BACKUP = BASE / "ops" / "pg_backup.py"
PYTHON = r"D:\AI\qqbot-mcp\Scripts\python.exe"


def _port(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def _mcp_tools() -> dict | None:
    """同步拿 MCP 工具结果（用 asyncio 包一层）。"""

    async def run():
        try:
            from mcp import ClientSession
        except ImportError:
            return None
        try:
            from mcp.client.streamable_http import streamablehttp_client as connect
        except ImportError:
            from mcp.client.streamable_http import streamable_http_client as connect

        out: dict = {}
        try:
            async with connect(OPS_MCP) as c:
                async with ClientSession(c[0], c[1]) as s:
                    await s.initialize()

                    async def safe(name: str, **kw):
                        try:
                            r = await s.call_tool(name, kw)
                            text = "".join(getattr(b, "text", "") for b in (r.content or []))
                            return json.loads(text) if text.strip().startswith(("{", "[")) else text
                        except Exception as exc:  # noqa: BLE001
                            return {"_error": f"{type(exc).__name__}: {exc}"}

                    out["status"] = await safe("mobius_status")
                    out["pg"] = await safe("pg_health")
                    out["tasks"] = await safe("task_list", limit=10)
                    out["messages"] = await safe("message_state", limit=5)
                    out["watchdog"] = await safe("log_tail", name="watchdog", lines=30)
        except Exception as exc:  # noqa: BLE001
            out["_error"] = f"{type(exc).__name__}: {exc}"
        return out

    try:
        return asyncio.run(run())
    except Exception:  # noqa: BLE001
        return None


def _fallback_status() -> dict:
    return {
        "ports": {p: _port(p) for p in (8900, 8899, 5300, 8188, 13000, 8901)},
        "containers": {
            n: subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", n],
                              capture_output=True, text=True, timeout=10).stdout.strip()
            for n in ("mobius-pg", "mobius-redis", "langbot", "napcat")
        },
        "note": "MCP 不可用，使用降级采集",
    }


def _repo_status() -> dict:
    git = r"D:\AI\Git\cmd\git.exe"
    try:
        r = subprocess.run([git, "-C", str(BASE), "status", "--short"],
                           capture_output=True, text=True, timeout=15)
        changes = [l for l in (r.stdout or "").splitlines() if l.strip()]
        log = subprocess.run([git, "-C", str(BASE), "log", "--oneline", "-5"],
                             capture_output=True, text=True, timeout=15)
        return {"dirty": len(changes), "changes": changes[:10],
                "recent_commits": (log.stdout or "").strip().splitlines()}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def _incidents() -> list[str]:
    d = BASE / "logs" / "incidents"
    if not d.exists():
        return []
    return sorted(p.name for p in d.glob("incident-*.md"))[-5:]


def _dsh_narrative(prompt: str, timeout: int = 420) -> str:
    node = r"D:\Program Files\nodejs\node.exe"
    binjs = r"D:\AI\tools\npm-global\node_modules\@deepseek-ai\dsh\lib\bin.js"
    patch = BASE / "ops" / "dsh" / "mobius-ops.yml"
    if not (os.path.exists(node) and os.path.exists(binjs) and patch.exists()):
        return "（DSH 不可用，跳过叙述部分）"
    try:
        r = subprocess.run(
            [node, binjs, "--profile", "headless", "--patch", str(patch), prompt],
            capture_output=True, text=True, timeout=timeout, cwd=str(BASE),
            encoding="utf-8", errors="replace",
        )
        return (r.stdout or "").strip() or "（DSH 无输出）"
    except Exception as exc:  # noqa: BLE001
        return f"（DSH 调用失败：{type(exc).__name__}）"


def build_report(mode: str, with_dsh: bool) -> str:
    now = time.strftime("%Y-%m-%d %H:%M:%S %z")
    data = _mcp_tools()
    lines: list[str] = [f"# Möbius {mode} 报告", "", f"- 生成时间：{now}",
                        f"- 主机：{socket.gethostname()}", ""]

    if data and isinstance(data.get("status"), dict):
        lines += ["## 系统状态（来源：ops MCP）", "", "```json",
                  json.dumps(data.get("status"), ensure_ascii=False, indent=1), "```", ""]
        lines += ["## PG 健康", "", "```json",
                  json.dumps(data.get("pg"), ensure_ascii=False, indent=1), "```", ""]
        lines += ["## 最近任务", "", "```json",
                  json.dumps(data.get("tasks"), ensure_ascii=False, indent=1)[:3000], "```", ""]
        msgs = data.get("messages") or []
        lines += ["## 最近消息轨迹", f"- 条数：{len(msgs)}"]
        for m in msgs[:3]:
            lines.append(f"  - {m.get('msg_id')} phase={m.get('phase')} ts={m.get('phase_ts')} nodes={len(m.get('nodes') or [])}")
        lines += ["", "## watchdog 最近日志", "```",
                  "\n".join((data.get("watchdog") or {}).get("tail", [])[-15:]), "```", ""]
    else:
        fb = _fallback_status()
        lines += ["## 系统状态（降级采集：MCP 不可用）", "", "```json",
                  json.dumps(fb, ensure_ascii=False, indent=1), "```", ""]
        if data and data.get("_error"):
            lines += [f"> MCP 错误：{data['_error']}", ""]

    lines += ["## 仓库", "```json", json.dumps(_repo_status(), ensure_ascii=False, indent=1)[:2000], "```", ""]
    inc = _incidents()
    inc_lines = [f"- {n}" for n in inc] or ["- 无"]
    lines += ["## 最近 incident", *inc_lines, ""]

    if mode == "drill":
        lines += ["## 恢复演练", ""]
        try:
            r = subprocess.run([PYTHON, str(PG_BACKUP), "--drill"], capture_output=True,
                               text=True, timeout=900, cwd=str(BASE))
            out = (r.stdout or "") + (r.stderr or "")
            lines += ["```", out[-4000:], "```", ""]
        except Exception as exc:  # noqa: BLE001
            lines += [f"> 演练失败：{type(exc).__name__}: {exc}", ""]

    if with_dsh:
        lines += ["## DSH 叙述", "", _dsh_narrative(
            "你是 Möbius 的只读运维分析 agent。请用 mobius MCP 工具读取当前状态，"
            "生成简洁的巡检结论：1) 是否异常及证据 2) 与昨日相比值得注意的变化 "
            "3) 建议动作（仅供人工确认）。不要执行任何写操作。"), ""]

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["daily", "weekly", "drill"], default="daily")
    ap.add_argument("--with-dsh", action="store_true")
    ap.add_argument("--print", action="store_true", dest="to_print")
    args = ap.parse_args()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        body = build_report(args.mode, args.with_dsh)
    except Exception as exc:  # noqa: BLE001
        print(f"报告生成失败：{type(exc).__name__}: {exc}")
        return 1
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = REPORT_DIR / f"{args.mode}-{stamp}.md"
    path.write_text(body, encoding="utf-8")
    if args.to_print:
        print(body)
    print(f"报告已写入：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
