"""PG connection timeout contract tests (no real PG connection)."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import store  # noqa: E402


class PgTimeoutTest(unittest.TestCase):
    def test_pg_connect_timeout_passed(self):
        old_dsn = store.PG_DSN
        old_timeout = store.PG_CONNECT_TIMEOUT
        store.PG_DSN = "postgresql://user:pass@127.0.0.1:55432/db"
        store.PG_CONNECT_TIMEOUT = 5
        rows = types.ModuleType("psycopg.rows")
        rows.dict_row = object()
        psycopg = types.ModuleType("psycopg")
        psycopg.rows = rows
        psycopg.connect = mock.Mock(return_value=mock.Mock())
        try:
            with mock.patch.dict(sys.modules, {
                "psycopg": psycopg,
                "psycopg.rows": rows,
            }):
                store._PgConnection()
            kwargs = psycopg.connect.call_args.kwargs
            self.assertEqual(kwargs["connect_timeout"], 5)
            self.assertIs(kwargs["row_factory"], rows.dict_row)
        finally:
            store.PG_DSN = old_dsn
            store.PG_CONNECT_TIMEOUT = old_timeout


if __name__ == "__main__":
    unittest.main(verbosity=2)
