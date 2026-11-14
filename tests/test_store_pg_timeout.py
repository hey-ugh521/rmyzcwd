"""PG connection/statement timeout contract tests (no real PG connection)."""

from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CORE_DB_BACKEND", "sqlite")

from core import store  # noqa: E402


class PgTimeoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.old_dsn = store.PG_DSN
        self.old_connect = store.PG_CONNECT_TIMEOUT
        self.old_statement = store.PG_STATEMENT_TIMEOUT_MS
        store.PG_DSN = "postgresql://user:pass@127.0.0.1:55432/db"
        self.rows = types.ModuleType("psycopg.rows")
        self.rows.dict_row = object()
        self.psycopg = types.ModuleType("psycopg")
        self.psycopg.rows = self.rows
        self.psycopg.connect = mock.Mock(return_value=mock.Mock())

    def tearDown(self) -> None:
        store.PG_DSN = self.old_dsn
        store.PG_CONNECT_TIMEOUT = self.old_connect
        store.PG_STATEMENT_TIMEOUT_MS = self.old_statement

    def _connect(self):
        with mock.patch.dict(sys.modules, {
            "psycopg": self.psycopg,
            "psycopg.rows": self.rows,
        }):
            store._PgConnection()
        return self.psycopg.connect.call_args.kwargs

    def test_pg_connect_timeout_passed(self):
        store.PG_CONNECT_TIMEOUT = 5
        store.PG_STATEMENT_TIMEOUT_MS = 0
        kwargs = self._connect()
        self.assertEqual(kwargs["connect_timeout"], 5)
        self.assertIs(kwargs["row_factory"], self.rows.dict_row)
        self.assertNotIn("options", kwargs)

    def test_pg_statement_timeout_passed(self):
        store.PG_CONNECT_TIMEOUT = 0
        store.PG_STATEMENT_TIMEOUT_MS = 5000
        kwargs = self._connect()
        self.assertNotIn("connect_timeout", kwargs)
        self.assertEqual(kwargs["options"], "-c statement_timeout=5000")

    def test_pg_timeouts_context_overrides(self):
        store.PG_CONNECT_TIMEOUT = 0
        store.PG_STATEMENT_TIMEOUT_MS = 0
        with store.pg_timeouts(connect_timeout=2,
                               statement_timeout_ms=1234):
            kwargs = self._connect()
        self.assertEqual(kwargs["connect_timeout"], 2)
        self.assertEqual(kwargs["options"], "-c statement_timeout=1234")
        self.assertNotIn("connect_timeout", self._connect())

    def test_cfg_int_zero_and_invalid(self):
        with mock.patch.dict(os.environ, {"TEST_TIMEOUT": "0"}):
            self.assertEqual(store._cfg_int("TEST_TIMEOUT", "x", 5), 0)
        with mock.patch.dict(os.environ, {"TEST_TIMEOUT": "2"}):
            self.assertEqual(store._cfg_int("TEST_TIMEOUT", "x", 5), 2)
        for bad in ("2.7", "-1", "inf", "abc", ""):
            with mock.patch.dict(os.environ, {"TEST_TIMEOUT": bad}):
                self.assertEqual(
                    store._cfg_int("TEST_TIMEOUT", "x", 5), 5, bad)
        with mock.patch.dict(os.environ, {"TEST_TIMEOUT": "True"}):
            self.assertEqual(store._cfg_int("TEST_TIMEOUT", "x", 5), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
