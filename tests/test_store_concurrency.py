"""Regression: concurrent connections must not race journal_mode.

Before the fix, every store.conn() executed PRAGMA journal_mode=WAL. Two threads
entering conn() concurrently could leave one connection in a read-only view and
the next BEGIN IMMEDIATE failed with 'attempt to write a readonly database'.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from core import store  # noqa: E402


class StoreConcurrencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_backend = store.DB_BACKEND
        self.old_path = store.DB_PATH
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = Path(self.tmp.name) / "concurrency.db"
        store.init_db()

    def tearDown(self) -> None:
        store.DB_BACKEND = self.old_backend
        store.DB_PATH = self.old_path
        self.tmp.cleanup()

    def test_concurrent_begin_immediate_no_readonly(self):
        errors: list[str] = []
        for round_no in range(6):
            barrier = threading.Barrier(8)

            def worker(n: int) -> None:
                barrier.wait()
                c = store.conn()
                try:
                    c.execute("BEGIN IMMEDIATE")
                    c.execute(
                        "INSERT OR REPLACE INTO kv(k,v,updated_at) VALUES(?,?,?)",
                        (f"race-{round_no}-{n}", "ok", 1.0),
                    )
                    c.commit()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{type(exc).__name__}: {exc}")
                    try:
                        c.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                finally:
                    c.close()

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(errors, [])

    def test_journal_mode_is_wal_after_init(self):
        with store.conn() as c:
            mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(str(mode).lower(), "wal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
