"""Cross-process WAL startup regression.

Runs the multiprocess probe in a child process so its anchor connections and
lock files do not leak into the unit-test process.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tests" / "probe_store_multiprocess.py"


class StoreMultiprocessTest(unittest.TestCase):
    def test_cross_process_startup(self):
        env = dict(os.environ)
        env["CORE_DB_BACKEND"] = "sqlite"
        proc = subprocess.run(
            [sys.executable, str(PROBE)],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"cross-process WAL probe failed\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
