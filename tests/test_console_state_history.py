"""tests/test_console_state_history.py — Console 状态历史（静态 + API 集成）。

无 Core 在跑时自动 SKIP（只做静态检查）。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASSED: list[str] = []
FAILED: list[str] = []

CORE = os.environ.get("MOBIUS_CORE_URL", "http://127.0.0.1:8900").rstrip("/")


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def get_json(path: str, timeout: float = 5.0):
    req = urllib.request.Request(CORE + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    app = (ROOT / "core" / "console" / "app.js").read_text(encoding="utf-8")
    check("app.js 调用历史 API", "/api/services/history?limit=20" in app)
    check("app.js 有 paintHistory", "function paintHistory(data)" in app)
    check("app.js 展示登录态", "LOGIN_TEXT" in app and "svc-login" in app)
    check("app.js 展示状态持续", "该状态已持续" in app)

    store_src = (ROOT / "core" / "store.py").read_text(encoding="utf-8")
    check("store 有 audit_state_history", "def audit_state_history" in store_src)
    server_src = (ROOT / "core" / "server.py").read_text(encoding="utf-8")
    check("server 有 history 路由", '"/api/services/history"' in server_src)

    # 在线集成检查
    try:
        health = get_json("/health")
        check("Core 可达", health.get("ok") is not False or "status" in health)
    except Exception as exc:  # noqa: BLE001
        print(f"  SKIP  Core 不可达（{type(exc).__name__}），跳过 API 检查")
        print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过（API SKIP）")
        return 0 if not FAILED else 1

    try:
        data = get_json("/api/services/history?limit=10")
        rows = data.get("history") or []
        check("history 返回 200/ok", data.get("ok") is True)
        check("history 行数 <= limit", len(rows) <= 10, str(len(rows)))
        if rows:
            sample = rows[0]
            check("行含 ts/action/detail", "ts" in sample and "action" in sample and isinstance(sample.get("detail"), dict))
            actions = {r.get("action") for r in rows}
            check("含 state/login 动作", any(str(a).startswith(("state.", "login.")) for a in actions), str(actions))
        filtered = get_json("/api/services/history?service=napcat&limit=5")
        frows = filtered.get("history") or []
        check("service 过滤生效", all(str((r.get("detail") or {}).get("service")) == "napcat" for r in frows), str(len(frows)))
    except urllib.error.HTTPError as exc:
        check("history API 非 500", exc.code != 500, f"HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001
        check("history API 请求成功", False, f"{type(exc).__name__}: {exc}")

    try:
        with urllib.request.urlopen(CORE + "/console/", timeout=5) as r:
            page = r.read().decode("utf-8", errors="replace")
        check("console 页面可达", r.status == 200 and "view-root" in page)
    except Exception as exc:  # noqa: BLE001
        check("console 页面可达", False, str(exc))

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name in FAILED:
            print("  FAIL:", name)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
