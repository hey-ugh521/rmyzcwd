"""T5 回归：网关鉴权边界（回环默认 + token + 参考图限制 + 非回环 fail-fast）。

覆盖（全部离线、进程内，不连 PG / ComfyUI / 网络）：
  1. _is_loopback_host：回环判定
  2. _enforce_bind_security：非回环 + 无 token → 启动失败（SystemExit）；有 token 放行
  3. Settings 默认 host=127.0.0.1
  4. _secret_gateway_token：从密钥 SSOT（secrets.json gateway.token）读取
  5. resolve_reference_bytes：禁 URL / 绝对路径 / 分隔符 / .. 穿越；upload_id 可解析
  6. api_route 守卫：/api/* 配 token 后强制 Bearer；/api/pulse 豁免；未配 token 不鉴权

隔离：导入 server 前把 MOBIUS_CONFIG 指向临时 config（回环、无 token）、
MOBIUS_SECRETS_FILE 指向不存在的临时文件，并清掉相关环境变量。

运行：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe media_gateway\\test_t5_gateway_auth.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

# ---- 导入 server 之前完成隔离 ----
_TMP = Path(tempfile.mkdtemp(prefix="t5-gw-"))
_cfg = _TMP / "config.json"
_cfg.write_text(json.dumps({
    "services": {"gateway": {"host": "127.0.0.1", "port": 18899}},
}), encoding="utf-8")
os.environ["MOBIUS_CONFIG"] = str(_cfg)
os.environ["MOBIUS_SECRETS_FILE"] = str(_TMP / "absent-secrets.json")
os.environ.pop("MOBIUS_GATEWAY_TOKEN", None)
os.environ.pop("MOBIUS_GATEWAY_HOST", None)

import server as gw  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


# ---------------------------------------------------------------- 1. 回环判定
def test_is_loopback() -> None:
    print("\n[1] _is_loopback_host")
    for h in ("127.0.0.1", "localhost", "::1", "127.0.0.9", "127.1.2.3"):
        check(f"{h} 是回环", gw._is_loopback_host(h) is True)
    for h in ("0.0.0.0", "::", "", "192.168.1.5", "example.com", "10.0.0.1"):
        check(f"{h!r} 非回环", gw._is_loopback_host(h) is False)


# ---------------------------------------------------------------- 2. fail-fast
def test_enforce_bind_security() -> None:
    print("\n[2] _enforce_bind_security：非回环 + 无 token → 启动失败")

    def fake(host: str, token: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(host=host, gateway_token=token)

    # 回环：无论有无 token 都放行
    for host in ("127.0.0.1", "::1", "localhost"):
        try:
            gw._enforce_bind_security(fake(host, ""))
            check(f"回环 {host} 无 token 放行", True)
        except SystemExit:
            check(f"回环 {host} 无 token 放行", False, "误抛 SystemExit")

    # 非回环 + 无 token → SystemExit
    for host in ("0.0.0.0", "::", "", "192.168.1.5"):
        try:
            gw._enforce_bind_security(fake(host, ""))
            check(f"非回环 {host!r} 无 token → 启动失败", False, "未抛 SystemExit")
        except SystemExit:
            check(f"非回环 {host!r} 无 token → 启动失败", True)

    # 非回环 + 有 token → 放行
    try:
        gw._enforce_bind_security(fake("0.0.0.0", "sekret"))
        check("非回环 + 有 token 放行", True)
    except SystemExit:
        check("非回环 + 有 token 放行", False, "误抛 SystemExit")


# ---------------------------------------------------------------- 3. 默认 host
def test_default_host() -> None:
    print("\n[3] Settings 默认监听回环")
    check("settings.host == 127.0.0.1", gw.settings.host == "127.0.0.1", gw.settings.host)
    check("_is_loopback_host(settings.host) 为真", gw._is_loopback_host(gw.settings.host))


# ---------------------------------------------------------------- 4. token SSOT
def test_secret_gateway_token() -> None:
    print("\n[4] _secret_gateway_token 从 SSOT 读取")
    sec_file = _TMP / "with-token.json"
    sec_file.write_text(json.dumps({"gateway": {"token": "abc123"}}), encoding="utf-8")
    old = os.environ.get("MOBIUS_SECRETS_FILE")
    try:
        os.environ["MOBIUS_SECRETS_FILE"] = str(sec_file)
        check("secrets.json gateway.token 被读出", gw._secret_gateway_token() == "abc123",
              gw._secret_gateway_token())
        os.environ["MOBIUS_SECRETS_FILE"] = str(_TMP / "absent-secrets.json")
        check("无 secrets 文件时返回空串", gw._secret_gateway_token() == "")
    finally:
        if old is None:
            os.environ.pop("MOBIUS_SECRETS_FILE", None)
        else:
            os.environ["MOBIUS_SECRETS_FILE"] = old


# ---------------------------------------------------------------- 5. 参考图限制
def test_resolve_reference_bytes() -> None:
    print("\n[5] resolve_reference_bytes：禁 URL / 路径，仅 upload_id")
    up = _TMP / "uploads"
    up.mkdir(exist_ok=True)
    (up / "up1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    cfg = types.SimpleNamespace(uploads_dir=str(up))

    def expect_error(reference: str, code: str, status: int | None = None) -> None:
        try:
            asyncio.run(gw.resolve_reference_bytes(cfg, reference))
            check(f"{reference!r} → {code}", False, "未抛 ApiError")
        except gw.ApiError as exc:
            ok = exc.error == code and (status is None or exc.status == status)
            check(f"{reference!r} → {code}", ok, f"error={exc.error} status={exc.status}")

    expect_error("http://evil/x.png", "reference_image_url_forbidden", 400)
    expect_error("https://evil/x.png", "reference_image_url_forbidden", 400)
    expect_error("ftp://evil/x.png", "reference_image_url_forbidden", 400)
    expect_error("C:\\abs\\x.png", "reference_image_path_forbidden", 400)
    expect_error("/abs/x.png", "reference_image_path_forbidden", 400)
    expect_error("..\\..\\evil.png", "reference_image_path_forbidden", 400)
    expect_error("a/b.png", "reference_image_path_forbidden", 400)
    expect_error("", "reference_image_missing", 400)
    expect_error("nope", "reference_image_not_found", 404)

    # 合法：精确文件名
    data, name = asyncio.run(gw.resolve_reference_bytes(cfg, "up1.png"))
    check("精确文件名可解析", data.startswith(b"\x89PNG") and name == "up1.png",
          f"name={name} bytes={len(data)}")
    # 合法：upload_id 前缀（无扩展名）
    data2, name2 = asyncio.run(gw.resolve_reference_bytes(cfg, "up1"))
    check("upload_id 前缀匹配可解析", data2.startswith(b"\x89PNG") and name2 == "up1.png",
          f"name={name2}")


# ---------------------------------------------------------------- 6. api_route 守卫
def _fake_request(path: str, auth: str | None = None) -> Request:
    headers = [(b"host", b"testserver")]
    if auth is not None:
        headers.append((b"authorization", auth.encode("latin-1")))
    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "server": ("testserver", 80), "client": ("127.0.0.1", 5000), "headers": headers,
    }
    return Request(scope)


def test_api_route_guard() -> None:
    print("\n[6] api_route Bearer 守卫（进程内假请求）")

    async def handler(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "probe": True})

    endpoint = gw.api_route("/api/_t5probe", ["GET"])(handler)
    original_token = gw.settings.gateway_token
    try:
        gw.settings.gateway_token = "sekret"
        r1 = asyncio.run(endpoint(_fake_request("/api/_t5probe")))
        check("配 token + 无 Authorization → 401", r1.status_code == 401, str(r1.status_code))
        r2 = asyncio.run(endpoint(_fake_request("/api/_t5probe", "Bearer wrong")))
        check("配 token + 错误 token → 401", r2.status_code == 401, str(r2.status_code))
        r3 = asyncio.run(endpoint(_fake_request("/api/_t5probe", "Bearer sekret")))
        check("配 token + 正确 token → 200", r3.status_code == 200, str(r3.status_code))
        r4 = asyncio.run(endpoint(_fake_request("/api/pulse", "Bearer wrong")))
        check("/api/pulse 豁免鉴权 → 200", r4.status_code == 200, str(r4.status_code))
        r5 = asyncio.run(endpoint(_fake_request("/health")))
        check("非 /api/* 路径不鉴权 → 200", r5.status_code == 200, str(r5.status_code))

        gw.settings.gateway_token = ""
        r6 = asyncio.run(endpoint(_fake_request("/api/_t5probe")))
        check("未配 token → 不鉴权（向后兼容）200", r6.status_code == 200, str(r6.status_code))
    finally:
        gw.settings.gateway_token = original_token


def main() -> int:
    print("==> T5 网关鉴权边界回归")
    test_is_loopback()
    test_enforce_bind_security()
    test_default_host()
    test_secret_gateway_token()
    test_resolve_reference_bytes()
    test_api_route_guard()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
