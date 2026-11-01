"""Möbius 信息探针（P1，只读）。

目标
----
把「一条用户消息从收到到回复/中断」的全过程记成可查询的状态轨迹，
每个节点都带**日志同源时间戳**（本地时间 + epoch），便于建立过程树排错。

设计约束
--------
- 全程只读：三处数据源都用 `mode=ro` + busy_timeout，绝不写生产库。
- 旁路：探针挂了不影响主链路。
- 时间戳统一为本地时区（Asia/Shanghai）+08:00，同时给出 epoch 便于排序。

数据源
------
  LangBot  monitoring_messages / agent_run / monitoring_tool_calls
  Gateway  tasks.db: task_snapshot
  Watchdog watchdog.run.log（重启/延迟等运维动作）

用法
----
  python ops/probe/probe_state.py --once            # 采集一轮并入库
  python ops/probe/probe_state.py --recent 10       # 最近 10 条消息轨迹
  python ops/probe/probe_state.py --tree user:<id>  # 某条消息的过程树
  python ops/probe/probe_state.py --tree run:40     # 某个 run 的过程树
  python ops/probe/probe_state.py --tree task:g_xxx # 某个任务的过程树
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time

TZ = dt.timezone(dt.timedelta(hours=8))
MOBIUS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # .../mobius
QQBOT = os.path.join(os.path.dirname(MOBIUS), "qqbot")                                  # .../qqbot

LANGBOT_DB = os.path.join(QQBOT, "langbot", "data", "langbot.db")
GATEWAY_DB = os.path.join(MOBIUS, "media_gateway", "data", "tasks.db")
WATCHDOG_LOG = os.path.join(QQBOT, "watchdog.run.log")
STATE_DB = os.path.join(MOBIUS, "ops", "probe", "state.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS message_state (
  msg_id      TEXT PRIMARY KEY,
  session_id  TEXT,
  role        TEXT,
  user_text   TEXT,
  run_id      TEXT,
  task_id     TEXT,
  phase       TEXT,
  phase_ts    TEXT,
  phase_epoch REAL,
  nodes_json  TEXT,
  updated_at  REAL
);
CREATE INDEX IF NOT EXISTS ix_msg_run ON message_state(run_id);
CREATE INDEX IF NOT EXISTS ix_msg_task ON message_state(task_id);

CREATE TABLE IF NOT EXISTS maintenance_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT,
  epoch       REAL,
  action      TEXT,
  detail      TEXT
);
"""

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LOG_TS_RE = re.compile(r"\[(?:(\d{2}-\d{2}) )?(\d{2}:\d{2}:\d{2})(?:\.\d+)?\]")


# --------------------------------------------------------------------------- 时间
def utc_text_to_local(text: str) -> tuple[str, float] | tuple[None, None]:
    """LangBot 库里的时间戳是 UTC naive 文本，转成本地 +08:00 与 epoch。"""
    if not text:
        return None, None
    raw = str(text).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = dt.datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    else:
        return None, None
    aware = naive.replace(tzinfo=dt.timezone.utc).astimezone(TZ)
    return aware.isoformat(timespec="seconds"), aware.timestamp()


def epoch_to_local(ts: float | None) -> str | None:
    if not ts:
        return None
    return dt.datetime.fromtimestamp(float(ts), TZ).isoformat(timespec="seconds")


def local_log_epoch(stamp: str) -> float | None:
    """watchdog 日志里的 `MM-DD HH:MM:SS` 是本地时间。"""
    try:
        full = stamp if "-" in stamp else f"{dt.datetime.now().strftime('%m-%d')} {stamp}"
        full = f"{dt.datetime.now().year}-{full}"
        naive = dt.datetime.strptime(full, "%Y-%m-%d %H:%M:%S")
        return naive.replace(tzinfo=TZ).timestamp()
    except ValueError:
        return None


# --------------------------------------------------------------------------- 读取
def connect_ro(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    return con



# --------------------------------------------------------------------------- 数据源（PostgreSQL，阶段 4）
# LangBot / Gateway 均已迁移到 PG；探针直接读 PG（双库），不再读旧 SQLite。
PG_HOST = os.environ.get("MOBIUS_PG_HOST", "127.0.0.1")
PG_PORT = int(os.environ.get("MOBIUS_PG_PORT", "55432"))
PG_USER = os.environ.get("MOBIUS_PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "")


def _load_env_file() -> None:
    """从 D:/AI/mobius/.env 读取 PG_PASSWORD（watchdog 直接调用时无需注入环境）。"""
    global PG_PASSWORD
    env_path = r"D:/AI/mobius/.env"
    if PG_PASSWORD or not os.path.exists(env_path):
        return
    try:
        for line in open(env_path, encoding="utf-8"):
            if line.startswith("PG_PASSWORD="):
                PG_PASSWORD = line.split("=", 1)[1].strip()
                return
    except Exception:  # noqa: BLE001
        pass


_load_env_file()
LANGBOT_DBNAME = "langbot"
GATEWAY_DBNAME = "mobius"


def _pg_connect(dbname: str):
    import psycopg
    from psycopg.rows import dict_row

    dsn = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{dbname}"
    return psycopg.connect(dsn, row_factory=dict_row)


def _ts(value):
    """PG 返回值可能是 datetime 或 str（naive UTC），统一转本地时间 + epoch。"""
    if value is None:
        return None, None
    if isinstance(value, str):
        return utc_text_to_local(value)
    aware = value.replace(tzinfo=dt.timezone.utc).astimezone(TZ)
    return aware.isoformat(timespec="seconds"), aware.timestamp()


def load_runs(window_hours: float = 12) -> list[dict]:
    cutoff = time.time() - window_hours * 3600
    with _pg_connect(LANGBOT_DBNAME) as con:
        rows = con.execute(
            "SELECT id, run_id, conversation_id, status, created_at, started_at, finished_at,"
            " status_reason FROM agent_run ORDER BY id DESC LIMIT 200"
        ).fetchall()
    out = []
    for r in rows:
        created_iso, created_epoch = _ts(r["created_at"])
        if created_epoch is None or created_epoch < cutoff:
            continue
        finished_iso, finished_epoch = _ts(r["finished_at"])
        started_iso, started_epoch = _ts(r["started_at"])
        out.append({
            "run_id": r["run_id"], "run_seq": str(r["id"]), "session_id": r["conversation_id"],
            "status": r["status"], "started": created_iso, "started_epoch": created_epoch,
            "started_actual": started_iso, "started_actual_epoch": started_epoch,
            "finished": finished_iso, "finished_epoch": finished_epoch,
            "reason": r["status_reason"],
        })
    return out


def load_messages(window_hours: float = 12, limit: int = 200) -> list[dict]:
    cutoff = time.time() - window_hours * 3600
    with _pg_connect(LANGBOT_DBNAME) as con:
        rows = con.execute(
            "SELECT id, timestamp, session_id, role, message_content, run_id, parent_message_id"
            " FROM monitoring_messages ORDER BY timestamp DESC LIMIT %s", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        iso, ep = _ts(r["timestamp"])
        if ep is None or ep < cutoff:
            continue
        out.append({"msg_id": r["id"], "ts": iso, "epoch": ep, "session_id": r["session_id"],
                    "role": r["role"], "content": r["message_content"] or "",
                    "run_id": r["run_id"], "parent": r["parent_message_id"]})
    return out


def load_tool_calls(window_hours: float = 12) -> list[dict]:
    cutoff = time.time() - window_hours * 3600
    with _pg_connect(LANGBOT_DBNAME) as con:
        rows = con.execute(
            "SELECT timestamp, tool_name, status, duration, session_id, message_id, arguments, result"
            " FROM monitoring_tool_calls ORDER BY timestamp DESC LIMIT 500"
        ).fetchall()
    out = []
    for r in rows:
        iso, ep = _ts(r["timestamp"])
        if ep is None or ep < cutoff:
            continue
        out.append({"ts": iso, "epoch": ep, "tool": r["tool_name"], "status": r["status"],
                    "duration_ms": r["duration"], "session_id": r["session_id"],
                    "message_id": r["message_id"], "arguments": r["arguments"] or "",
                    "result": r["result"] or ""})
    return out


def load_tasks(window_hours: float = 12) -> list[dict]:
    cutoff = time.time() - window_hours * 3600
    with _pg_connect(GATEWAY_DBNAME) as con:
        rows = con.execute(
            "SELECT task_id, kind, status, created_at, started_at, finished_at, result_json,"
            " error_code, error_message, attempts, idempotency_key"
            " FROM task_snapshot ORDER BY created_at DESC LIMIT 200"
        ).fetchall()
    out = []
    for r in rows:
        created = float(r["created_at"] or 0)
        if created < cutoff:
            continue
        result = {}
        try:
            result = json.loads(r["result_json"] or "{}")
        except Exception:  # noqa: BLE001
            pass
        out.append({
            "task_id": r["task_id"], "kind": r["kind"], "status": r["status"],
            "created": epoch_to_local(created), "created_epoch": created,
            "started": epoch_to_local(r["started_at"]), "started_epoch": r["started_at"],
            "finished": epoch_to_local(r["finished_at"]), "finished_epoch": r["finished_at"],
            "result": result, "error_code": r["error_code"], "error_message": r["error_message"],
            "attempts": r["attempts"], "idempotency_key": r["idempotency_key"],
        })
    return out


def load_maintenance(limit: int = 300) -> list[dict]:
    if not os.path.exists(WATCHDOG_LOG):
        return []
    out = []
    with open(WATCHDOG_LOG, encoding="utf-8", errors="replace") as fh:
        for line in fh.read().splitlines()[-limit:]:
            clean = ANSI_RE.sub("", line)
            m = LOG_TS_RE.search(clean)
            if not m:
                continue
            stamp = f"{m.group(1)} {m.group(2)}" if m.group(1) else m.group(2)
            ep = local_log_epoch(stamp)
            if ep is None:
                continue
            body = clean[m.end():].strip()
            if any(k in body for k in ("重启", "defer", "延迟")) and "备份" not in body:
                out.append({"ts": epoch_to_local(ep), "epoch": ep, "detail": body[:160]})
    return out


def extract_task_id(tool: dict) -> str | None:
    for blob in (tool.get("result") or "", tool.get("arguments") or ""):
        m = re.search(r'"(g_[0-9a-f]{6,}|v_[0-9a-f]{6,})"', blob)
        if m:
            return m.group(1)
    return None


def build_message_trees(window_hours: float = 12) -> list[dict]:
    messages = load_messages(window_hours)
    runs = {r["run_id"]: r for r in load_runs(window_hours)}
    tools = load_tool_calls(window_hours)
    tasks = {t["task_id"]: t for t in load_tasks(window_hours)}
    maint = load_maintenance()

    # 助手消息按 parent 归到用户消息
    replies_by_parent: dict[str, list[dict]] = {}
    for m in messages:
        if m["role"] == "assistant" and m["parent"]:
            replies_by_parent.setdefault(m["parent"], []).append(m)
        elif m["role"] == "assistant" and m["run_id"]:
            replies_by_parent.setdefault(f"run:{m['run_id']}", []).append(m)

    trees = []
    for m in messages:
        if m["role"] != "user":
            continue
        nodes: list[dict] = [{"phase": "received", "ts": m["ts"], "epoch": m["epoch"],
                              "detail": {"session_id": m["session_id"]}}]
        run = runs.get(str(m["run_id"])) if m["run_id"] else None
        if run:
            nodes.append({"phase": "run_started", "ts": run["started"], "epoch": run["started_epoch"],
                          "detail": {"run_id": run["run_id"], "status": run["status"]}})
            # 工具调用：同一 session、落在 run 时间窗内
            run_start = run.get("started_actual_epoch") or run["started_epoch"]
            end = run["finished_epoch"] or (run_start + 3600)
            related = [t for t in tools
                       if t["session_id"] == run["session_id"]
                       and run_start - 5 <= t["epoch"] <= end + 5]
            task_id = None
            for t in sorted(related, key=lambda x: x["epoch"]):
                nodes.append({"phase": "tool", "ts": t["ts"], "epoch": t["epoch"],
                              "detail": {"tool": t["tool"], "status": t["status"],
                                         "duration_ms": t["duration_ms"]}})
                task_id = extract_task_id(t) or task_id
            if task_id and task_id in tasks:
                task = tasks[task_id]
                nodes.append({"phase": "task_created", "ts": task["created"], "epoch": task["created_epoch"],
                              "detail": {"task_id": task_id, "status": task["status"]}})
                if task["started"]:
                    nodes.append({"phase": "task_running", "ts": task["started"], "epoch": task["started_epoch"],
                                  "detail": {"task_id": task_id}})
                if task["finished"]:
                    nodes.append({"phase": f"task_{task['status']}", "ts": task["finished"],
                                  "epoch": task["finished_epoch"],
                                  "detail": {"task_id": task_id,
                                             "filename": (task["result"] or {}).get("filename"),
                                             "error_code": task["error_code"]}})
            if run["finished"]:
                nodes.append({"phase": f"run_{run['status']}", "ts": run["finished"],
                              "epoch": run["finished_epoch"],
                              "detail": {"run_id": run["run_id"], "reason": (run["reason"] or "")[:160]}})
                # 中断时找最近的维护动作
                if run["status"] in ("cancelled", "failed"):
                    near = [x for x in maint if abs(x["epoch"] - run["finished_epoch"]) <= 180]
                    for x in near[:3]:
                        nodes.append({"phase": "maintenance", "ts": x["ts"], "epoch": x["epoch"],
                                      "detail": {"detail": x["detail"]}})
            nodes.append({"phase": "run_end", "ts": run["finished"], "epoch": run["finished_epoch"],
                          "detail": {}})
        for reply in replies_by_parent.get(m["msg_id"], []):
            nodes.append({"phase": "reply_sent", "ts": reply["ts"], "epoch": reply["epoch"],
                          "detail": {"role": "assistant"}})
        nodes.sort(key=lambda n: n["epoch"] or 0)
        last = nodes[-1] if nodes else {}
        text = m["content"]
        trees.append({
            "msg_id": m["msg_id"],
            "session_id": m["session_id"],
            "run_id": m["run_id"],
            "task_id": next((n["detail"].get("task_id") for n in nodes
                             if n["phase"] == "task_created"), None),
            "phase": last.get("phase"),
            "phase_ts": last.get("ts"),
            "phase_epoch": last.get("epoch"),
            "user_text": extract_text(text)[:80],
            "nodes": nodes,
        })
    return trees


def extract_text(content_json: str) -> str:
    try:
        parts = json.loads(content_json)
    except Exception:  # noqa: BLE001
        return content_json
    buf = []
    for p in parts if isinstance(parts, list) else []:
        if isinstance(p, dict) and p.get("type") == "Plain":
            buf.append(p.get("text") or "")
    return " ".join(buf) or content_json


# --------------------------------------------------------------------------- 存储
def save(trees: list[dict]) -> None:
    os.makedirs(os.path.dirname(STATE_DB), exist_ok=True)
    con = sqlite3.connect(STATE_DB, timeout=30)
    con.executescript(SCHEMA)
    now = time.time()
    for t in trees:
        con.execute(
            "INSERT OR REPLACE INTO message_state(msg_id,session_id,role,user_text,run_id,task_id,"
            "phase,phase_ts,phase_epoch,nodes_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (t["msg_id"], t["session_id"], "user", t["user_text"], t["run_id"], t["task_id"],
             t["phase"], t["phase_ts"], t["phase_epoch"],
             json.dumps(t["nodes"], ensure_ascii=False), now),
        )
    for m in load_maintenance():
        con.execute(
            "INSERT INTO maintenance_log(ts,epoch,action,detail) VALUES(?,?,?,?)"
            " ON CONFLICT DO NOTHING",
            (m["ts"], m["epoch"], "watchdog", m["detail"]),
        )
    con.commit()
    con.close()


# --------------------------------------------------------------------------- 输出
def print_tree(tree: dict) -> None:
    print(f"\n=== 消息 {tree['msg_id']}  session={tree['session_id']} ===")
    print(f"  用户: {tree['user_text']}")
    for n in tree["nodes"]:
        print(f"  [{n.get('ts')}] {n['phase']:<18} {json.dumps(n.get('detail') or {}, ensure_ascii=False)[:140]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="采集一轮并入库")
    ap.add_argument("--recent", type=int, default=0, help="显示最近 N 条消息轨迹")
    ap.add_argument("--tree", default="", help="user:<msg_id> | run:<run_id> | task:<task_id>")
    ap.add_argument("--hours", type=float, default=12)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    trees = build_message_trees(args.hours)

    if args.once:
        save(trees)
        print(f"已采集 {len(trees)} 条消息轨迹 -> {STATE_DB}")

    if args.tree:
        key, _, value = args.tree.partition(":")
        picked = []
        for t in trees:
            if key == "user" and t["msg_id"] == value:
                picked.append(t)
            elif key == "run" and str(t["run_id"]) == value:
                picked.append(t)
            elif key == "task" and t["task_id"] == value:
                picked.append(t)
        if not picked:
            print(f"未找到 {args.tree}")
            return 1
        for t in picked:
            print_tree(t)
        return 0

    if args.recent:
        for t in sorted(trees, key=lambda x: x["phase_epoch"] or 0, reverse=True)[: args.recent]:
            print(f"[{t['phase_ts']}] msg={t['msg_id'][:8]} run={t['run_id']} "
                  f"task={t['task_id']} phase={t['phase']} | {t['user_text']}")

    if args.json:
        print(json.dumps(trees, ensure_ascii=False, indent=1))

    if not (args.once or args.recent or args.tree or args.json):
        print("用法: --once | --recent N | --tree user:<id> | --json")
    return 0


if __name__ == "__main__":
    sys.exit(main())


