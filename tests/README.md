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
- QQ webhook

## Not covered by the unit runner

`tests/probe_store_multiprocess.py` is a diagnostic probe for cross-process
WAL startup. It is intentionally excluded from CI until a process-level policy
exists; a failing probe documents a known gap rather than turning it into a
green test.

These remain manual/integration suites because they need real components,
configuration, or a disposable PostgreSQL database:

- `test_registry_pg_contract.py` — needs `MOBIUS_TEST_PG_DSN`
- M1/M3/E2E/console/gateway install and recovery suites
- any test that starts real services or depends on local config

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
