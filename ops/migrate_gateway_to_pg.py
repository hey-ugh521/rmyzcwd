"""Gateway 任务数据迁移：SQLite tasks.db → PostgreSQL（阶段 2）。

只迁移 task_snapshot / outbox / schema_meta 三张表；目标 schema 由 PgTaskStore.ensure_schema 创建。

用法：
  python ops/migrate_gateway_to_pg.py --source media_gateway/data/tasks.db --dsn postgresql://...
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys

import psycopg
from psycopg.rows import dict_row

TABLES = ["schema_meta", "task_snapshot", "outbox"]
SERIAL_COLUMNS = {"outbox": "seq"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--dsn", required=True)
    args = ap.parse_args()

    src = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    with psycopg.connect(args.dsn, row_factory=dict_row, autocommit=False) as dst:
        with dst.cursor() as cur:
            # 先清空（outbox 序列重置），再逐表导入
            cur.execute("TRUNCATE task_snapshot, outbox RESTART IDENTITY")
            cur.execute("DELETE FROM schema_meta")
            report = []
            for table in TABLES:
                src_cols = [r[1] for r in src.execute(f'pragma table_info("{table}")')]
                if not src_cols:
                    report.append((table, 0, "SKIP(源无此表)"))
                    continue
                cur.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema='public' AND table_name=%s",
                    (table,),
                )
                dst_cols = {r["column_name"] for r in cur.fetchall()}
                cols = [c for c in src_cols if c in dst_cols]
                rows = src.execute(
                    f'SELECT {",".join(chr(34)+c+chr(34) for c in cols)} FROM "{table}"'
                ).fetchall()
                if rows:
                    placeholders = ",".join(["%s"] * len(cols))
                    col_list = ",".join(f'"{c}"' for c in cols)
                    cur.executemany(
                        f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})',
                        [tuple(r[c] for c in cols) for r in rows],
                    )
                report.append((table, len(rows), "OK"))
                seq_col = SERIAL_COLUMNS.get(table)
                if seq_col and rows:
                    cur.execute(
                        f"SELECT setval(pg_get_serial_sequence('{table}', '{seq_col}'),"
                        f" COALESCE((SELECT MAX({seq_col}) FROM {table}), 1))"
                    )
            dst.commit()

            print(f"{'表':16} {'行数':>6} 状态")
            for table, n, status in report:
                print(f"{table:16} {n:>6} {status}")
            # 校验
            for table, n, _ in report:
                actual = dst.execute(f'SELECT COUNT(*) AS n FROM "{table}"').fetchone()["n"]
                flag = "✅" if actual == n else "❌"
                print(f"  校验 {table}: 源 {n} / PG {actual} {flag}")
                if actual != n:
                    return 1
    src.close()
    print("迁移完成，逐表一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
