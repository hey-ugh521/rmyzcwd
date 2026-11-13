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
- QQ webhook

## Not covered by the unit runner

These remain manual/integration suites because they need real components,
configuration, or a disposable PostgreSQL database:

- `test_registry_pg_contract.py` — needs `MOBIUS_TEST_PG_DSN`
- M1/M3/E2E/console/gateway install and recovery suites
- any test that starts real services or depends on local config

## Why the runner exists

Previously test execution depended on the local shell and installed interpreter.
The unit runner fixes the backend before imports and keeps each suite on temp
SQLite files so a clean machine does not accidentally use production PG/config.
