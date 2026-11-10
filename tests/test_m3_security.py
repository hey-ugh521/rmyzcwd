"""M3a 安全边界测试：本机 token / Console cookie / 非回环 Bearer。

隔离临时 sqlite + 临时 config，不连生产。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="m3-sec-"))
_cfg = _TMP / "config.json"
_cfg.write_text(json.dumps({
    "db": (_TMP / "core.db").as_posix(),
    "db_backend": "sqlite",
    "core": {"api_key": "test-local-key", "port": 18900},
    "services": {},
}), encoding="utf-8")
os.environ["MOBIUS_CONFIG"] = str(_cfg)
os.environ["MOBIUS_SECRETS_FILE"] = str(_TMP / "absent-secrets.json")
os.environ["CORE_DB_BACKEND"] = "sqlite"
os.environ.pop("CORE_DB_DSN", None)
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from core import server  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def main() -> int:
    c = TestClient(server.app, client=("testclient", 50000))
    print("\n[loopback]")
    r = c.post("/api/tasks/nope/cancel")
    check("回环变更无 token → 401", r.status_code == 401, str(r.status_code))
    r = c.post("/api/tasks/nope/cancel",
               headers={"Authorization": "Bearer test-local-key"})
    check("回环变更 Bearer → 通过鉴权（404 业务）", r.status_code == 404, str(r.status_code))
    r = c.get("/console")
    check("Console GET 200", r.status_code == 200, str(r.status_code))
    sc = r.headers.get("set-cookie", "")
    check("Console 下发 HttpOnly+SameSite=Strict cookie",
          "mobius_local_token=" in sc and "HttpOnly" in sc and "SameSite=strict" in sc,
          sc[:160])
    r = c.post("/api/tasks/nope/cancel")
    check("同源 cookie → 通过鉴权（404 业务）", r.status_code == 404, str(r.status_code))
    r = c.get("/api/services")
    check("只读 /api/services 回环免 token", r.status_code == 200, str(r.status_code))
    r = c.get("/health")
    check("/health 免 token", r.status_code == 200, str(r.status_code))

    c2 = TestClient(server.app, client=("203.0.113.10", 54321))
    print("\n[non-loopback]")
    r = c2.get("/api/services")
    check("非回环 GET 无 token → 401", r.status_code == 401, str(r.status_code))
    r = c2.get("/api/services", headers={"Authorization": "Bearer test-local-key"})
    check("非回环 GET Bearer → 200", r.status_code == 200, str(r.status_code))
    r = c2.get("/console")
    check("非回环 Console → 401（不泄露 cookie）", r.status_code == 401, str(r.status_code))
    r = c2.post("/api/tasks/nope/cancel")
    check("非回环 POST 无 token → 401", r.status_code == 401, str(r.status_code))
    r = c2.post("/api/tasks/nope/cancel",
                cookies={"mobius_local_token": "test-local-key"})
    check("非回环 cookie 不通过（必须 Bearer）", r.status_code == 401, str(r.status_code))
    r = c2.post("/api/tasks/nope/cancel",
                headers={"Authorization": "Bearer test-local-key"})
    check("非回环 POST Bearer → 通过鉴权（404 业务）", r.status_code == 404, str(r.status_code))

    print("\n[secrets]")
    from core import secrets as core_secrets
    tmp_secret = _TMP / "acl-secrets.json"
    tmp_secret.write_text("{}", encoding="utf-8")
    check("harden_acl 成功", core_secrets.harden_acl(tmp_secret) is True)
    check("harden_acl 不存在文件返回 False", core_secrets.harden_acl(_TMP / "nope.json") is False)
    check("harden_acl CLI 存在", (ROOT / "ops" / "secrets" / "harden_acl.py").is_file())

    print("\n[static]")
    src = (ROOT / "launcher" / "mobius_launcher.py").read_text(encoding="utf-8")
    check("launcher http_json 注入 Authorization",
          "_core_auth_headers" in src and '"Authorization": f"Bearer {key}"' in src)
    gw = (ROOT / "media_gateway" / "server.py").read_text(encoding="utf-8")
    check("gateway token 回退 core.api_key",
          'module.get("gateway", "token") or module.get("core", "api_key")' in gw)
    svc = (ROOT / "core" / "services.py").read_text(encoding="utf-8")
    check("GatewayClient token 回退 core.api_key",
          '_secrets.get("core", "api_key")' in svc)

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
