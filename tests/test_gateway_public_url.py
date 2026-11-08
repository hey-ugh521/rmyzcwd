"""回归测试：生图回传 URL 必须是原生可达地址（127.0.0.1），不得是 host.docker.internal。

背景 bug（2026-11-08）：
  ComfyUI 出图成功，但 Gateway 返回的图片 URL 是
  http://host.docker.internal:8899/img?...（Docker 时代默认值）。
  原生 LangBot 回传时下载该 URL 得到 HTTP 502，图片丢失、只剩文字链接。

运行：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe tests\\test_gateway_public_url.py
  ... --static-only   # 不访问运行中的网关

覆盖：
  1. config.json 的网关公开 URL 为 127.0.0.1
  2. media_gateway/server.py 默认 public_base 为 127.0.0.1（无 host.docker.internal 默认）
  3. 运行中网关 /api/status 的 gateway.url 为 127.0.0.1
  4. LangBot model_providers 无 host.docker.internal 残留（可选，能连库才测）
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def test_static() -> None:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    public = str((((cfg.get("services") or {}).get("gateway") or {}).get("env") or {}).get("MOBIUS_GATEWAY_PUBLIC_URL") or "")
    check("config 显式网关公开 URL", public.startswith("http://127.0.0.1:"), public or "未配置")
    if public:
        check("config 无 host.docker.internal", "host.docker.internal" not in public, public)

    src = (ROOT / "media_gateway" / "server.py").read_text(encoding="utf-8")
    check("代码默认 public_base 为 127.0.0.1", 'f"http://127.0.0.1:{self.port}"' in src)
    check("代码无 host.docker.internal 默认值", 'host.docker.internal:{self.port}' not in src
          and 'host.docker.internal:{int(port)}' not in src)


def test_live() -> None:
    url = "http://127.0.0.1:8899/api/status"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as exc:  # noqa: BLE001
        check("网关 /api/status 可达", False, f"跳过：{exc}")
        return
    gw = (data or {}).get("gateway") or {}
    base = str(gw.get("url") or "")
    check("运行中网关 url 为 127.0.0.1", base.startswith("http://127.0.0.1:"), base)
    check("运行中网关 url 无 host.docker.internal", "host.docker.internal" not in base, base)


def test_langbot_db() -> None:
    try:
        import psycopg

        env = {}
        env_file = Path(r"D:\AI\mobius\.env")
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip()
        pw = env.get("PG_PASSWORD") or os.environ.get("PG_PASSWORD", "")
        if not pw:
            check("LangBot providers 无 host.docker.internal", True, "跳过：无 PG_PASSWORD")
            return
        with psycopg.connect(f"postgresql://postgres:{pw}@127.0.0.1:55432/langbot", connect_timeout=5) as conn:
            hits = conn.execute(
                "select count(*) from model_providers where base_url like '%host.docker.internal%'"
            ).fetchone()[0]
        check("LangBot providers 无 host.docker.internal", hits == 0, f"{hits} 条残留")
    except Exception as exc:  # noqa: BLE001
        check("LangBot providers 无 host.docker.internal", True, f"跳过：{exc}")


def main() -> int:
    print("==> Gateway 回传 URL 回归测试")
    test_static()
    if "--static-only" not in sys.argv:
        test_live()
    test_langbot_db()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
