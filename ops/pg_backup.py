"""PostgreSQL 备份与恢复演练（阶段 4）。

用法：
  python ops/pg_backup.py --backup           # 备份 3 个库（保留 7 天）
  python ops/pg_backup.py --drill            # 用最新 dump 恢复到临时库并校验
  python ops/pg_backup.py --list             # 列出备份

实现：通过 `docker exec mobius-pg pg_dump` 导出，文件落在 D:/AI/mobius/pgbackup。
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import os
import subprocess
import sys

CONTAINER = "mobius-pg"
DBS = ["langbot", "mobius", "mobius_core"]
BACKUP_DIR = r"D:\AI\mobius\pgbackup"
KEEP_DAYS = 7


def _pg_password() -> str:
    env_file = r"D:\AI\mobius\.env"
    if os.path.exists(env_file):
        for line in open(env_file, encoding="utf-8"):
            if line.startswith("PG_PASSWORD="):
                return line.split("=", 1)[1].strip()
    return os.environ.get("PG_PASSWORD", "")


def _env() -> dict:
    env = dict(os.environ)
    env["PGPASSWORD"] = _pg_password()
    return env


def backup() -> int:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    ok = True
    for db in DBS:
        out = os.path.join(BACKUP_DIR, f"{db}-{ts}.dump")
        with open(out, "wb") as fh:
            r = subprocess.run(
                ["docker", "exec", "-e", f"PGPASSWORD={_pg_password()}", CONTAINER,
                 "pg_dump", "-U", "postgres", "-Fc", "-d", db],
                stdout=fh, stderr=subprocess.PIPE,
            )
        size = os.path.getsize(out) if os.path.exists(out) else 0
        status = "OK" if r.returncode == 0 and size > 0 else f"FAIL({r.returncode})"
        print(f"  {db:14} {size/1024:8.1f} KB  {status}")
        ok = ok and status == "OK"
        if status != "OK":
            print("   stderr:", (r.stderr or b"").decode("utf-8", "replace")[:200])
    # 保留策略
    cutoff = dt.datetime.now().timestamp() - KEEP_DAYS * 86400
    for path in glob.glob(os.path.join(BACKUP_DIR, "*.dump")):
        if os.path.getmtime(path) < cutoff:
            os.remove(path)
            print(f"  删除过期备份: {os.path.basename(path)}")
    return 0 if ok else 1


def _latest(db: str) -> str | None:
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, f"{db}-*.dump")))
    return files[-1] if files else None


def drill() -> int:
    env = _env()
    ok = True
    for db in DBS:
        dump = _latest(db)
        if dump is None:
            print(f"  {db}: 无备份，跳过")
            ok = False
            continue
        tmp_db = f"{db}_restore_test"
        r = subprocess.run(
            ["docker", "exec", "-e", f"PGPASSWORD={env['PGPASSWORD']}", CONTAINER,
             "psql", "-U", "postgres", "-d", "postgres", "-c", f'DROP DATABASE IF EXISTS "{tmp_db}";'],
            capture_output=True, text=True)
        r = subprocess.run(
            ["docker", "exec", "-e", f"PGPASSWORD={env['PGPASSWORD']}", CONTAINER,
             "psql", "-U", "postgres", "-d", "postgres", "-c", f'CREATE DATABASE "{tmp_db}";'],
            capture_output=True, text=True)
        # 把 dump 拷进容器再 restore（Windows 下不能用 /dev/stdin）
        subprocess.run(["docker", "cp", dump, f"{CONTAINER}:/tmp/restore.dump"],
                       capture_output=True, text=True)
        r = subprocess.run(
            ["docker", "exec", "-e", f"PGPASSWORD={env['PGPASSWORD']}", CONTAINER,
             "pg_restore", "-U", "postgres", "-d", tmp_db, "/tmp/restore.dump"],
            capture_output=True, text=True)
        q = subprocess.run(
            ["docker", "exec", "-e", f"PGPASSWORD={env['PGPASSWORD']}", CONTAINER,
             "psql", "-U", "postgres", "-d", tmp_db, "-t", "-c",
             "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema='public')"],
            capture_output=True, text=True)
        tables = (q.stdout or "").strip()
        status = "OK" if r.returncode == 0 and tables.isdigit() and int(tables) > 0 else f"FAIL({r.returncode})"
        print(f"  {db:14} -> {tmp_db:20} 表数={tables}  {status}")
        if status != "OK":
            print("   stderr:", (r.stderr or "")[:200])
            ok = False
        subprocess.run(
            ["docker", "exec", "-e", f"PGPASSWORD={env['PGPASSWORD']}", CONTAINER,
             "psql", "-U", "postgres", "-d", "postgres", "-c", f'DROP DATABASE IF EXISTS "{tmp_db}";'],
            capture_output=True)
    return 0 if ok else 1


def list_backups() -> int:
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, "*.dump")))
    for f in files[-20:]:
        print(f"  {os.path.basename(f):40} {os.path.getsize(f)/1024:8.1f} KB")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--drill", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.backup:
        sys.exit(backup())
    if args.drill:
        sys.exit(drill())
    if args.list:
        sys.exit(list_backups())
    print("用法: --backup | --drill | --list")
