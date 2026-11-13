"""Negative-path check: the multiprocess probe must fail fast when a child
initialization fails. This proves the regression itself can go red.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tests" / "probe_store_multiprocess.py"


def main() -> int:
    env = dict(os.environ)
    env["CORE_DB_BACKEND"] = "sqlite"
    env["MOBIUS_PROBE_FAIL_CHILD"] = "0"
    proc = subprocess.run(
        [sys.executable, str(PROBE)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode == 0:
        print("probe unexpectedly PASSED on injected child failure", flush=True)
        return 1
    print(f"probe failed as expected: exit={proc.returncode}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
