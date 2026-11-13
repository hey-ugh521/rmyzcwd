"""Diagnostic probe for cross-process SQLite WAL startup.

This is intentionally NOT part of the unit suite or CI: current policy only
serializes connection creation inside one process. A failing probe documents
the known cross-process gap instead of hiding it behind a green test.

Run: python tests/probe_store_multiprocess.py
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _child(db_path: str, i: int, barrier, q) -> None:
    from core import store

    store.DB_BACKEND = "sqlite"
    store.DB_PATH = Path(db_path)
    try:
        store.init_db()
        barrier.wait()
        c = store.conn()
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT OR REPLACE INTO kv(k,v,updated_at) VALUES(?,?,?)",
                  (f"proc-{i}", "ok", 1.0))
        c.commit()
        c.close()
        q.put(None)
    except Exception as exc:  # noqa: BLE001
        q.put(f"{type(exc).__name__}: {exc}")


def main() -> int:
    os.environ["CORE_DB_BACKEND"] = "sqlite"
    from core import store

    failures = 0
    for round_no in range(3):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "multi.db")
            store.DB_BACKEND = "sqlite"
            store.DB_PATH = Path(db)
            store.init_db()
            store.close_anchor()
            ctx = mp.get_context("spawn")
            barrier = ctx.Barrier(4)
            q = ctx.Queue()
            procs = [ctx.Process(target=_child, args=(db, i, barrier, q)) for i in range(4)]
            for p in procs:
                p.start()
            for p in procs:
                p.join(10)
            errors = [q.get() for _ in procs]
            bad = [e for e in errors if e]
            print(f"round {round_no + 1}: errors={len(bad)} {bad[:2]}")
            failures += len(bad)
    print("RESULT:", "PASS" if failures == 0 else f"FAIL ({failures} readonly/errors)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
