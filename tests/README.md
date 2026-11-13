# Tests

## Portable unit suite

```powershell
python tests/run_unit.py
```

- forces `CORE_DB_BACKEND=sqlite`
- uses isolated temp databases
- no live Core/Gateway/ComfyUI/PostgreSQL required
- intended for CI and clean environments

Included suites:

- identity
- capabilities
- registry core auth
- registry migration
- registry runtime
- voice assets
- registry lifecycle
- registry API
- registry index
- store concurrency (including connection-creation serialization)
- store multiprocess startup
- QQ webhook

## Negative-path check

CI runs:

```powershell
python tests/check_probe_negative.py
```

It sets `MOBIUS_PROBE_FAIL_CHILD=0` and requires
`probe_store_multiprocess.py` to exit non-zero. This proves the regression can
go red; it is not a success-only smoke test.

Manual failure injection:

```powershell
$env:MOBIUS_PROBE_FAIL_CHILD="0"
python tests/probe_store_multiprocess.py
# expect exit=1 within a few seconds
```

## Not covered by the unit runner

`tests/probe_store_multiprocess.py` is the subprocess probe used by
`test_store_multiprocess.py`; the wrapper is in the unit runner and CI. The probe
is not run directly as a test module.

These remain manual/integration suites because they need real components,
configuration, or a disposable PostgreSQL database:

- `test_registry_pg_contract.py` — needs `MOBIUS_TEST_PG_DSN`
- M1/M3/E2E/console/gateway install and recovery suites
- any test that starts real services or depends on local config

## CI

`.github/workflows/unit-tests.yml` runs on `windows-latest` and
`ubuntu-latest` (Python 3.13). This exercises both `msvcrt.locking` and
`fcntl.flock` branches of the DB initialization lock.

## Important: run serially

The suites still share a process-global `store.DB_PATH` while test classes swap
temporary databases. Do **not** run them with `pytest-xdist`/parallel test
classes in one process. `tests/run_unit.py` executes them serially; parallel
test runners can make a worker see another test's temp DB path or a stale
read-only database handle.

## Why the runner exists

Previously test execution depended on the local shell and installed interpreter.
The unit runner fixes the backend before imports and keeps each suite on temp
SQLite files so a clean machine does not accidentally use production PG/config.
