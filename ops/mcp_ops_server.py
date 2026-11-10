"""Möbius 只读运维 MCP 服务（供 DSH / 其他 agent 查询）。

设计原则：**只读**。不提供任何写操作、不执行命令、不接受任意 SQL。
- 状态：端口/容器/QQ/PG/Redis/备份新鲜度
- 任务：Gateway PG 的 task_snapshot
- 探针：ops/probe/state.db 的消息过程树（SQLite，本地只读）
- 日志：白名单文件的 tail

启动：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe ops\\mcp_ops_server.py --port 8901

MCP 端点：http://127.0.0.1:8901/mcp
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sqlite3
import time
from pathlib import Path
from typing import Any

import psycopg
import uvicorn
from mcp.server.mcpserver import MCPServer

BASE = Path(__file__).resolve().parent.parent          # mobius/
MOBIUS = BASE
QQBOT = BASE.parent / "qqbot"
PROBE_DB = MOBIUS / "ops" / "probe" / "state.db"
BACKUP_DIR = Path(r"D:\AI\mobius\pgbackup")

LOG_ALLOWLIST = {
    "gateway": MOBIUS / "media_gateway" / "logs" / "gateway.log",
    "comfyui": MOBIUS / "logs" / "comfyui.log",
    "core": MOBIUS / "logs" / "core.out.log",
}

_APP = None


def _pg_password() -> str:
    env_file = Path(r"D:\AI\mobius\.env")
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("PG_PASSWORD="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return os.environ.get("PG_PASSWORD", "")


def _pg(db: str):
    return psycopg.connect(
        f"postgresql://postgres:{_pg_password()}@127.0.0.1:55432/{db}",
        connect_timeout=4,
        row_factory=psycopg.rows.dict_row,
    )


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except OSError:
        return False


def _docker_running(name: str) -> str:
    import subprocess

    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}", name],
            capture_output=True, text=True, timeout=8,
        )
        return (r.stdout or "").strip() or "unknown"
    except Exception as exc:  # noqa: BLE001
        return f"error: {type(exc).__name__}"


mcp = MCPServer("mobius-ops", instructions=(
    "Möbius 只读运维查询服务。所有工具都不修改任何状态，仅供诊断。"
    "服务端口：Core 8900 / Gateway 8899 / LangBot 5300 / ComfyUI 8188 / OneBot 13000。"
))


@mcp.tool(name="mobius_status", description="汇总系统状态：端口、容器、QQ、PG/Redis、最近 PG 备份。")
def mobius_status() -> str:
    ports = {p: _port_open(p) for p in (8900, 8899, 5300, 8188, 13000, 5401)}
    containers = {n: _docker_running(n) for n in ("mobius-pg", "mobius-redis", "langbot", "napcat")}
    qq: Any = None
    token = ""
    env_file = Path(r"D:\AI\mobius\.env")
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("NAPCAT_HTTP_TOKEN="):
                token = line.split("=", 1)[1].strip()
    except OSError:
        pass
    try:
        import urllib.request

        req = urllib.request.Request(
            "http://127.0.0.1:13000/get_status",
            headers={"Authorization": f"Bearer {token}"} if token else {},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode())
        qq = (data.get("data") or {}).get("online")
    except Exception as exc:  # noqa: BLE001
        qq = f"error: {type(exc).__name__}"

    backups = sorted(BACKUP_DIR.glob("*.dump")) if BACKUP_DIR.exists() else []
    newest = None
    if backups:
        f = max(backups, key=lambda p: p.stat().st_mtime)
        newest = {
            "file": f.name,
            "age_sec": round(time.time() - f.stat().st_mtime),
            "size_kb": round(f.stat().st_size / 1024, 1),
        }
    return json.dumps({
        "ports": ports,
        "containers": containers,
        "qq_online": qq,
        "pg_backup_latest": newest,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }, ensure_ascii=False)


@mcp.tool(name="task_list", description="列出 Gateway 任务（读 mobius 库 task_snapshot），最多 50 条。")
def task_list(limit: int = 20, status: str = "") -> str:
    limit = max(1, min(int(limit or 20), 50))
    sql = ("SELECT task_id, kind, status, attempts, created_at, finished_at, error_code,"
           " substr(result_json, 1, 120) AS result_preview FROM task_snapshot")
    args: tuple = ()
    if status:
        sql += " WHERE status=%s"
        args = (status,)
    sql += " ORDER BY created_at DESC LIMIT %s"
    args = args + (limit,)
    with _pg("mobius") as con:
        rows = con.execute(sql, args).fetchall()
    return json.dumps(rows, ensure_ascii=False, default=str)


@mcp.tool(name="task_detail", description="单个任务详情（含结果/错误）。")
def task_detail(task_id: str) -> str:
    with _pg("mobius") as con:
        row = con.execute("SELECT * FROM task_snapshot WHERE task_id=%s", (task_id,)).fetchone()
    if row is None:
        return json.dumps({"ok": False, "error": "unknown_task", "task_id": task_id})
    for k in ("created_at", "started_at", "finished_at", "updated_at"):
        if row.get(k):
            row[k] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(row[k])))
    return json.dumps(row, ensure_ascii=False, default=str)


@mcp.tool(name="message_state", description="探针记录的消息过程树（近 N 条或指定 msg_id）。")
def message_state(msg_id: str = "", limit: int = 5) -> str:
    limit = max(1, min(int(limit or 5), 20))
    if not PROBE_DB.exists():
        return json.dumps({"ok": False, "error": "probe_db_missing", "path": str(PROBE_DB)})
    con = sqlite3.connect(f"file:{PROBE_DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        if msg_id:
            rows = con.execute(
                "SELECT * FROM message_state WHERE msg_id LIKE ? OR run_id=? OR task_id=? LIMIT 5",
                (f"{msg_id}%", msg_id, msg_id),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM message_state ORDER BY phase_epoch DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            nodes = json.loads(r["nodes_json"] or "[]")
            out.append({
                "msg_id": r["msg_id"], "session_id": r["session_id"], "run_id": r["run_id"],
                "task_id": r["task_id"], "phase": r["phase"], "phase_ts": r["phase_ts"],
                "user_text": (r["user_text"] or "")[:80],
                "nodes": [
                    {"phase": n.get("phase"), "ts": n.get("ts"),
                     "detail": str(n.get("detail"))[:120]} for n in nodes[-8:]
                ],
            })
    finally:
        con.close()
    return json.dumps(out, ensure_ascii=False)


@mcp.tool(name="log_tail", description="查看白名单日志的尾部（gateway/comfyui/core）。")
def log_tail(name: str = "gateway", lines: int = 40) -> str:
    lines = max(1, min(int(lines or 40), 200))
    path = LOG_ALLOWLIST.get(name)
    if path is None:
        return json.dumps({"ok": False, "error": "log_not_allowed", "allowed": sorted(LOG_ALLOWLIST)})
    if not path.exists():
        return json.dumps({"ok": False, "error": "log_missing", "path": str(path)})
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - 200_000))
        data = fh.read().decode("utf-8", "replace")
    return json.dumps({
        "ok": True, "path": str(path), "lines": lines,
        "tail": data.splitlines()[-lines:],
    }, ensure_ascii=False)


@mcp.tool(name="pg_health", description="PG 三个库的连接数、表数与最近写入时间（只读）。")
def pg_health() -> str:
    out: dict[str, Any] = {}
    for db in ("langbot", "mobius", "mobius_core"):
        try:
            with _pg(db) as con:
                n = con.execute("SELECT COUNT(*) AS n FROM pg_stat_activity WHERE datname=%s", (db,)).fetchone()["n"]
                t = con.execute(
                    "SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema='public'"
                ).fetchone()["n"]
            out[db] = {"connections": n, "tables": t}
        except Exception as exc:  # noqa: BLE001
            out[db] = {"error": f"{type(exc).__name__}: {exc}"}
    return json.dumps(out, ensure_ascii=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8901)
    args = ap.parse_args()
    app = mcp.streamable_http_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
