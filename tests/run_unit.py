"""Portable unit-test runner for CI and clean local environments.

Only suites that run without a live Core/Gateway/ComfyUI/PostgreSQL are listed
here. E2E/contract suites remain explicitly separate.

Usage (from repo root):
    python tests/run_unit.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force SQLite before importing any core module; never let an inherited shell
# variable make the unit suite hit a real PostgreSQL instance.
os.environ["CORE_DB_BACKEND"] = "sqlite"
os.environ.setdefault("PYTHONUTF8", "1")

UNIT_SUITES = [
    "tests.test_identity",
    "tests.test_capabilities",
    "tests.test_registry_core_auth",
    "tests.test_registry_migration",
    "tests.test_registry_runtime",
    "tests.test_voice_assets",
    "tests.test_registry_lifecycle",
    "tests.test_registry_api",
    "tests.test_registry_index",
    "tests.test_store_concurrency",
    "tests.test_store_multiprocess",
]

# Not a unittest class; kept as a standalone script.
SCRIPT_SUITES = ["tests/test_qq_webhook.py"]


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite([loader.loadTestsFromName(name) for name in UNIT_SUITES])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    ok = result.wasSuccessful()
    env = dict(os.environ)
    for script in SCRIPT_SUITES:
        print(f"\n===== {script} =====", flush=True)
        proc = subprocess.run([sys.executable, str(ROOT / script)],
                              cwd=str(ROOT), env=env)
        if proc.returncode != 0:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
