"""Möbius Core —— 配置、存储、角色档案。全系统唯一事实来源。

本模块不依赖 FastAPI/httpx，可被独立调用与测试。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent          # mobius/
CONFIG_PATH = Path(os.environ.get("MOBIUS_CONFIG") or (BASE / "config.json"))


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


CFG: dict = _load_config()
# L3 P1：展开配置里的 ${VAR} 路径占位符（ROOT/RUNTIME/MODELS/自定义 paths）
try:
    from . import paths as _paths

    CFG = _paths.expand_config(CFG)
except Exception:  # noqa: BLE001
    pass

# 密钥 SSOT：secrets.json 是唯一编辑源，启动时覆盖到已展开配置
try:
    from . import secrets as _secrets

    _secrets.apply_to_config(CFG, BASE)
except Exception:  # noqa: BLE001
    pass
DB_PATH = (BASE / CFG.get("db", "data/mobius.db")).resolve()
# 阶段 3：存储后端可切换（sqlite 默认；pg 走 PostgreSQL）
DB_BACKEND = str(os.environ.get("CORE_DB_BACKEND") or CFG.get("db_backend") or "sqlite").lower()
PG_DSN = str(os.environ.get("CORE_DB_DSN") or CFG.get("db_dsn") or "")
CHARS_DIR = (BASE / CFG.get("characters_dir", "data/characters")).resolve()
DATA_DIR = DB_PATH.parent
LOG_DIR = BASE / "logs"

_lock = threading.RLock()


def now() -> float:
    return time.time()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class _PgConnection:
    """把 psycopg 连接包成 sqlite3 风格的极小适配器（? 占位符 + dict 行）。"""

    def __init__(self) -> None:
        import psycopg
        from psycopg.rows import dict_row

        if not PG_DSN:
            raise RuntimeError("CORE_DB_BACKEND=pg 但缺少 CORE_DB_DSN/db_dsn")
        self._c = psycopg.connect(PG_DSN, row_factory=dict_row, autocommit=False)

    @staticmethod
    def _sql(sql: str) -> str:
        return sql.replace("?", "%s")

    def execute(self, sql: str, args: tuple = ()):
        return self._c.execute(self._sql(sql), args)

    def executemany(self, sql: str, seq) -> None:
        with self._c.cursor() as cur:
            cur.executemany(self._sql(sql), seq)

    def executescript(self, script: str) -> None:
        for stmt in script.split(";"):
            if stmt.strip():
                self._c.execute(stmt)

    def commit(self) -> None:
        self._c.commit()

    def rollback(self) -> None:
        self._c.rollback()

    def close(self) -> None:
        self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self._c.commit()
        else:
            self._c.rollback()
        self._c.close()
        return False


def conn():
    if DB_BACKEND == "pg":
        return _PgConnection()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, kind TEXT, status TEXT, source TEXT, user_id TEXT, session_id TEXT,
  character TEXT, prompt TEXT, params TEXT, result TEXT, error TEXT, remote_id TEXT,
  created_at REAL, started_at REAL, finished_at REAL, updated_at REAL,
  idempotency_key TEXT, payload_hash TEXT, payload_json TEXT, execution_id TEXT,
  error_code TEXT, attempts INTEGER DEFAULT 0, lease_owner TEXT, lease_expires_at REAL,
  cancel_requested INTEGER DEFAULT 0, version INTEGER DEFAULT 0, last_event_seq INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS audit(
  id TEXT PRIMARY KEY, ts REAL, actor TEXT, character TEXT, action TEXT, detail TEXT,
  result TEXT, error TEXT, task_id TEXT, source TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC);

CREATE TABLE IF NOT EXISTS turns(
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, user_id TEXT, role TEXT,
  content TEXT, ts REAL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, ts DESC);

CREATE TABLE IF NOT EXISTS user_memory(
  id TEXT PRIMARY KEY, user_id TEXT, character TEXT, key TEXT, value TEXT,
  confidence REAL, source TEXT, created_at REAL, updated_at REAL, deleted INTEGER DEFAULT 0,
  UNIQUE(user_id, key)
);
CREATE INDEX IF NOT EXISTS idx_mem_user ON user_memory(user_id);

CREATE TABLE IF NOT EXISTS memory_candidates(
  id TEXT PRIMARY KEY, user_id TEXT, character TEXT, key TEXT, value TEXT,
  confidence REAL, source TEXT, evidence TEXT, status TEXT, created_at REAL, decided_at REAL
);
CREATE INDEX IF NOT EXISTS idx_cand_status ON memory_candidates(status, created_at DESC);

CREATE TABLE IF NOT EXISTS timeline(
  id TEXT PRIMARY KEY, user_id TEXT, character TEXT, kind TEXT, text TEXT,
  session_id TEXT, ts REAL
);
CREATE INDEX IF NOT EXISTS idx_timeline_user ON timeline(user_id, ts DESC);

CREATE TABLE IF NOT EXISTS constitution(
  id TEXT PRIMARY KEY, char_id TEXT, version INTEGER, text TEXT, active INTEGER,
  created_at REAL, note TEXT
);
CREATE INDEX IF NOT EXISTS idx_cons ON constitution(char_id, version DESC);

CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT, updated_at REAL);

CREATE TABLE IF NOT EXISTS identities(
  platform TEXT, platform_user TEXT, mobius_user TEXT, created_at REAL,
  PRIMARY KEY(platform, platform_user)
);
"""


# ---------------------------------------------------------------- 迁移（M1a：Core 任务权威字段）
# v1=原始 16 列；v2=ADR-007 任务权威字段（幂等/租约/执行/版本）。
# CREATE TABLE IF NOT EXISTS 不会给既有库补列，故用幂等 ALTER 迁移；新库由 SCHEMA 直接建全列。
CORE_SCHEMA_VERSION = 2

# (列名, 声明)——声明在 sqlite 与 PG 上均可用于 ALTER TABLE ADD COLUMN
TASKS_MIGRATION_COLS = (
    ("idempotency_key", "TEXT"),
    ("payload_hash", "TEXT"),
    ("payload_json", "TEXT"),
    ("execution_id", "TEXT"),
    ("error_code", "TEXT"),
    ("attempts", "INTEGER DEFAULT 0"),
    ("lease_owner", "TEXT"),
    ("lease_expires_at", "REAL"),
    ("cancel_requested", "INTEGER DEFAULT 0"),
    ("version", "INTEGER DEFAULT 0"),
    ("last_event_seq", "INTEGER DEFAULT 0"),
)


def _table_columns(c, table: str) -> set[str]:
    """返回表的现有列名（双后端）。"""
    if DB_BACKEND == "pg":
        rows = c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name=?",
            (table,),
        ).fetchall()
        return {r["column_name"] for r in rows}
    rows = c.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


def _migrate_tasks(c) -> None:
    """把 tasks 表补齐到 CORE_SCHEMA_VERSION（幂等；新库无操作，旧库 ALTER 补列）。"""
    c.execute("CREATE TABLE IF NOT EXISTS schema_meta(k TEXT PRIMARY KEY, v TEXT)")
    have = _table_columns(c, "tasks")
    for col, decl in TASKS_MIGRATION_COLS:
        if col not in have:
            c.execute(f"ALTER TABLE tasks ADD COLUMN {col} {decl}")
    # 幂等键唯一索引：既有行 idempotency_key 为 NULL，PG/sqlite 唯一索引均允许多个 NULL，不冲突。
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_idem_key ON tasks(idempotency_key)")
    row = c.execute("SELECT v FROM schema_meta WHERE k='schema_version'").fetchone()
    ver = str(CORE_SCHEMA_VERSION)
    if row is None:
        c.execute("INSERT INTO schema_meta(k, v) VALUES(?, ?)", ("schema_version", ver))
    else:
        c.execute("UPDATE schema_meta SET v=? WHERE k='schema_version'", (ver,))


def init_db() -> None:
    with _lock:
        c = conn()
        try:
            script = SCHEMA
            if DB_BACKEND == "pg":
                script = script.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
            c.executescript(script)
            _migrate_tasks(c)
            c.commit()
        finally:
            c.close()


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def _loads(v, default):
    if v is None or v == "":
        return default
    try:
        return json.loads(v)
    except Exception:
        return default


def _dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


# ---------------------------------------------------------------- kv

def kv_get(key: str, default=None):
    with conn() as c:
        row = c.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
    if not row:
        return default
    return _loads(row["v"], default)


def kv_set(key: str, value) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO kv(k,v,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at",
            (key, _dumps(value), now()),
        )


# ---------------------------------------------------------------- tasks

TASK_COLS = (
    "id", "kind", "status", "source", "user_id", "session_id", "character",
    "prompt", "params", "result", "error", "remote_id",
    "created_at", "started_at", "finished_at", "updated_at",
)


def _task_row(row) -> dict:
    d = dict(row)
    d["params"] = _loads(d.get("params"), {})
    d["result"] = _loads(d.get("result"), None)
    if "payload_json" in d:
        d["payload"] = _loads(d.get("payload_json"), None)
    return d


def task_create(kind: str, source: str, user_id: str, session_id: str,
                character: str | None, prompt: str, params: dict | None = None,
                status: str = "queued") -> dict:
    t = {
        "id": uid("t"), "kind": kind, "status": status, "source": source,
        "user_id": user_id, "session_id": session_id, "character": character,
        "prompt": prompt, "params": _dumps(params or {}), "result": None,
        "error": None, "remote_id": None,
        "created_at": now(), "started_at": None, "finished_at": None, "updated_at": now(),
    }
    with conn() as c:
        c.execute(
            f"INSERT INTO tasks({','.join(TASK_COLS)}) VALUES({','.join('?' * len(TASK_COLS))})",
            tuple(t[k] for k in TASK_COLS),
        )
    return _task_row(dict(t) | {"params": _loads(t["params"], {}), "result": None})


def task_update(task_id: str, **fields) -> dict | None:
    if not fields:
        return task_get(task_id)
    if "params" in fields:
        fields["params"] = _dumps(fields["params"])
    if "result" in fields:
        fields["result"] = _dumps(fields["result"])
    fields["updated_at"] = now()
    sets = ",".join(f"{k}=?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE tasks SET {sets} WHERE id=?", (*fields.values(), task_id))
    return task_get(task_id)


def task_get(task_id: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return _task_row(row) if row else None


def tasks_list(limit: int = 50, status: str | None = None, source: str | None = None) -> list[dict]:
    sql = "SELECT * FROM tasks WHERE 1=1"
    args: list[Any] = []
    if status:
        sql += " AND status=?"
        args.append(status)
    if source:
        sql += " AND source=?"
        args.append(source)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(int(limit))
    with conn() as c:
        return [_task_row(r) for r in c.execute(sql, args).fetchall()]


def tasks_active(kind: str | None = None) -> list[dict]:
    # 方案A：活跃 = 规范词 pending/running（兼容历史词 queued）
    sql = "SELECT * FROM tasks WHERE status IN ('pending','queued','running')"
    args: list[Any] = []
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    with conn() as c:
        return [_task_row(r) for r in c.execute(sql, args).fetchall()]


def tasks_today_counts() -> dict:
    import datetime
    midnight = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    with conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) n FROM tasks WHERE created_at>=? GROUP BY status",
            (midnight,),
        ).fetchall()
    # 方案A：规范词桶；历史行（succeeded/canceled/queued）读时归一到规范桶
    out = {"completed": 0, "failed": 0, "cancelled": 0, "running": 0,
           "pending": 0, "submission_unknown": 0}
    for r in rows:
        bucket = normalize_task_status(r["status"])
        out[bucket] = out.get(bucket, 0) + int(r["n"])
    out["total"] = sum(v for k, v in out.items() if k != "total")
    return out


# ---------------------------------------------------------------- audit

def audit(actor: str, action: str, detail: dict | None = None, result: str = "ok",
          error: str | None = None, task_id: str | None = None,
          character: str | None = None, source: str = "core") -> str:
    aid = uid("a")
    with conn() as c:
        c.execute(
            "INSERT INTO audit(id,ts,actor,character,action,detail,result,error,task_id,source)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (aid, now(), actor, character, action, _dumps(detail or {}), result,
             error, task_id, source),
        )
    return aid


def audit_list(limit: int = 100, kind: str | None = None) -> list[dict]:
    sql = "SELECT * FROM audit WHERE 1=1"
    args: list[Any] = []
    if kind:
        sql += " AND action LIKE ?"
        args.append(f"{kind}%")
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(int(limit))
    with conn() as c:
        rows = _rows(c.execute(sql, args))
    for r in rows:
        r["detail"] = _loads(r.get("detail"), {})
    return rows


def audit_state_history(limit: int = 30, service: str | None = None) -> list[dict]:
    """服务状态/登录态变更历史（供 Console 状态历史面板）。"""
    sql = "SELECT * FROM audit WHERE (action LIKE ? OR action LIKE ?)"
    args: list[Any] = ["state.%", "login.%"]
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(int(limit) * (5 if service else 1))
    with conn() as c:
        rows = _rows(c.execute(sql, args))
    out: list[dict] = []
    for r in rows:
        detail = _loads(r.get("detail"), {})
        r["detail"] = detail
        if service and str(detail.get("service") or "") != service:
            continue
        out.append(r)
        if len(out) >= int(limit):
            break
    return out


# ---------------------------------------------------------------- turns（临时工作记忆）

def turn_add(session_id: str, user_id: str, role: str, content: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO turns(session_id,user_id,role,content,ts) VALUES(?,?,?,?,?)",
            (session_id, user_id, role, content, now()),
        )


def turns_list(session_id: str, limit: int = 30) -> list[dict]:
    with conn() as c:
        rows = _rows(c.execute(
            "SELECT role,content,ts FROM turns WHERE session_id=? ORDER BY ts DESC LIMIT ?",
            (session_id, int(limit)),
        ))
    return list(reversed(rows))


def turns_prune(keep_sec: int = 7 * 86400) -> int:
    with conn() as c:
        n = c.execute("DELETE FROM turns WHERE ts < ?", (now() - keep_sec,)).rowcount
    return n


# ---------------------------------------------------------------- 用户记忆

def mem_upsert(user_id: str, key: str, value: str, confidence: float = 1.0,
               character: str | None = None, source: str = "") -> None:
    with conn() as c:
        row = c.execute("SELECT id FROM user_memory WHERE user_id=? AND key=?", (user_id, key)).fetchone()
        if row:
            c.execute(
                "UPDATE user_memory SET value=?,confidence=?,character=COALESCE(?,character),"
                "source=?,updated_at=?,deleted=0 WHERE id=?",
                (value, confidence, character, source, now(), row["id"]),
            )
        else:
            c.execute(
                "INSERT INTO user_memory(id,user_id,character,key,value,confidence,source,"
                "created_at,updated_at,deleted) VALUES(?,?,?,?,?,?,?,?,?,0)",
                (uid("m"), user_id, character, key, value, confidence, source, now(), now()),
            )


def mem_list(user_id: str) -> list[dict]:
    with conn() as c:
        return _rows(c.execute(
            "SELECT id,key,value,confidence,character,source,created_at,updated_at "
            "FROM user_memory WHERE user_id=? AND deleted=0 ORDER BY updated_at DESC",
            (user_id,),
        ))


def mem_users() -> list[dict]:
    with conn() as c:
        return _rows(c.execute(
            "SELECT user_id, COUNT(*) n, MAX(updated_at) last FROM user_memory "
            "WHERE deleted=0 GROUP BY user_id ORDER BY last DESC"
        ))


def mem_search(user_id: str, query: str, limit: int = 8) -> list[dict]:
    q = f"%{query}%"
    with conn() as c:
        return _rows(c.execute(
            "SELECT key,value,confidence FROM user_memory WHERE user_id=? AND deleted=0 "
            "AND (key LIKE ? OR value LIKE ?) ORDER BY updated_at DESC LIMIT ?",
            (user_id, q, q, int(limit)),
        ))


def mem_forget(user_id: str, key: str) -> bool:
    with conn() as c:
        n = c.execute(
            "UPDATE user_memory SET deleted=1,updated_at=? WHERE user_id=? AND key=? AND deleted=0",
            (now(), user_id, key),
        ).rowcount
    return n > 0


# ---------------------------------------------------------------- 候选记忆

def cand_add(user_id: str, key: str, value: str, confidence: float,
             source: str, evidence: str = "", character: str | None = None) -> dict:
    with conn() as c:
        row = c.execute(
            "SELECT id FROM memory_candidates WHERE user_id=? AND key=? AND status='pending'",
            (user_id, key),
        ).fetchone()
        if row:
            c.execute(
                "UPDATE memory_candidates SET value=?,confidence=?,evidence=?,source=? WHERE id=?",
                (value, confidence, evidence, source, row["id"]),
            )
            cid = row["id"]
        else:
            cid = uid("c")
            c.execute(
                "INSERT INTO memory_candidates(id,user_id,character,key,value,confidence,"
                "source,evidence,status,created_at) VALUES(?,?,?,?,?,?,?,?, 'pending', ?)",
                (cid, user_id, character, key, value, confidence, source, evidence, now()),
            )
    return cand_get(cid)


def cand_get(cid: str) -> dict | None:
    with conn() as c:
        row = c.execute("SELECT * FROM memory_candidates WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else None


def cand_list(status: str = "pending", limit: int = 100) -> list[dict]:
    with conn() as c:
        return _rows(c.execute(
            "SELECT * FROM memory_candidates WHERE status=? ORDER BY created_at DESC LIMIT ?",
            (status, int(limit)),
        ))


def cand_decide(cid: str, approve: bool, edited_value: str | None = None) -> dict | None:
    c0 = cand_get(cid)
    if not c0:
        return None
    status = "approved" if approve else "rejected"
    with conn() as c:
        c.execute("UPDATE memory_candidates SET status=?,decided_at=? WHERE id=?", (status, now(), cid))
    if approve:
        mem_upsert(
            c0["user_id"], c0["key"], edited_value if edited_value is not None else c0["value"],
            confidence=c0.get("confidence") or 1.0, character=c0.get("character"),
            source=c0.get("source") or "candidate",
        )
    return cand_get(cid)


# ---------------------------------------------------------------- 时间线

def timeline_add(user_id: str, text: str, kind: str = "event",
                 character: str | None = None, session_id: str = "") -> str:
    tid = uid("e")
    with conn() as c:
        c.execute(
            "INSERT INTO timeline(id,user_id,character,kind,text,session_id,ts) VALUES(?,?,?,?,?,?,?)",
            (tid, user_id, character, kind, text, session_id, now()),
        )
    return tid


def timeline_list(user_id: str, limit: int = 50) -> list[dict]:
    with conn() as c:
        return _rows(c.execute(
            "SELECT * FROM timeline WHERE user_id=? ORDER BY ts DESC LIMIT ?",
            (user_id, int(limit)),
        ))


# ---------------------------------------------------------------- 角色宪法

def constitution_get(char_id: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM constitution WHERE char_id=? AND active=1 ORDER BY version DESC LIMIT 1",
            (char_id,),
        ).fetchone()
    return dict(row) if row else None


def constitution_versions(char_id: str) -> list[dict]:
    with conn() as c:
        return _rows(c.execute(
            "SELECT id,version,note,created_at,active FROM constitution "
            "WHERE char_id=? ORDER BY version DESC",
            (char_id,),
        ))


def constitution_set(char_id: str, text: str, note: str = "") -> dict:
    with conn() as c:
        row = c.execute("SELECT MAX(version) v FROM constitution WHERE char_id=?", (char_id,)).fetchone()
        ver = int(row["v"] or 0) + 1
        c.execute("UPDATE constitution SET active=0 WHERE char_id=?", (char_id,))
        cid = uid("k")
        c.execute(
            "INSERT INTO constitution(id,char_id,version,text,active,created_at,note) "
            "VALUES(?,?,?,?,1,?,?)",
            (cid, char_id, ver, text, now(), note),
        )
    return constitution_get(char_id) or {}


DEFAULT_CONSTITUTION = """你是一个有长期人格的聊天伙伴，不是通用助手。
原则：
1. 保持角色口吻与情绪连续性，不因话题变化而变成客服腔。
2. 不编造与用户相关的事实；不确定时用角色口吻承认不确定。
3. 用户明确表达的喜好可以被记住（会进入待确认队列），但你不能自行改写角色核心设定。
4. 不要输出内部字段名、任务 ID、URL 之外的系统信息。
5. 涉及图片/语音时，用自然语言描述结果，不要把链接说成技术细节。
"""


# ---------------------------------------------------------------- 身份绑定

def identity_bind(platform: str, platform_user: str, mobius_user: str) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO identities(platform,platform_user,mobius_user,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(platform,platform_user) DO UPDATE SET mobius_user=excluded.mobius_user",
            (platform, platform_user, mobius_user, now()),
        )


def identity_resolve(platform: str, platform_user: str) -> str:
    with conn() as c:
        row = c.execute(
            "SELECT mobius_user FROM identities WHERE platform=? AND platform_user=?",
            (platform, platform_user),
        ).fetchone()
    return row["mobius_user"] if row else f"{platform}:{platform_user}"


# ---------------------------------------------------------------- 角色档案

def load_characters() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not CHARS_DIR.exists():
        return out
    for p in sorted(CHARS_DIR.glob("*.json")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("id"):
                out[d["id"]] = d
        except Exception:
            continue
    return out


def _char_match(ch: dict, key: str) -> bool:
    keys = {str(ch.get("id", "")).lower(), str(ch.get("name", "")).lower()}
    keys |= {str(a).lower() for a in (ch.get("aliases") or [])}
    return key.lower() in keys


def get_character(key: str | None) -> dict | None:
    chars = load_characters()
    if not key:
        d = CFG.get("default_character")
        return chars.get(d) if d else (next(iter(chars.values())) if chars else None)
    if key in chars:
        return chars[key]
    for ch in chars.values():
        if _char_match(ch, key):
            return ch
    return None


def save_character(char_id: str, data: dict) -> dict:
    data = dict(data)
    data["id"] = char_id
    CHARS_DIR.mkdir(parents=True, exist_ok=True)
    path = CHARS_DIR / f"{char_id}.json"
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    return data


def constitution_text(char_id: str | None) -> str:
    if char_id:
        row = constitution_get(char_id)
        if row:
            return row["text"]
    ch = get_character(char_id)
    if ch and ch.get("constitution"):
        return str(ch["constitution"])
    return DEFAULT_CONSTITUTION


init_db()


# ---------------------------------------------------------------- Gateway 事件投影
_GW_STATUS = {
    "pending": "queued", "running": "running",
    "completed": "succeeded", "failed": "failed",
    "cancelled": "canceled", "canceled": "canceled",
    # ADR-002：submission_unknown 是独立终态，需人工确认，不得当作可重试的 failed
    "submission_unknown": "submission_unknown",
}


def map_gateway_status(status: str) -> str:
    """网关(ADR-002) → Core 状态词；未知词原样保留，便于排查而非静默吞掉。"""
    s = str(status or "")
    return _GW_STATUS.get(s, s)


def task_apply_event(event: dict) -> dict:
    """把 Gateway outbox 事件幂等投影到 Core 任务表（按 seq 去重）。"""
    seq = int(event.get("seq") or 0)
    last = int(kv_get("gateway_outbox_last_seq", 0) or 0)
    if seq and seq <= last:
        return {"duplicate": True, "seq": seq}
    payload = event.get("payload") or {}
    tid = str(payload.get("task_id") or event.get("task_id") or "")
    if not tid:
        return {"skipped": "no_task_id"}
    status = _GW_STATUS.get(str(payload.get("status") or ""), "running")
    existing = task_get(tid)
    now_ts = now()
    result = payload.get("result")
    error = payload.get("error_message")
    if existing is None:
        row = {
            "id": tid, "kind": str(payload.get("kind") or "image"), "status": status,
            "source": str(payload.get("source") or "gateway"),
            "user_id": payload.get("user_id"), "session_id": payload.get("session_id"),
            "character": payload.get("character"), "prompt": str(payload.get("prompt") or ""),
            "params": _dumps({"event_seq": seq}), "result": _dumps(result) if result is not None else None,
            "error": error, "remote_id": payload.get("remote_id"),
            "created_at": float(payload.get("created_at") or now_ts),
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "updated_at": now_ts,
        }
        with conn() as c:
            c.execute(
                f"INSERT INTO tasks({','.join(TASK_COLS)}) VALUES({','.join('?' * len(TASK_COLS))})",
                tuple(row[k] for k in TASK_COLS),
            )
    else:
        fields = {"status": status, "updated_at": now_ts}
        if result is not None:
            fields["result"] = result
        if error:
            fields["error"] = error
        if payload.get("finished_at"):
            fields["finished_at"] = payload["finished_at"]
        task_update(tid, **fields)
    if seq:
        kv_set("gateway_outbox_last_seq", seq)
    return {"applied": True, "seq": seq, "task_id": tid, "status": status}


# ============================================================================
# M1a：Core 权威任务生命周期（ADR-007）
# ----------------------------------------------------------------------------
# Core 是任务业务状态唯一 owner。以下统一用 ADR-002 规范词；历史词（queued/succeeded/
# canceled）经 normalize_task_status 归一，兼容既有行与旧摄入路径。旧 task_apply_event /
# _GW_STATUS 在 M1b 切换调用方前保持不变（本阶段纯增量，不改生产链路）。
# ============================================================================

TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_COMPLETED = "completed"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"
TASK_UNKNOWN = "submission_unknown"
TASK_STATES = (TASK_PENDING, TASK_RUNNING, TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED, TASK_UNKNOWN)
TASK_TERMINAL = frozenset({TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED, TASK_UNKNOWN})

# 历史/别名 → ADR-002 规范词
_TASK_STATUS_ALIASES = {
    "queued": TASK_PENDING, "succeeded": TASK_COMPLETED, "canceled": TASK_CANCELLED,
    "cancelled": TASK_CANCELLED, "completed": TASK_COMPLETED, "pending": TASK_PENDING,
    "running": TASK_RUNNING, "failed": TASK_FAILED, "submission_unknown": TASK_UNKNOWN,
}

# 合法转移（ADR-002 + ADR-007 §9：submission_unknown 仅经 24h 超时/人工确认转 failed）
# 注：同步任务（如 TTS 语音）无 running 阶段，允许 pending→completed 直接终态。
ALLOWED_TASK_TRANSITIONS = {
    TASK_PENDING: frozenset({TASK_RUNNING, TASK_COMPLETED, TASK_CANCELLED, TASK_FAILED, TASK_UNKNOWN}),
    TASK_RUNNING: frozenset({TASK_COMPLETED, TASK_FAILED, TASK_CANCELLED, TASK_UNKNOWN}),
    TASK_COMPLETED: frozenset(),
    TASK_FAILED: frozenset(),
    TASK_CANCELLED: frozenset(),
    TASK_UNKNOWN: frozenset({TASK_FAILED}),
}

# 不改变业务状态、只更新执行/租约字段的事件
_NON_STATUS_EVENTS = frozenset({"progress", "lease"})


def normalize_task_status(status) -> str:
    """任意历史/规范状态词 → ADR-002 规范词；未知词原样返回（便于排查，不静默吞）。"""
    s = str(status or "").strip().lower()
    return _TASK_STATUS_ALIASES.get(s, s)


def can_transition(src, dst) -> bool:
    """状态机合法性。同状态视为幂等 no-op（合法）。"""
    src, dst = normalize_task_status(src), normalize_task_status(dst)
    if src == dst:
        return True
    return dst in ALLOWED_TASK_TRANSITIONS.get(src, frozenset())


class TaskConflictError(Exception):
    """同 idempotency_key 但 payload 不同（→ HTTP 409）。"""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"idempotency_key 冲突：已存在任务 {task_id} 且 payload 不同")
        self.task_id = task_id


class IllegalTransitionError(Exception):
    """非法状态转移（拒绝写入）。"""

    def __init__(self, task_id: str, src: str, dst: str) -> None:
        super().__init__(f"任务 {task_id} 非法状态转移 {src} → {dst}")
        self.task_id, self.src, self.dst = task_id, src, dst


def payload_hash(payload) -> str:
    """payload 规范化 sha256（幂等冲突判定；字段顺序无关）。"""
    canonical = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_unique_violation(exc: Exception) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return "UNIQUE" in str(exc).upper()
    try:
        import psycopg
        return isinstance(exc, psycopg.errors.UniqueViolation)
    except Exception:  # noqa: BLE001
        return False


# 权威创建/写入用的完整列（含 M1a 新字段）
_AUTH_COLS = (
    "id", "kind", "status", "source", "user_id", "session_id", "character", "prompt",
    "params", "result", "error", "remote_id",
    "created_at", "started_at", "finished_at", "updated_at",
    "idempotency_key", "payload_hash", "payload_json", "execution_id", "error_code",
    "attempts", "lease_owner", "lease_expires_at", "cancel_requested", "version", "last_event_seq",
)


def task_create_authoritative(*, kind: str, payload: dict, idempotency_key: str = "",
                              source: str = "console", user_id: str | None = None,
                              session_id: str | None = None, character: str | None = None,
                              prompt: str = "") -> tuple[dict, bool]:
    """Core 权威创建 / 幂等命中。返回 (task, idempotent_hit)。task_id 由 Core 分配。

    - 同 key 同 payload_hash → 原任务，hit=True
    - 同 key 异 payload_hash → TaskConflictError（409）
    - 并发同 key 撞唯一索引 → 回滚重查后同上
    """
    phash = payload_hash(payload)
    key = (idempotency_key or "").strip()
    now_ts = now()
    result_id: str | None = None
    hit = False
    with conn() as c:
        if key:
            row = c.execute(
                "SELECT id, payload_hash FROM tasks WHERE idempotency_key=?", (key,)
            ).fetchone()
            if row is not None:
                if row["payload_hash"] == phash:
                    result_id, hit = row["id"], True
                else:
                    raise TaskConflictError(row["id"])
        if result_id is None:
            tid = uid("t")
            rec = {
                "id": tid, "kind": kind, "status": TASK_PENDING, "source": source,
                "user_id": user_id, "session_id": session_id, "character": character,
                "prompt": prompt, "params": _dumps({}), "result": None, "error": None,
                "remote_id": None, "created_at": now_ts, "started_at": None,
                "finished_at": None, "updated_at": now_ts,
                "idempotency_key": key or None, "payload_hash": phash,
                "payload_json": _dumps(payload), "execution_id": None, "error_code": None,
                "attempts": 0, "lease_owner": None, "lease_expires_at": None,
                "cancel_requested": 0, "version": 0, "last_event_seq": 0,
            }
            try:
                c.execute(
                    f"INSERT INTO tasks({','.join(_AUTH_COLS)}) "
                    f"VALUES({','.join('?' * len(_AUTH_COLS))})",
                    tuple(rec[k] for k in _AUTH_COLS),
                )
                result_id, hit = tid, False
            except Exception as exc:  # noqa: BLE001
                if key and _is_unique_violation(exc):
                    c.rollback()
                    row = c.execute(
                        "SELECT id, payload_hash FROM tasks WHERE idempotency_key=?", (key,)
                    ).fetchone()
                    if row is not None:
                        if row["payload_hash"] == phash:
                            result_id, hit = row["id"], True
                        else:
                            raise TaskConflictError(row["id"]) from exc
                    raise
                raise
    return task_get(result_id), hit


def task_apply_authoritative_event(task_id: str, *, event: str, seq: int = 0,
                                   execution_id: str | None = None,
                                   error_code: str | None = None,
                                   error_message: str | None = None,
                                   result=None, lease_owner: str | None = None,
                                   lease_expires_at: float | None = None,
                                   attempts: int | None = None) -> dict:
    """Core 权威事件写入：seq 幂等 + 状态机校验。Core 是唯一状态写入者。

    event ∈ running|completed|failed|cancelled|submission_unknown|progress|lease
    （历史词 succeeded/canceled 自动归一）。非法转移抛 IllegalTransitionError；
    任务不存在抛 KeyError。progress/lease 不改业务状态，只更新执行/租约字段。
    """
    target = normalize_task_status(event)
    now_ts = now()
    with conn() as c:
        row = c.execute(
            "SELECT id, status, last_event_seq, cancel_requested FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise KeyError(task_id)
        cur = normalize_task_status(row["status"])
        last_seq = int(row["last_event_seq"] or 0)
        if seq and seq <= last_seq:
            return {"duplicate": True, "seq": seq, "task_id": task_id, "status": cur}

        sets: list[str] = ["updated_at=?", "version=version+1"]
        args: list[Any] = [now_ts]
        if seq:
            sets.append("last_event_seq=?"); args.append(seq)
        if execution_id is not None:
            sets.append("execution_id=?"); args.append(execution_id)
        if attempts is not None:
            sets.append("attempts=?"); args.append(int(attempts))

        if target in _NON_STATUS_EVENTS:
            # 进度/租约续约：不动业务状态
            if lease_owner is not None:
                sets.append("lease_owner=?"); args.append(lease_owner)
            if lease_expires_at is not None:
                sets.append("lease_expires_at=?"); args.append(lease_expires_at)
            args.append(task_id)
            c.execute(f"UPDATE tasks SET {','.join(sets)} WHERE id=?", tuple(args))
            return {"ok": True, "task_id": task_id, "status": cur, "seq": seq, "progress": True}

        if not can_transition(cur, target):
            raise IllegalTransitionError(task_id, cur, target)

        sets.append("status=?"); args.append(target)
        if error_code is not None:
            sets.append("error_code=?"); args.append(error_code)
        if error_message is not None:
            sets.append("error=?"); args.append(error_message)
        if result is not None:
            sets.append("result=?"); args.append(_dumps(result))
        if lease_owner is not None:
            sets.append("lease_owner=?"); args.append(lease_owner)
        if lease_expires_at is not None:
            sets.append("lease_expires_at=?"); args.append(lease_expires_at)
        if target == TASK_RUNNING:
            sets.append("started_at=COALESCE(started_at, ?)"); args.append(now_ts)
        if target in TASK_TERMINAL:
            sets.append("finished_at=?"); args.append(now_ts)
            sets.append("lease_owner=NULL"); sets.append("lease_expires_at=NULL")
        args.append(task_id)
        c.execute(f"UPDATE tasks SET {','.join(sets)} WHERE id=?", tuple(args))
    return {"ok": True, "task_id": task_id, "status": target, "seq": seq}


def task_request_cancel(task_id: str) -> dict:
    """写 cancel_requested=1（终态由执行端确认）。已是终态则 no-op。

    ADR-007 §2.4：完成先于取消到达 → completed + cancel_requested=true，不引入第二终态。
    """
    with conn() as c:
        row = c.execute("SELECT id, status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        cur = normalize_task_status(row["status"])
        if cur in TASK_TERMINAL:
            return {"ok": True, "task_id": task_id, "status": cur, "note": "已终态，无需取消"}
        c.execute(
            "UPDATE tasks SET cancel_requested=1, updated_at=?, version=version+1 WHERE id=?",
            (now(), task_id),
        )
    return {"ok": True, "task_id": task_id, "status": cur, "cancel_requested": True}


def task_renew_lease(task_id: str, owner: str, ttl_sec: float) -> dict:
    """执行器续约：仅 running 任务可续约，刷新 lease_owner/lease_expires_at。"""
    expires = now() + float(ttl_sec)
    with conn() as c:
        row = c.execute("SELECT id, status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        cur = normalize_task_status(row["status"])
        if cur != TASK_RUNNING:
            return {"ok": False, "task_id": task_id, "status": cur, "reason": "not_running"}
        c.execute(
            "UPDATE tasks SET lease_owner=?, lease_expires_at=?, updated_at=? WHERE id=?",
            (owner, expires, now(), task_id),
        )
    return {"ok": True, "task_id": task_id, "lease_expires_at": expires}


def task_expire_stale_leases(grace_sec: float = 0.0) -> list[str]:
    """把租约过期的 running 任务置 failed(lease_expired)。返回被置失败的 task_id 列表。

    ADR-007 §9.2：过期由 Core 置 failed(lease_expired)，禁止永久 running。
    """
    cutoff = now() - float(grace_sec)
    expired: list[str] = []
    with conn() as c:
        rows = c.execute(
            "SELECT id FROM tasks WHERE status=? AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at < ?",
            (TASK_RUNNING, cutoff),
        ).fetchall()
        for r in rows:
            expired.append(r["id"])
    for tid in expired:
        try:
            task_apply_authoritative_event(
                tid, event=TASK_FAILED, error_code="lease_expired",
                error_message="执行租约过期，Core 置失败（禁止永久 running）",
            )
        except (IllegalTransitionError, KeyError):
            continue
    return expired


def task_expire_stale_unknown(max_age_sec: float = 24 * 3600) -> list[str]:
    """submission_unknown 超过 max_age_sec 未人工确认 → failed(submission_unknown_timeout)。

    ADR-007 §9.3：保留人工确认窗口；超时自动收敛，Console/审计保留原因。返回被收敛的 task_id。
    """
    cutoff = now() - float(max_age_sec)
    stale: list[str] = []
    with conn() as c:
        rows = c.execute(
            "SELECT id FROM tasks WHERE status=? AND COALESCE(updated_at, created_at) < ?",
            (TASK_UNKNOWN, cutoff),
        ).fetchall()
        for r in rows:
            stale.append(r["id"])
    for tid in stale:
        try:
            task_apply_authoritative_event(
                tid, event=TASK_FAILED, error_code="submission_unknown_timeout",
                error_message="submission_unknown 超过 24h 未人工确认，自动收敛为 failed",
            )
        except (IllegalTransitionError, KeyError):
            continue
    return stale
