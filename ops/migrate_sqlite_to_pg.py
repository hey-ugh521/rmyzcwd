"""SQLite → PostgreSQL 数据迁移（LangBot 库）。

用法：
  python ops/migrate_sqlite_to_pg.py --source <langbot.db> --dsn postgresql://... --database langbot_rehearsal [--dry-run]

策略：
- 目标 schema 由 LangBot 自己（alembic + create_tables）建好，本脚本只搬数据
- 关闭外键校验导入（session_replication_role=replica）
- 类型适配：boolean / jsonb / ARRAY / bytea / 时间戳（会话时区 UTC）
- 导入后重置序列
- 输出逐表行数比对（差异表会标记）
"""

from __future__ import annotations

import argparse
import io
import json
import sqlite3
import sys
import urllib.parse

import psycopg
from psycopg.types.json import Jsonb


def pg_columns(conn, table: str) -> list[tuple[str, str, str]]:
    rows = conn.execute(
        """
        select column_name, data_type, udt_name
        from information_schema.columns
        where table_schema = 'public' and table_name = %s
        order by ordinal_position
        """,
        (table,),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def sqlite_tables(path: str) -> list[str]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [
            r[0]
            for r in con.execute(
                "select name from sqlite_master where type='table'"
                " and name not like 'sqlite_%' and name != 'alembic_version' order by name"
            )
        ]
    finally:
        con.close()
    return tables


def adapt(value, data_type: str, udt: str):
    if value is None:
        return None
    if data_type == "boolean":
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "t", "true", "yes", "y")
        return bool(value)
    if udt in ("json", "jsonb"):
        if isinstance(value, (list, dict)):
            return Jsonb(value)
        if isinstance(value, (bytes, bytearray)):
            value = value.decode("utf-8", "replace")
        return Jsonb(json.loads(value)) if value.strip() else None
    if data_type == "ARRAY":
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                return None
        return [value]
    return value


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--dsn", required=True, help="postgresql://user:pass@host:port/db")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = sqlite3.connect(f"file:{args.source}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    src_tables = sqlite_tables(args.source)

    with psycopg.connect(args.dsn, autocommit=False) as dst:
        dst.execute("SET session_replication_role = replica")
        dst.execute("SET TIME ZONE 'UTC'")
        dst_tables = {
            r[0]
            for r in dst.execute(
                "select table_name from information_schema.tables where table_schema='public'"
            )
        }

        # 先统一 TRUNCATE（CASCADE 会连带清空引用表，所以必须在插入前一次做完）
        if not args.dry_run:
            for table in src_tables:
                if table in dst_tables:
                    dst.execute(f'TRUNCATE TABLE "{table}" CASCADE')
            dst.commit()

        reordered = []  # 全部导入后统一重置序列
        report = []
        for table in src_tables:
            if table not in dst_tables:
                report.append((table, -1, -1, "SKIP(目标无此表)"))
                continue
            sqlite_cols = [r[1] for r in src.execute(f'pragma table_info("{table}")')]
            pg_cols = pg_columns(dst, table)
            pg_names = [c[0] for c in pg_cols]
            cols = [c for c in sqlite_cols if c in pg_names]
            if not cols:
                report.append((table, -1, -1, "SKIP(无公共列)"))
                continue
            rows = src.execute(f'select {",".join(chr(34)+c+chr(34) for c in cols)} from "{table}"').fetchall()
            if args.dry_run:
                report.append((table, len(rows), 0, "DRY-RUN"))
                continue
            placeholders = ",".join(["%s"] * len(cols))
            col_list = ",".join(f'"{c}"' for c in cols)
            sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'
            type_map = {c[0]: (c[1], c[2]) for c in pg_cols}
            payload = []
            for row in rows:
                payload.append(
                    tuple(adapt(row[c], type_map[c][0], type_map[c][1]) for c in cols)
                )
            if payload:
                with dst.cursor() as cur:
                    cur.executemany(sql, payload)
            report.append((table, len(rows), len(payload), "OK"))
            if "id" in cols:
                reordered.append(table)

        if not args.dry_run:
            for table in reordered:
                id_col = next((c for c in pg_columns(dst, table) if c[0] == 'id'), None)
                if id_col is None or id_col[1] not in ('integer', 'bigint', 'smallint'):
                    continue
                dst.execute(
                    f"""
                    SELECT setval(
                        pg_get_serial_sequence('"{table}"', 'id'),
                        COALESCE((SELECT MAX(id) FROM "{table}"), 1),
                        COALESCE((SELECT MAX(id) FROM "{table}"), 1) > 0
                    )
                    """
                )
            dst.commit()

        print(f"{'表':34} {'源行数':>7} {'导入':>7} 状态")
        total_src = total_dst = 0
        mismatches = []
        for table, n_src, n_dst, status in report:
            if n_src >= 0:
                total_src += n_src
            if n_dst >= 0:
                total_dst += n_dst
            print(f"{table:34} {n_src:>7} {n_dst:>7} {status}")
            if status == "OK":
                actual = dst.execute(f'select count(*) from "{table}"').fetchone()[0]
                if actual != n_src:
                    mismatches.append((table, n_src, actual))
        print(f"\n源总行数 {total_src} / 导入 {total_dst}")
        if mismatches:
            print("行数不一致的表：")
            for t, a, b in mismatches:
                print(f"  {t}: 源 {a} / 目标 {b}")
            return 1
        print("逐表行数一致 ✅")
    src.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
