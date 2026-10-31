-- P0.3 任务持久化 schema v1
-- 由 media_gateway/taskstore.py ensure_schema() 执行；本文件仅作留档/手工迁移用

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

INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version','1');
