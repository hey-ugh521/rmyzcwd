"""Negative-path check: the multiprocess probe must fail fast when a child
initialization fails. This proves the regression itself can go red.

A non-zero exit alone is not enough: on POSIX a duplicate ``barrier.abort()``
once made the injected-failure path hang until the outer timeout. So this check
also bounds the elapsed time, instead of accepting a slow-but-eventual failure.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "tests" / "probe_store_multiprocess.py"

PROBE_TIMEOUT = 30
# A healthy injected-failure run takes ~4-7s locally; a child sleeping 2s per
# round pushes it to ~14s, and the POSIX abort hang took ~37s. An absolute
# bound is deliberate: measuring a baseline in this same process proved too
# noisy on shared Windows machines (2.8s vs 16.9s across identical runs),
# inflating the limit until it detected nothing.
# Override only if a runner is genuinely and consistently slower.
MAX_FAIL_SECONDS = float(os.environ.get("MOBIUS_NEGATIVE_MAX_SECONDS", "12"))


def main() -> int:
    env = dict(os.environ)
    env["CORE_DB_BACKEND"] = "sqlite"
    env["MOBIUS_PROBE_FAIL_CHILD"] = "0"
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(PROBE)],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        print(
            f"probe hung on injected failure (>{PROBE_TIMEOUT}s); fail-fast "
            "regressed or a child/barrier is stuck",
            flush=True,
        )
        return 1
    elapsed = time.monotonic() - started

    if proc.returncode == 0:
        print("probe unexpectedly PASSED on injected child failure", flush=True)
        return 1
    if elapsed > MAX_FAIL_SECONDS:
        print(
            f"probe failed but too slowly: {elapsed:.1f}s "
            f"(limit {MAX_FAIL_SECONDS:.1f}s); fail-fast has regressed",
            flush=True,
        )
        return 1

    print(
        f"probe failed as expected: exit={proc.returncode} in {elapsed:.1f}s "
        f"(limit {MAX_FAIL_SECONDS:.1f}s)",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
