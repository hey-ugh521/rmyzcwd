-- M1a：Core 任务权威字段 schema v2（ADR-007 任务唯一 owner = Core）
-- 由 core/store.py 的 init_db() -> _migrate_tasks() 幂等执行；本文件仅作留档 / 手工迁移用。
--
-- 语义：
--   * 新库：core/store.py 的 SCHEMA 直接建全列，_migrate_tasks 只补唯一索引并写 schema_version。
--   * 旧库：CREATE TABLE IF NOT EXISTS 不改既有表，_migrate_tasks 逐列 ALTER ADD COLUMN 补齐。
--   * 既有行 idempotency_key 为 NULL；PG 与 SQLite 的唯一索引均允许多个 NULL，故不冲突。
--   * 双后端：DB_BACKEND=sqlite|pg，占位符 ? 由 _PgConnection 适配为 %s。
--
-- schema_version：v1=原始 16 列；v2=本次任务权威字段。记录于 schema_meta(k,v)。

CREATE TABLE IF NOT EXISTS schema_meta (
  k TEXT PRIMARY KEY,
  v TEXT
);

-- 逐列补齐（_migrate_tasks 仅在列缺失时执行对应 ALTER）：
ALTER TABLE tasks ADD COLUMN idempotency_key TEXT;
ALTER TABLE tasks ADD COLUMN payload_hash    TEXT;
ALTER TABLE tasks ADD COLUMN payload_json    TEXT;
ALTER TABLE tasks ADD COLUMN execution_id    TEXT;
ALTER TABLE tasks ADD COLUMN error_code      TEXT;
ALTER TABLE tasks ADD COLUMN attempts        INTEGER DEFAULT 0;
ALTER TABLE tasks ADD COLUMN lease_owner     TEXT;
ALTER TABLE tasks ADD COLUMN lease_expires_at REAL;
ALTER TABLE tasks ADD COLUMN cancel_requested INTEGER DEFAULT 0;
ALTER TABLE tasks ADD COLUMN version         INTEGER DEFAULT 0;
ALTER TABLE tasks ADD COLUMN last_event_seq  INTEGER DEFAULT 0;

-- 幂等键唯一索引（Core 保证 idempotency_key 唯一性；ADR-007 §2.6）
CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_idem_key ON tasks(idempotency_key);

-- 记录版本
-- INSERT INTO schema_meta(k, v) VALUES('schema_version', '2');
