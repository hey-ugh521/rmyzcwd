"""Cross-process SQLite WAL startup probe.

Used by tests/test_store_multiprocess.py and runnable directly:
    python tests/probe_store_multiprocess.py
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NPROC = 4
ROUNDS = 3
BARRIER_TIMEOUT = 12
CHILD_TIMEOUT = 30


def _child(db_path: str, i: int, barrier, q) -> None:
    from core import store

    store.DB_BACKEND = "sqlite"
    store.DB_PATH = Path(db_path)
    try:
        if os.environ.get("MOBIUS_PROBE_FAIL_CHILD") == str(i):
            raise RuntimeError("injected child failure")
        store.init_db()
        barrier.wait(timeout=BARRIER_TIMEOUT)
        c = store.conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT OR REPLACE INTO kv(k,v,updated_at) VALUES(?,?,?)",
                      (f"proc-{i}", "ok", 1.0))
            c.commit()
        finally:
            c.close()
        q.put((i, None))
    except BaseException as exc:  # noqa: BLE001
        # Always report, including init_db() failures before the barrier.
        try:
            q.put((i, f"{type(exc).__name__}: {exc}"))
        except Exception:  # noqa: BLE001
            pass
        # Non-zero exit lets the parent abort the barrier immediately instead
        # of waiting for every sibling's barrier timeout.
        raise SystemExit(1)
    finally:
        try:
            store.close_anchor()
        except Exception:  # noqa: BLE001
            pass


def _stop_processes(procs, barrier) -> None:
    deadline = time.time() + CHILD_TIMEOUT
    while time.time() < deadline and any(p.is_alive() for p in procs):
        # If a child failed before reaching the barrier, wake the others
        # immediately instead of waiting for the barrier timeout.
        if any(p.exitcode not in (None, 0) for p in procs):
            barrier.abort()
            for p in procs:
                if p.is_alive():
                    p.terminate()
            break
        for p in procs:
            p.join(0.05)
    for p in procs:
        if p.is_alive():
            p.terminate()
    for p in procs:
        p.join(5)
        if p.is_alive() and hasattr(p, "kill"):
            p.kill()
            p.join(2)


def main() -> int:
    os.environ["CORE_DB_BACKEND"] = "sqlite"
    from core import store

    failures = 0
    for round_no in range(ROUNDS):
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / "multi.db")
            store.DB_BACKEND = "sqlite"
            store.DB_PATH = Path(db)
            store.init_db()
            store.close_anchor()

            ctx = mp.get_context("spawn")
            barrier = ctx.Barrier(NPROC)
            q = ctx.Queue()
            procs = [ctx.Process(target=_child, args=(db, i, barrier, q))
                     for i in range(NPROC)]
            for p in procs:
                p.start()

            _stop_processes(procs, barrier)
            if any(p.exitcode != 0 for p in procs):
                barrier.abort()

            results: dict[int, str | None] = {}
            deadline = time.time() + 5
            while len(results) < NPROC and time.time() < deadline:
                try:
                    idx, err = q.get(timeout=0.2)
                except queue.Empty:
                    break
                if isinstance(idx, int):
                    results[idx] = err
            q.close()

            bad: list[str] = []
            for i, p in enumerate(procs):
                if i not in results:
                    bad.append(f"child {i}: no result (exitcode={p.exitcode})")
                elif results[i]:
                    bad.append(f"child {i}: {results[i]}")
            print(f"round {round_no + 1}: errors={len(bad)} {bad[:2]}")
            failures += len(bad)
    print("RESULT:", "PASS" if failures == 0 else f"FAIL ({failures} errors)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
