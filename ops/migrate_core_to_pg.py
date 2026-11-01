"""Core 数据迁移：mobius.db(SQLite) → PostgreSQL（阶段 3）。

表：tasks / audit / turns / user_memory / memory_candidates / timeline /
    constitution / kv / identities

用法：
  python ops/migrate_core_to_pg.py --source data/mobius.db --dsn postgresql://.../mobius_core
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import psycopg
from psycopg.rows import dict_row

TABLES = ["tasks", "audit", "turns", "user_memory", "memory_candidates",
          "timeline", "constitution", "kv", "identities"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--dsn", required=True)
    args = ap.parse_args()

    src = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    with psycopg.connect(args.dsn, row_factory=dict_row) as dst:
        with dst.cursor() as cur:
            for table in TABLES:
                cur.execute(f'TRUNCATE TABLE "{table}"')
            for table in TABLES:
                src_cols = [r[1] for r in src.execute(f'pragma table_info("{table}")')]
                if not src_cols:
                    print(f"{table}: 源无此表，跳过")
                    continue
                cur.execute(
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_schema='public' AND table_name=%s", (table,))
                dst_cols = {r["column_name"] for r in cur.fetchall()}
                cols = [c for c in src_cols if c in dst_cols]
                rows = src.execute(
                    f'SELECT {",".join(chr(34)+c+chr(34) for c in cols)} FROM "{table}"').fetchall()
                if rows:
                    cur.executemany(
                        f'INSERT INTO "{table}" ({",".join(chr(34)+c+chr(34) for c in cols)})'
                        f' VALUES ({",".join(["%s"] * len(cols))})',
                        [tuple(r[c] for c in cols) for r in rows])
                print(f"{table:22} 导入 {len(rows)}")
        dst.commit()
        ok = True
        for table in TABLES:
            n_src = src.execute(f'select count(*) from "{table}"').fetchone()[0]
            n_dst = dst.execute(f'select count(*) AS n from "{table}"').fetchone()["n"]
            flag = "✅" if n_src == n_dst else "❌"
            print(f"  校验 {table:20} 源 {n_src:>5} / PG {n_dst:>5} {flag}")
            ok = ok and n_src == n_dst
    src.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
