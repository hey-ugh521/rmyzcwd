"""Gateway 任务存储的 PostgreSQL 实现（阶段 2）。

与 `taskstore.DurableTaskStore` 语义一致：
- 幂等（idempotency_key + payload_hash）
- 任务状态变更与 outbox 在同一事务
- 队列领取用 `FOR UPDATE SKIP LOCKED`（多 worker 安全）
- 恢复/提交不确定语义不变

通过环境变量切换：MOBIUS_TASK_STORE=pg + MOBIUS_TASK_DSN=postgresql://...
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Awaitable, Callable

import psycopg
from psycopg.rows import dict_row

from taskstore import (
    ALLOWED_TRANSITIONS,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_UNKNOWN,
    TERMINAL,
    TaskConflict,
    TaskRecord,
    _now,
    canonical_hash,
    normalize_status,
)

log = logging.getLogger("mobius.pgstore")

SCHEMA = """
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
  prompt           TEXT NOT NULL DEFAULT '',
  params_json      TEXT NOT NULL DEFAULT '{}',
  payload_json     TEXT NOT NULL DEFAULT '{}',
  remote_id        TEXT,
  result_json      TEXT,
  error_code       TEXT,
  error_message    TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  attempts         INTEGER NOT NULL DEFAULT 0,
  version          INTEGER NOT NULL DEFAULT 0,
  created_at       DOUBLE PRECISION NOT NULL,
  started_at       DOUBLE PRECISION,
  finished_at      DOUBLE PRECISION,
  updated_at       DOUBLE PRECISION NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_task_idem_key ON task_snapshot(idempotency_key);
CREATE INDEX IF NOT EXISTS ix_task_status ON task_snapshot(status);
CREATE INDEX IF NOT EXISTS ix_task_created ON task_snapshot(created_at);
CREATE TABLE IF NOT EXISTS outbox (
  seq          BIGSERIAL PRIMARY KEY,
  task_id      TEXT NOT NULL,
  op           TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at   DOUBLE PRECISION NOT NULL,
  sent_at      DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS ix_outbox_unsent ON outbox(sent_at);
"""

SCHEMA_VERSION = "1"


class PgTaskStore:
    def __init__(self, dsn: str, *, outbox_limit: int = 500) -> None:
        self._dsn = dsn
        self._outbox_limit = outbox_limit
        self.ensure_schema()

    # -- 连接/事务 --------------------------------------------------------
    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn, row_factory=dict_row, autocommit=False)

    def ensure_schema(self) -> None:
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(SCHEMA)
                cur.execute(
                    "INSERT INTO schema_meta(key, value) VALUES('schema_version', %s)"
                    " ON CONFLICT (key) DO NOTHING",
                    (SCHEMA_VERSION,),
                )
            con.commit()

    def close(self) -> None:
        return None

    # -- 写入 -------------------------------------------------------------
    def _outbox_insert(self, cur, task_id: str, op: str, payload: dict) -> None:
        cur.execute(
            "INSERT INTO outbox(task_id, op, payload_json, created_at) VALUES(%s,%s,%s,%s)",
            (task_id, op, json.dumps(payload, ensure_ascii=False), _now()),
        )

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
        payload_hash = canonical_hash(payload)
        key = (idempotency_key or "").strip() or f"req_{uuid.uuid4().hex}"
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT task_id, payload_hash FROM task_snapshot WHERE idempotency_key=%s",
                    (key,),
                )
                row = cur.fetchone()
                if row is not None:
                    if row["payload_hash"] == payload_hash:
                        con.commit()
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
                cur.execute(
                    "INSERT INTO task_snapshot("
                    "task_id, idempotency_key, payload_hash, kind, status, source, user_id, session_id,"
                    " character, prompt, params_json, payload_json, created_at, updated_at"
                    ") VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        tid, key, payload_hash, kind, STATUS_PENDING, source or "console",
                        user_id, session_id, spec.get("character"), spec.get("prompt", "") or "",
                        json.dumps(params, ensure_ascii=False),
                        json.dumps(payload, ensure_ascii=False), now, now,
                    ),
                )
                self._outbox_insert(
                    cur, tid, "task.created",
                    {"task_id": tid, "kind": kind, "status": STATUS_PENDING,
                     "source": source, "user_id": user_id, "session_id": session_id,
                     "payload_hash": payload_hash, "idempotency_key": key, "created_at": now},
                )
            con.commit()
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
        status = normalize_status(status)
        if status not in TERMINAL:
            raise ValueError(f"record_terminal 只接受终态: {status}")
        prefix = "g_" if kind == "image" else "v_"
        tid = prefix + uuid.uuid4().hex[:8]
        payload_hash = canonical_hash(payload)
        key = f"req_{uuid.uuid4().hex}"
        now = _now()
        params = {
            "workflow": spec.get("workflow"), "checkpoint": spec.get("checkpoint"),
            "loras": spec.get("loras") or [], "engine": spec.get("engine"),
            "voice": spec.get("voice"), "speed": spec.get("speed"),
        }
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(
                    "INSERT INTO task_snapshot("
                    "task_id, idempotency_key, payload_hash, kind, status, source, user_id, session_id,"
                    " character, prompt, params_json, payload_json, result_json, remote_id,"
                    " error_code, error_message, attempts, created_at, started_at, finished_at, updated_at"
                    ") VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
                    cur, tid, "task.created",
                    {"task_id": tid, "kind": kind, "status": status, "source": source,
                     "user_id": user_id, "session_id": session_id, "created_at": now},
                )
            con.commit()
        return self.get(tid)  # type: ignore[return-value]

    def claim_next(self) -> TaskRecord | None:
        """多 worker 安全的原子领取（SKIP LOCKED）。"""
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT * FROM task_snapshot WHERE status=%s ORDER BY created_at"
                    " LIMIT 1 FOR UPDATE SKIP LOCKED",
                    (STATUS_PENDING,),
                )
                row = cur.fetchone()
                if row is None:
                    con.commit()
                    return None
                task_id = row["task_id"]
                now = _now()
                cur.execute(
                    "UPDATE task_snapshot SET status=%s, started_at=%s, attempts=attempts+1,"
                    " updated_at=%s, version=version+1 WHERE task_id=%s AND status=%s",
                    (STATUS_RUNNING, now, now, task_id, STATUS_PENDING),
                )
                if cur.rowcount == 0:
                    con.commit()
                    return None
                self._outbox_insert(
                    cur, task_id, "task.updated",
                    {"task_id": task_id, "status": STATUS_RUNNING, "started_at": now},
                )
            con.commit()
        return self.get(task_id)

    def set_remote_id(self, task_id: str, remote_id: str) -> None:
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(
                    "UPDATE task_snapshot SET remote_id=%s, updated_at=%s, version=version+1"
                    " WHERE task_id=%s",
                    (remote_id, _now(), task_id),
                )
            con.commit()

    def update_params(self, task_id: str, extra: dict[str, Any]) -> None:
        rec = self.get(task_id)
        if rec is None:
            return
        merged = rec.params
        merged.update(extra)
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(
                    "UPDATE task_snapshot SET params_json=%s, updated_at=%s, version=version+1"
                    " WHERE task_id=%s",
                    (json.dumps(merged, ensure_ascii=False), _now(), task_id),
                )
            con.commit()

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
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT status FROM task_snapshot WHERE task_id=%s", (task_id,))
                row = cur.fetchone()
                if row is None:
                    con.commit()
                    return None
                current = normalize_status(row["status"])
                if current in TERMINAL:
                    con.commit()
                    return self.get(task_id)
                if status not in ALLOWED_TRANSITIONS.get(current, set()):
                    raise ValueError(f"非法状态迁移 {current} -> {status}")
                now = _now()
                cur.execute(
                    "UPDATE task_snapshot SET status=%s, result_json=%s, error_code=%s,"
                    " error_message=%s, finished_at=%s, updated_at=%s, version=version+1"
                    " WHERE task_id=%s",
                    (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
                     error_code, error, now, now, task_id),
                )
                self._outbox_insert(
                    cur, task_id, "task.updated",
                    {"task_id": task_id, "status": status, "error_code": error_code,
                     "error_message": error, "finished_at": now, "result": result},
                )
            con.commit()
        return self.get(task_id)

    def request_cancel(self, task_id: str) -> TaskRecord | None:
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT status FROM task_snapshot WHERE task_id=%s", (task_id,))
                row = cur.fetchone()
                if row is None:
                    con.commit()
                    return None
                current = normalize_status(row["status"])
                if current in TERMINAL:
                    con.commit()
                    return self.get(task_id)
                now = _now()
                if current == STATUS_PENDING:
                    cur.execute(
                        "UPDATE task_snapshot SET status=%s, cancel_requested=1, finished_at=%s,"
                        " updated_at=%s, version=version+1 WHERE task_id=%s",
                        (STATUS_CANCELLED, now, now, task_id),
                    )
                    self._outbox_insert(
                        cur, task_id, "task.updated",
                        {"task_id": task_id, "status": STATUS_CANCELLED, "finished_at": now},
                    )
                else:
                    cur.execute(
                        "UPDATE task_snapshot SET cancel_requested=1, updated_at=%s,"
                        " version=version+1 WHERE task_id=%s",
                        (now, task_id),
                    )
            con.commit()
        return self.get(task_id)

    # -- 读取 -------------------------------------------------------------
    def get(self, task_id: str) -> TaskRecord | None:
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT * FROM task_snapshot WHERE task_id=%s", (str(task_id),))
                row = cur.fetchone()
            con.commit()
        return TaskRecord(row) if row is not None else None

    def get_by_idempotency_key(self, key: str) -> TaskRecord | None:
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT * FROM task_snapshot WHERE idempotency_key=%s", (key,))
                row = cur.fetchone()
            con.commit()
        return TaskRecord(row) if row is not None else None

    def list(self, limit: int = 50, status: str | None = None, source: str | None = None) -> list[TaskRecord]:
        sql = "SELECT * FROM task_snapshot"
        clauses, args = [], []
        if status:
            clauses.append("status=%s")
            args.append(normalize_status(status))
        if source:
            clauses.append("source=%s")
            args.append(source)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT %s"
        args.append(max(1, min(int(limit or 50), 200)))
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(sql, args)
                rows = cur.fetchall()
            con.commit()
        return [TaskRecord(r) for r in rows]

    def count_by_status(self) -> dict[str, int]:
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT status, COUNT(*) AS n FROM task_snapshot GROUP BY status")
                rows = cur.fetchall()
            con.commit()
        return {normalize_status(r["status"]): int(r["n"]) for r in rows}

    def pending_count(self) -> int:
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM task_snapshot WHERE status=%s", (STATUS_PENDING,))
                row = cur.fetchone()
            con.commit()
        return int(row["n"])

    # -- Outbox -----------------------------------------------------------
    def outbox_pending(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT seq, task_id, op, payload_json, created_at FROM outbox"
                    " WHERE sent_at IS NULL ORDER BY seq LIMIT %s",
                    (max(1, min(int(limit), self._outbox_limit)),),
                )
                rows = cur.fetchall()
            con.commit()
        return [{"seq": r["seq"], "task_id": r["task_id"], "op": r["op"],
                 "payload": json.loads(r["payload_json"]), "created_at": r["created_at"]} for r in rows]

    def mark_outbox_sent(self, seqs: list[int]) -> None:
        if not seqs:
            return
        now = _now()
        with self._connect() as con:
            with con.cursor() as cur:
                for seq in seqs:
                    cur.execute("UPDATE outbox SET sent_at=%s WHERE seq=%s AND sent_at IS NULL", (now, seq))
            con.commit()

    def outbox_pending_count(self) -> int:
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM outbox WHERE sent_at IS NULL")
                row = cur.fetchone()
            con.commit()
        return int(row["n"])

    async def flush_outbox(
        self,
        sender: Callable[[dict[str, Any]], Awaitable[bool]],
        *,
        batch: int = 50,
    ) -> int:
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
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute("SELECT status FROM task_snapshot WHERE task_id=%s", (task_id,))
                row = cur.fetchone()
                if row is None:
                    con.commit()
                    return None
                if normalize_status(row["status"]) in TERMINAL:
                    con.commit()
                    return self.get(task_id)
                now = _now()
                cur.execute(
                    "UPDATE task_snapshot SET status=%s, error_code=%s, error_message=%s,"
                    " updated_at=%s, version=version+1 WHERE task_id=%s",
                    (STATUS_UNKNOWN, "submission_unknown", reason, now, task_id),
                )
                self._outbox_insert(
                    cur, task_id, "task.updated",
                    {"task_id": task_id, "status": STATUS_UNKNOWN,
                     "error_code": "submission_unknown", "error_message": reason},
                )
            con.commit()
        return self.get(task_id)

    async def recover(
        self,
        probe: Callable[[str], Awaitable[tuple[str, dict[str, Any] | None]]],
    ) -> dict[str, int]:
        stats = {"completed": 0, "active": 0, "unknown": 0, "pending": 0}
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT task_id, status, remote_id FROM task_snapshot WHERE status IN (%s,%s)",
                    (STATUS_RUNNING, STATUS_PENDING),
                )
                rows = cur.fetchall()
            con.commit()
        for row in rows:
            status = normalize_status(row["status"])
            task_id = row["task_id"]
            if status == STATUS_PENDING:
                stats["pending"] += 1
                continue
            prompt_id = row["remote_id"]
            if not prompt_id:
                self.mark_submission_unknown(task_id, "崩溃发生在记录 prompt_id 之前，无法确认是否已提交")
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
                    task_id, "ComfyUI 既无历史也无队列记录（可能被清理或重启过），无法确认是否执行")
                stats["unknown"] += 1
        if any(stats.values()):
            log.info("任务恢复完成：%s", stats)
        return stats
