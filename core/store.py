"""Möbius Core —— 配置、存储、角色档案。全系统唯一事实来源。

本模块不依赖 FastAPI/httpx，可被独立调用与测试。
"""
from __future__ import annotations

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
  created_at REAL, started_at REAL, finished_at REAL, updated_at REAL
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


def init_db() -> None:
    with _lock:
        c = conn()
        try:
            script = SCHEMA
            if DB_BACKEND == "pg":
                script = script.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
            c.executescript(script)
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
    sql = "SELECT * FROM tasks WHERE status IN ('queued','running')"
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
    out = {"succeeded": 0, "failed": 0, "canceled": 0, "running": 0, "queued": 0}
    for r in rows:
        out[r["status"]] = r["n"]
    out["total"] = sum(out.values())
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
    "submission_unknown": "failed",
}


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
