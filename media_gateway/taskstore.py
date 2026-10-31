"""持久化任务存储（P0.3）。

设计要点（依据 docs/P0-async-media-task-design.md 及 2026-10-31 审查意见）：

1. 状态机
       pending -> running -> completed | failed | cancelled
       running -> submission_unknown   （恢复期无法证明是否执行过，禁止自动重提）
       pending -> cancelled

2. 幂等（区分「请求身份」与「请求内容」）
       idempotency_key   = 请求身份（调用方提供；不提供则生成新的随机 key，绝不用内容哈希）
       payload_hash      = 请求内容指纹（sha256 of canonical json）
       - 同 key + 同 hash  -> 返回原任务（idempotent_hit=True）
       - 同 key + 异 hash  -> 409 Conflict
       - 异 key + 同 hash  -> 正常新建

3. 事务性 outbox
       任务状态变更与 outbox 事件在**同一个 SQLite 事务**里提交，
       避免「任务建了、上报事件没生成」。

4. 恢复
       不把查不到的任务直接判 failed；证据不足时进入 submission_unknown，
       并要求人工/上层确认，绝不自动重新提交（避免重复消耗 GPU）。

本模块不依赖 FastAPI / MCP，可独立测试。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from typing import Any, Awaitable, Callable

log = logging.getLogger("mobius.taskstore")

SCHEMA_VERSION = 1

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_UNKNOWN = "submission_unknown"

STATUSES = (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    STATUS_UNKNOWN,
)

# 终态：不会再有 worker 去动它
TERMINAL = {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_UNKNOWN}

# 旧词汇 -> 新词汇（读取兼容）
LEGACY_ALIASES = {
    "queued": STATUS_PENDING,
    "succeeded": STATUS_COMPLETED,
    "canceled": STATUS_CANCELLED,
}

# 合法迁移（写库时校验）
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    STATUS_PENDING: {STATUS_RUNNING, STATUS_CANCELLED, STATUS_UNKNOWN},
    STATUS_RUNNING: {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_UNKNOWN},
    STATUS_COMPLETED: set(),
    STATUS_FAILED: set(),
    STATUS_CANCELLED: set(),
    STATUS_UNKNOWN: set(),
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_snapshot (
  task_id          TEXT PRIMARY KEY,
  idempotency_key  TEXT NOT NULL,
  payload_hash     TEXT NOT NULL,
  kind             TEXT NOT NULL,
  status           TEXT NOT NULL,
  source           TEXT,
  user_id          TEXT,
  session_id       TEXT,
  character        TEXT,
  prompt           TEXT,
  params_json      TEXT NOT NULL DEFAULT '{}',
  payload_json     TEXT NOT NULL DEFAULT '{}',
  remote_id        TEXT,
  result_json      TEXT,
  error_code       TEXT,
  error_message    TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  attempts         INTEGER NOT NULL DEFAULT 0,
  version          INTEGER NOT NULL DEFAULT 1,
  created_at       REAL NOT NULL,
  started_at       REAL,
  finished_at      REAL,
  updated_at       REAL NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_task_idem_key ON task_snapshot(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_task_status ON task_snapshot(status);
CREATE INDEX IF NOT EXISTS ix_task_created ON task_snapshot(created_at);

CREATE TABLE IF NOT EXISTS outbox (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id      TEXT NOT NULL,
  op           TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at   REAL NOT NULL,
  sent_at      REAL
);

CREATE INDEX IF NOT EXISTS ix_outbox_unsent ON outbox(sent_at);
"""


class TaskConflict(Exception):
    """同一 idempotency_key 但 payload 不一致。"""

    def __init__(self, task_id: str):
        super().__init__(f"idempotency_key 冲突，已存在任务 {task_id}")
        self.task_id = task_id


def canonical_hash(payload: dict[str, Any]) -> str:
    """稳定内容指纹：键排序、紧凑分隔符，跨平台一致。"""
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def normalize_status(value: str | None) -> str:
    value = (value or "").strip().lower()
    return LEGACY_ALIASES.get(value, value)


def _now() -> float:
    return time.time()


class TaskRecord:
    """任务的内存视图；所有读写都回落到 SQLite。"""

    def __init__(self, row: sqlite3.Row):
        self._row = row

    # -- 只读属性 ---------------------------------------------------------
    @property
    def id(self) -> str:
        return self._row["task_id"]

    @property
    def task_id(self) -> str:
        return self.id

    @property
    def idempotency_key(self) -> str:
        return self._row["idempotency_key"]

    @property
    def payload_hash(self) -> str:
        return self._row["payload_hash"]

    @property
    def kind(self) -> str:
        return self._row["kind"]

    @property
    def status(self) -> str:
        return self._row["status"]

    @property
    def source(self) -> str | None:
        return self._row["source"]

    @property
    def user_id(self) -> str | None:
        return self._row["user_id"]

    @property
    def session_id(self) -> str | None:
        return self._row["session_id"]

    @property
    def character(self) -> str | None:
        return self._row["character"]

    @property
    def prompt(self) -> str:
        return self._row["prompt"] or ""

    @property
    def remote_id(self) -> str | None:
        return self._row["remote_id"]

    @property
    def cancel_requested(self) -> bool:
        return bool(self._row["cancel_requested"])

    @property
    def attempts(self) -> int:
        return int(self._row["attempts"] or 0)

    @property
    def version(self) -> int:
        return int(self._row["version"] or 0)

    @property
    def created_at(self) -> float:
        return float(self._row["created_at"])

    @property
    def started_at(self) -> float | None:
        return self._row["started_at"]

    @property
    def finished_at(self) -> float | None:
        return self._row["finished_at"]

    @property
    def result(self) -> dict[str, Any] | None:
        raw = self._row["result_json"]
        return json.loads(raw) if raw else None

    @property
    def error(self) -> str | None:
        return self._row["error_message"]

    @property
    def error_code(self) -> str | None:
        return self._row["error_code"]

    @property
    def params(self) -> dict[str, Any]:
        raw = self._row["params_json"]
        return json.loads(raw) if raw else {}

    @property
    def payload(self) -> dict[str, Any]:
        raw = self._row["payload_json"]
        return json.loads(raw) if raw else {}

    @property
    def spec(self) -> dict[str, Any]:
        """执行用的完整参数（由 params + payload 还原）。"""
        merged = dict(self.payload)
        merged.update(self.params)
        return merged

    def public(self) -> dict[str, Any]:
        out = {
            "id": self.id,
            "task_id": self.id,
            "kind": self.kind,
            "status": self.status,
            "source": self.source,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "character": self.character,
            "prompt": self.prompt,
            "params": self.params,
            "result": self.result,
            "error": self.error,
            "remote_id": self.remote_id,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_requested": self.cancel_requested,
            "attempts": self.attempts,
            "version": self.version,
        }
        if self.error_code:
            out["error_code"] = self.error_code
        if self.status == STATUS_UNKNOWN:
            out["hint"] = (
                "恢复时无法确认该任务是否执行过；为避免重复消耗 GPU，不会自动重新提交，"
                "请人工确认后取消或重新提交。"
            )
        return out


class DurableTaskStore:
    """SQLite 持久化任务存储（单进程内使用，WAL 模式）。"""

    def __init__(self, db_path: str, *, outbox_limit: int = 500) -> None:
        self.db_path = db_path
        self._outbox_limit = outbox_limit
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self._con = sqlite3.connect(db_path, timeout=30, isolation_level=None)
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL")
        self._con.execute("PRAGMA synchronous=NORMAL")
        self._con.execute("PRAGMA busy_timeout=30000")
        self.ensure_schema()

    # -- 生命周期 ---------------------------------------------------------
    def ensure_schema(self) -> None:
        self._con.executescript(SCHEMA_SQL)
        row = self._con.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            self._con.execute(
                "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif int(row["value"]) != SCHEMA_VERSION:
            raise RuntimeError(
                f"task store schema 版本不匹配：库={row['value']} 代码={SCHEMA_VERSION}"
            )

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:  # noqa: BLE001
            pass

    # -- 事务 -------------------------------------------------------------
    def _tx(self):
        return _Tx(self._con)

    def _outbox_insert(self, con: sqlite3.Connection, task_id: str, op: str, payload: dict) -> None:
        con.execute(
            "INSERT INTO outbox(task_id, op, payload_json, created_at) VALUES(?,?,?,?)",
            (task_id, op, json.dumps(payload, ensure_ascii=False), _now()),
        )

    # -- 写入 -------------------------------------------------------------
    def submit(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        spec: dict[str, Any],
        source: str = "console",
        user_id: str | None = None,
        session_id: str | None = None,
        idempotency_key: str | None = None,
        task_id: str | None = None,
    ) -> tuple[TaskRecord, bool]:
        """创建任务。返回 (记录, 是否命中原任务)。

        同一 idempotency_key + 不同 payload -> TaskConflict。
        """
        payload_hash = canonical_hash(payload)
        key = (idempotency_key or "").strip() or f"req_{uuid.uuid4().hex}"

        with self._tx() as con:
            row = con.execute(
                "SELECT task_id, payload_hash FROM task_snapshot WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if row is not None:
                if row["payload_hash"] == payload_hash:
                    return self.get(row["task_id"]), True
                raise TaskConflict(row["task_id"])

            prefix = "g_" if kind == "image" else "v_"
            tid = task_id or (prefix + uuid.uuid4().hex[:8])
            now = _now()
            params = {
                "workflow": spec.get("workflow"),
                "checkpoint": spec.get("checkpoint"),
                "loras": spec.get("loras") or [],
                "lora_source": spec.get("lora_source"),
                "width": spec.get("width"),
                "height": spec.get("height"),
                "seed": spec.get("seed"),
                "negative": spec.get("negative"),
                "reference_image": spec.get("reference_image"),
                "warnings": list(spec.get("warnings") or []),
            }
            con.execute(
                "INSERT INTO task_snapshot("
                "task_id, idempotency_key, payload_hash, kind, status, source, user_id, session_id,"
                " character, prompt, params_json, payload_json, created_at, updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tid, key, payload_hash, kind, STATUS_PENDING, source or "console",
                    user_id, session_id, spec.get("character"), spec.get("prompt", "") or "",
                    json.dumps(params, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    now, now,
                ),
            )
            self._outbox_insert(
                con, tid, "task.created",
                {"task_id": tid, "kind": kind, "status": STATUS_PENDING,
                 "source": source, "user_id": user_id, "session_id": session_id,
                 "payload_hash": payload_hash, "idempotency_key": key, "created_at": now},
            )
        log.info("task %s pending (%s, workflow=%s)", tid, kind, spec.get("workflow"))
        return self.get(tid), False  # type: ignore[return-value]

    def record_terminal(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        spec: dict[str, Any],
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        error_code: str | None = None,
        source: str = "console",
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> TaskRecord:
        """直接写入一个已终态的任务（用于同步执行后补记，不经过 worker）。"""
        status = normalize_status(status)
        if status not in TERMINAL:
            raise ValueError(f"record_terminal 只接受终态: {status}")
        prefix = "g_" if kind == "image" else "v_"
        tid = prefix + uuid.uuid4().hex[:8]
        payload_hash = canonical_hash(payload)
        key = f"req_{uuid.uuid4().hex}"
        now = _now()
        params = {
            "workflow": spec.get("workflow"),
            "checkpoint": spec.get("checkpoint"),
            "loras": spec.get("loras") or [],
            "engine": spec.get("engine"),
            "voice": spec.get("voice"),
            "speed": spec.get("speed"),
        }
        with self._tx() as con:
            con.execute(
                "INSERT INTO task_snapshot("
                "task_id, idempotency_key, payload_hash, kind, status, source, user_id, session_id,"
                " character, prompt, params_json, payload_json, result_json, remote_id,"
                " error_code, error_message, attempts, created_at, started_at, finished_at, updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tid, key, payload_hash, kind, status, source or "console", user_id, session_id,
                    spec.get("character"), spec.get("prompt", "") or "",
                    json.dumps(params, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    None, error_code, error, 1, now, now, now, now,
                ),
            )
            self._outbox_insert(
                con, tid, "task.created",
                {"task_id": tid, "kind": kind, "status": status, "source": source,
                 "user_id": user_id, "session_id": session_id, "created_at": now},
            )
        return self.get(tid)  # type: ignore[return-value]

    def claim_next(self) -> TaskRecord | None:
        """原子地把最早的 pending 任务置为 running。"""
        with self._tx() as con:
            row = con.execute(
                "SELECT * FROM task_snapshot WHERE status=? ORDER BY created_at LIMIT 1",
                (STATUS_PENDING,),
            ).fetchone()
            if row is None:
                return None
            task_id = row["task_id"]
            now = _now()
            con.execute(
                "UPDATE task_snapshot SET status=?, started_at=?, attempts=attempts+1,"
                " updated_at=?, version=version+1 WHERE task_id=? AND status=?",
                (STATUS_RUNNING, now, now, task_id, STATUS_PENDING),
            )
            self._outbox_insert(
                con, task_id, "task.updated",
                {"task_id": task_id, "status": STATUS_RUNNING, "started_at": now},
            )
        return self.get(task_id)

    def set_remote_id(self, task_id: str, remote_id: str) -> None:
        with self._tx() as con:
            con.execute(
                "UPDATE task_snapshot SET remote_id=?, updated_at=?, version=version+1"
                " WHERE task_id=?",
                (remote_id, _now(), task_id),
            )

    def update_params(self, task_id: str, extra: dict[str, Any]) -> None:
        rec = self.get(task_id)
        if rec is None:
            return
        merged = rec.params
        merged.update(extra)
        with self._tx() as con:
            con.execute(
                "UPDATE task_snapshot SET params_json=?, updated_at=?, version=version+1"
                " WHERE task_id=?",
                (json.dumps(merged, ensure_ascii=False), _now(), task_id),
            )

    def finish(
        self,
        task_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        error_code: str | None = None,
    ) -> TaskRecord | None:
        status = normalize_status(status)
        if status not in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_UNKNOWN):
            raise ValueError(f"非法终态: {status}")
        with self._tx() as con:
            row = con.execute(
                "SELECT status FROM task_snapshot WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            current = normalize_status(row["status"])
            if current in TERMINAL:
                return self.get(task_id)  # 幂等：重复结束不报错
            if status not in ALLOWED_TRANSITIONS.get(current, set()):
                raise ValueError(f"非法状态迁移 {current} -> {status}")
            now = _now()
            con.execute(
                "UPDATE task_snapshot SET status=?, result_json=?, error_code=?,"
                " error_message=?, finished_at=?, updated_at=?, version=version+1"
                " WHERE task_id=?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error_code, error, now, now, task_id,
                ),
            )
            self._outbox_insert(
                con, task_id, "task.updated",
                {"task_id": task_id, "status": status, "error_code": error_code,
                 "error_message": error, "finished_at": now,
                 "result": result},
            )
        return self.get(task_id)

    def request_cancel(self, task_id: str) -> TaskRecord | None:
        """置取消标记；pending 直接进 cancelled。运行中由调用方决定是否远程中断。"""
        with self._tx() as con:
            row = con.execute(
                "SELECT status FROM task_snapshot WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            current = normalize_status(row["status"])
            if current in TERMINAL:
                return self.get(task_id)
            now = _now()
            if current == STATUS_PENDING:
                con.execute(
                    "UPDATE task_snapshot SET status=?, cancel_requested=1, finished_at=?,"
                    " updated_at=?, version=version+1 WHERE task_id=?",
                    (STATUS_CANCELLED, now, now, task_id),
                )
                self._outbox_insert(
                    con, task_id, "task.updated",
                    {"task_id": task_id, "status": STATUS_CANCELLED, "finished_at": now},
                )
            else:
                con.execute(
                    "UPDATE task_snapshot SET cancel_requested=1, updated_at=?,"
                    " version=version+1 WHERE task_id=?",
                    (now, task_id),
                )
        return self.get(task_id)

    # -- 读取 -------------------------------------------------------------
    def get(self, task_id: str) -> TaskRecord | None:
        row = self._con.execute(
            "SELECT * FROM task_snapshot WHERE task_id=?", (str(task_id),)
        ).fetchone()
        return TaskRecord(row) if row is not None else None

    def get_by_idempotency_key(self, key: str) -> TaskRecord | None:
        row = self._con.execute(
            "SELECT * FROM task_snapshot WHERE idempotency_key=?", (key,)
        ).fetchone()
        return TaskRecord(row) if row is not None else None

    def list(
        self,
        limit: int = 50,
        status: str | None = None,
        source: str | None = None,
    ) -> list[TaskRecord]:
        sql = "SELECT * FROM task_snapshot"
        clauses, args = [], []
        if status:
            norm = normalize_status(status)
            clauses.append("status=?")
            args.append(norm)
        if source:
            clauses.append("source=?")
            args.append(source)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(int(limit or 50), 200)))
        return [TaskRecord(r) for r in self._con.execute(sql, args).fetchall()]

    def count_by_status(self) -> dict[str, int]:
        rows = self._con.execute(
            "SELECT status, COUNT(*) AS n FROM task_snapshot GROUP BY status"
        ).fetchall()
        return {normalize_status(r["status"]): int(r["n"]) for r in rows}

    def pending_count(self) -> int:
        row = self._con.execute(
            "SELECT COUNT(*) AS n FROM task_snapshot WHERE status=?", (STATUS_PENDING,)
        ).fetchone()
        return int(row["n"])

    # -- Outbox -----------------------------------------------------------
    def outbox_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._con.execute(
            "SELECT seq, task_id, op, payload_json, created_at FROM outbox"
            " WHERE sent_at IS NULL ORDER BY seq LIMIT ?",
            (max(1, min(int(limit), self._outbox_limit)),),
        ).fetchall()
        return [
            {
                "seq": r["seq"],
                "task_id": r["task_id"],
                "op": r["op"],
                "payload": json.loads(r["payload_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def mark_outbox_sent(self, seqs: list[int]) -> None:
        if not seqs:
            return
        now = _now()
        with self._tx() as con:
            con.executemany(
                "UPDATE outbox SET sent_at=? WHERE seq=? AND sent_at IS NULL",
                [(now, seq) for seq in seqs],
            )

    def outbox_pending_count(self) -> int:
        row = self._con.execute(
            "SELECT COUNT(*) AS n FROM outbox WHERE sent_at IS NULL"
        ).fetchone()
        return int(row["n"])

    async def flush_outbox(
        self,
        sender: Callable[[dict[str, Any]], Awaitable[bool]],
        *,
        batch: int = 50,
    ) -> int:
        """按 seq 顺序投递；遇到第一个失败即停止（保持顺序，下次重试）。"""
        items = self.outbox_pending(batch)
        sent: list[int] = []
        for item in items:
            try:
                ok = await sender(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("outbox 投递异常（保留重试）seq=%s: %s", item["seq"], exc)
                break
            if not ok:
                break
            sent.append(item["seq"])
        if sent:
            self.mark_outbox_sent(sent)
        return len(sent)

    # -- 恢复 -------------------------------------------------------------
    def mark_submission_unknown(self, task_id: str, reason: str) -> TaskRecord | None:
        """恢复期证据不足：进入 submission_unknown，禁止自动重提。"""
        with self._tx() as con:
            row = con.execute(
                "SELECT status FROM task_snapshot WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            current = normalize_status(row["status"])
            if current in TERMINAL:
                return self.get(task_id)
            now = _now()
            con.execute(
                "UPDATE task_snapshot SET status=?, error_code=?, error_message=?,"
                " updated_at=?, version=version+1 WHERE task_id=?",
                (STATUS_UNKNOWN, "submission_unknown", reason, now, task_id),
            )
            self._outbox_insert(
                con, task_id, "task.updated",
                {"task_id": task_id, "status": STATUS_UNKNOWN,
                 "error_code": "submission_unknown", "error_message": reason},
            )
        return self.get(task_id)

    async def recover(
        self,
        probe: Callable[[str], Awaitable[tuple[str, dict[str, Any] | None]]],
    ) -> dict[str, int]:
        """重启恢复。

        probe(prompt_id) -> (state, result)
            state: completed | active | absent | unknown
            result: 完成时的结果字典

        规则（审查意见）：
          - status=running 且有 prompt_id：
                completed -> completed
                active    -> 保持 running（worker 继续）
                absent    -> submission_unknown（历史可能被清理 / ComfyUI 重启过）
                unknown   -> submission_unknown
          - status=running 但无 prompt_id：submission_unknown（崩在记录之前）
          - status=pending：保持，worker 重新拾取
        """
        stats = {"completed": 0, "active": 0, "unknown": 0, "pending": 0}
        rows = self._con.execute(
            "SELECT task_id, status, remote_id FROM task_snapshot WHERE status IN (?,?)",
            (STATUS_RUNNING, STATUS_PENDING),
        ).fetchall()
        for row in rows:
            status = normalize_status(row["status"])
            task_id = row["task_id"]
            if status == STATUS_PENDING:
                stats["pending"] += 1
                continue
            prompt_id = row["remote_id"]
            if not prompt_id:
                self.mark_submission_unknown(
                    task_id, "崩溃发生在记录 prompt_id 之前，无法确认是否已提交"
                )
                stats["unknown"] += 1
                continue
            try:
                state, result = await probe(prompt_id)
            except Exception as exc:  # noqa: BLE001
                self.mark_submission_unknown(task_id, f"恢复探测失败：{exc}")
                stats["unknown"] += 1
                continue
            if state == "completed":
                self.finish(task_id, status=STATUS_COMPLETED, result=result or {})
                stats["completed"] += 1
            elif state == "active":
                stats["active"] += 1
            else:
                self.mark_submission_unknown(
                    task_id,
                    "ComfyUI 既无历史也无队列记录（可能被清理或重启过），无法确认是否执行",
                )
                stats["unknown"] += 1
        if any(stats.values()):
            log.info("任务恢复完成：%s", stats)
        return stats


class _Tx:
    """显式 BEGIN IMMEDIATE 的事务上下文（sqlite3 默认 isolation_level=None）。"""

    def __init__(self, con: sqlite3.Connection) -> None:
        self._con = con

    def __enter__(self) -> sqlite3.Connection:
        self._con.execute("BEGIN IMMEDIATE")
        return self._con

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._con.execute("COMMIT")
        else:
            self._con.execute("ROLLBACK")
        return False
