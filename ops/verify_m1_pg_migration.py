"""退回项#4：PG 旧库迁移验证（结构 dump + 合成行，不复制生产数据）。

流程：生产 mobius_core 只导 tasks 表结构（--schema-only，无行数据）→ scratch 库
mobius_m1_mig → 插入合成旧行（16 列、旧状态词 succeeded/canceled/queued）→ 跑真实迁移
（import core.store，CORE_DB_BACKEND=pg，init_db→_migrate_tasks ALTER 补列）→ 验证
schema_version=2 / 新列齐 / 旧行可读且归一 / 唯一索引对 NULL 幂等键不冲突 → 删 scratch。

口令只读自 core.secrets，经 PGPASSWORD 传子进程，绝不打印。用完删库。
运行：D:\\AI\\qqbot-mcp\\Scripts\\python.exe <this>
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)
PGBIN = os.environ.get("MOBIUS_PGBIN", r"D:\AI\pgsql-portable\pgsql\bin")
SCRATCH = os.environ.get("MOBIUS_M1_MIGRATION_SCRATCH", "mobius_m1_mig")
HOST, PORT, USER = "127.0.0.1", os.environ.get("MOBIUS_PG_PORT", "55432"), "postgres"

sys.path.insert(0, ROOT)
for k in list(os.environ):
    if k.lower().endswith("_proxy"):
        os.environ.pop(k, None)
# 只读生产 secrets 取 pg 口令（clone config 的 secrets_file 指向不存在的 data/secrets.json，
# 会盖掉默认回退，故此处显式指向生产 secrets 文件；口令绝不打印）
os.environ["MOBIUS_SECRETS_FILE"] = r"D:\AI\mobius\secrets.json"
from core import secrets  # noqa: E402
import psycopg  # noqa: E402

PW = secrets.get("pg", "password")
assert PW, "secrets 无 pg.password"
DSN_ADMIN = f"postgresql://{USER}:{PW}@{HOST}:{PORT}/postgres"
DSN_SCRATCH = f"postgresql://{USER}:{PW}@{HOST}:{PORT}/{SCRATCH}"
PASSED, FAILED = [], []


def check(name, ok, detail=""):
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def _env():
    e = dict(os.environ)
    e["PGPASSWORD"] = PW
    return e


def main() -> int:
    print("==> PG 旧库迁移验证（结构 dump + 合成行）")
    tmp = Path(tempfile.mkdtemp(prefix="m1-mig-"))
    dump = tmp / "tasks_schema.sql"
    # 1) 建 scratch
    with psycopg.connect(DSN_ADMIN, autocommit=True, connect_timeout=8) as con:
        con.execute(f"DROP DATABASE IF EXISTS {SCRATCH}")
        con.execute(f"CREATE DATABASE {SCRATCH}")
    print("  · scratch 库已建")
    # 2) 只导生产 tasks 表结构（无数据）
    r = subprocess.run([os.path.join(PGBIN, "pg_dump.exe"), "--schema-only", "--no-owner",
                        "--no-privileges", "-t", "tasks", "-h", HOST, "-p", PORT, "-U", USER,
                        "-f", str(dump), "mobius_core"], env=_env(), capture_output=True, text=True)
    if r.returncode != 0 or not dump.is_file():
        print("  FAIL pg_dump 失败:", (r.stderr or "")[:200])
        return 1
    check("生产 tasks 结构已导出（无行数据）", dump.is_file() and dump.stat().st_size > 0,
          f"{dump.stat().st_size}B")
    dumped = dump.read_text(encoding="utf-8", errors="ignore")
    check("导出为旧 16 列结构（无 idempotency_key）", "idempotency_key" not in dumped)
    # 3) 载入 scratch
    r2 = subprocess.run([os.path.join(PGBIN, "psql.exe"), "-h", HOST, "-p", PORT, "-U", USER,
                         "-d", SCRATCH, "-v", "ON_ERROR_STOP=1", "-f", str(dump)],
                        env=_env(), capture_output=True, text=True)
    check("旧结构载入 scratch 成功", r2.returncode == 0, (r2.stderr or "")[:200])
    # 4) 插入合成旧行（旧状态词）
    with psycopg.connect(DSN_SCRATCH, autocommit=True, connect_timeout=8) as con:
        con.execute("INSERT INTO tasks(id,kind,status,source,user_id,session_id,character,prompt,"
                    "params,result,error,remote_id,created_at,started_at,finished_at,updated_at) "
                    "VALUES ('old_s','image','succeeded','qq','u','s',NULL,'p','{}','{\"f\":\"a.png\"}',NULL,'g_1',1,2,3,3),"
                    "        ('old_c','image','canceled','qq','u','s',NULL,'p','{}',NULL,NULL,'g_2',1,2,3,3),"
                    "        ('old_q','image','queued','qq','u','s',NULL,'p','{}',NULL,NULL,NULL,1,0,0,1)")
        cols_before = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='tasks'")}
    check("迁移前为旧 16 列", "idempotency_key" not in cols_before and "version" not in cols_before,
          f"{len(cols_before)} 列")
    # 5) 跑真实迁移：import core.store（CORE_DB_BACKEND=pg, CORE_DB_DSN=scratch）→ init_db→_migrate_tasks
    os.environ["CORE_DB_BACKEND"] = "pg"
    os.environ["CORE_DB_DSN"] = DSN_SCRATCH
    os.environ["MOBIUS_SECRETS_FILE"] = str(tmp / "absent.json")
    from core import store  # noqa: E402  (导入即 init_db → 迁移)
    with psycopg.connect(DSN_SCRATCH, connect_timeout=8) as con:
        ver = con.execute("SELECT v FROM schema_meta WHERE k='schema_version'").fetchone()
        cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='tasks'")}
        need = ["idempotency_key", "payload_hash", "payload_json", "execution_id", "error_code",
                "attempts", "lease_owner", "lease_expires_at", "cancel_requested", "version",
                "last_event_seq"]
        check("迁移后 schema_version=2", ver is not None and str(ver[0]) == "2", str(ver))
        miss = [c for c in need if c not in cols]
        check("旧库 ALTER 补齐全部权威列", not miss, f"缺={miss}")
        # 旧行保留 + 新列默认值
        row = con.execute("SELECT status, version, attempts, cancel_requested FROM tasks WHERE id='old_s'").fetchone()
        check("旧行保留、新列有默认值", row[0] == "succeeded" and row[1] == 0 and row[3] == 0, str(row))
        # 唯一索引对多个 NULL 幂等键不冲突
        con.execute("INSERT INTO tasks(id,kind,status,created_at,updated_at) VALUES('n1','image','pending',1,1)")
        con.execute("INSERT INTO tasks(id,kind,status,created_at,updated_at) VALUES('n2','image','pending',1,1)")
        con.commit()
        n_null = con.execute("SELECT COUNT(*) FROM tasks WHERE idempotency_key IS NULL").fetchone()[0]
        check("唯一索引允许多个 NULL 幂等键（旧行不冲突）", n_null >= 5, f"null_keys={n_null}")
    # 6) 旧行读时归一（store 层）
    gs = store.task_get("old_s")
    check("旧行 succeeded 经 API 层可读且归一为 completed",
          gs is not None and store.normalize_task_status(gs["status"]) == "completed",
          gs and gs["status"])
    # 7) 清理
    with psycopg.connect(DSN_ADMIN, autocommit=True, connect_timeout=8) as con:
        con.execute(f"DROP DATABASE IF EXISTS {SCRATCH}")
    print("  · scratch 库已删")
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    for n, d in FAILED:
        print(f"  FAIL: {n} {d}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
