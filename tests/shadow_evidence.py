"""Isolated Phase 4F-2B-4 shadow evidence run.

Not production. Uses a temp SQLite DB and TestClient; Shadow is enabled only in
this process, then reported and discarded.

Run from repo root:
    python tests/shadow_evidence.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CORE_DB_BACKEND"] = "sqlite"

from fastapi.testclient import TestClient  # noqa: E402

from core import capabilities, identity, identity_shadow as shadow, store  # noqa: E402


def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    old_backend = store.DB_BACKEND
    old_path = store.DB_PATH
    old_core = store.CFG.get("core")
    old_identity = store.CFG.get("identity")
    try:
        store.DB_BACKEND = "sqlite"
        store.DB_PATH = Path(tmp.name) / "shadow-evidence.db"
        store.CFG["core"] = {**(old_core or {}), "api_key": "shadow-evidence-key"}
        store.CFG["identity"] = {"shadow": {"enabled": False}}
        store.init_db()
        identity.init_schema()
        capabilities.init_schema()
        shadow.init_schema()
        identity.resolve_legacy_api_key("shadow-evidence-key")

        import core.server as coreserver  # noqa: PLC0415

        old_key = coreserver.API_KEY
        coreserver.API_KEY = "shadow-evidence-key"
        client = TestClient(coreserver.app, client=("10.0.0.9", 50000))
        headers = {"Authorization": "Bearer shadow-evidence-key"}

        cases = [
            ("GET", "/api/capabilities/permissions", headers, None),
            ("GET", "/api/shadow-not-covered", headers, None),
            ("POST", "/api/capabilities/permissions",
             {"Authorization": "Bearer wrong-key"}, {}),
        ]

        # Baseline: Shadow off.
        baseline = []
        for method, path, hdrs, body in cases:
            r = client.request(method, path, headers=hdrs, json=body)
            baseline.append({"method": method, "path": path,
                             "status": r.status_code, "body": r.content})
        assert shadow.list_shadow_events() == []

        # Enabled: same requests; only observation is added.
        store.CFG["identity"] = {"shadow": {"enabled": True}}
        principals_before = len(identity.list_principals())
        parity = []
        for i, (method, path, hdrs, body) in enumerate(cases):
            r = client.request(method, path, headers=hdrs, json=body)
            base = baseline[i]
            parity.append({
                "method": method,
                "path": path,
                "status": r.status_code,
                "status_same": r.status_code == base["status"],
                "body_same": r.content == base["body"],
            })
        principals_after = len(identity.list_principals())

        events = shadow.list_shadow_events(limit=50)
        summary = {
            "mode": "isolated-sqlite-testclient",
            "requests": len(cases),
            "shadow_events": len(events),
            "event_summary": [
                {
                    "method": e["method"],
                    "path": e["path"],
                    "identity_state": e["identity_state"],
                    "evaluation_state": e["evaluation_state"],
                    "policy_domain": e["policy_domain"],
                    "policy_result": e["policy_result"],
                    "policy_state": e["policy_state"],
                    "policy_version": e["policy_version"],
                }
                for e in reversed(events)
            ],
            "response_parity": parity,
            "principals_before": principals_before,
            "principals_after": principals_after,
            "no_identity_write": principals_before == principals_after,
            "policy_versions": sorted({e["policy_version"] for e in events
                                      if e["policy_version"]}),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        ok = (
            all(p["status_same"] and p["body_same"] for p in parity)
            and summary["no_identity_write"]
            and summary["shadow_events"] == len(cases)
        )
        return 0 if ok else 1
    finally:
        try:
            client.close()  # type: ignore[name-defined]
        except Exception:  # noqa: BLE001
            pass
        try:
            coreserver.API_KEY = old_key  # type: ignore[name-defined]
        except Exception:  # noqa: BLE001
            pass
        store.close_anchor()
        store.DB_BACKEND = old_backend
        store.DB_PATH = old_path
        if old_core is None:
            store.CFG.pop("core", None)
        else:
            store.CFG["core"] = old_core
        if old_identity is None:
            store.CFG.pop("identity", None)
        else:
            store.CFG["identity"] = old_identity
        tmp.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
