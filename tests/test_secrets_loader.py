"""tests/test_secrets_loader.py — Core 密钥 SSOT 覆盖层单测（独立脚本）。

覆盖：路径解析优先级、DSN 口令替换、apply_to_config 覆盖、缺文件回退。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def main() -> int:
    from core import secrets as sec

    tmp = Path(tempfile.mkdtemp(prefix="secrets-loader-"))
    old_env = {k: os.environ.get(k) for k in ("MOBIUS_SECRETS_FILE", "MOBIUS_ROOT", "MOBIUS_CONFIG")}
    try:
        # 1) 环境变量优先
        sp = tmp / "s.json"
        sp.write_text(json.dumps({"pg": {"password": "envpw"}, "core": {"api_key": "ck"}}), encoding="utf-8")
        os.environ["MOBIUS_SECRETS_FILE"] = str(sp)
        check("环境变量优先", sec.secrets_path(tmp) == sp)
        data = sec.load(tmp)
        check("读取值", sec.get("pg", "password", data=data) == "envpw")
        check("嵌套取值", sec.get("core", "api_key", data=data) == "ck")
        check("缺省回退", sec.get("x", "y", default="d", data=data) == "d")

        # 2) config.json secrets_file（相对路径）
        os.environ.pop("MOBIUS_SECRETS_FILE", None)
        os.environ["MOBIUS_ROOT"] = str(tmp)
        (tmp / "data").mkdir(exist_ok=True)
        (tmp / "config.json").write_text(json.dumps({"secrets_file": "data/s.json"}), encoding="utf-8")
        (tmp / "data" / "s.json").write_text("{}", encoding="utf-8")
        os.environ["MOBIUS_CONFIG"] = str(tmp / "config.json")
        check("config.secrets_file 相对路径", sec.secrets_path(tmp) == tmp / "data" / "s.json")

        # 3) 默认 data/secrets.json（无 config 指定时）
        os.environ.pop("MOBIUS_CONFIG", None)
        (tmp / "config.json").unlink(missing_ok=True)
        (tmp / "data" / "secrets.json").write_text("{}", encoding="utf-8")
        check("默认 data/secrets.json", sec.secrets_path(tmp) == tmp / "data" / "secrets.json")

        # 4) DSN 口令替换
        dsn = "postgresql://postgres:old@127.0.0.1:55432/mobius_core"
        out = sec._with_password(dsn, "new pw/123")
        check("DSN 口令替换", "new%20pw%2F123" in out and out.endswith("/mobius_core"), out)

        # 5) apply_to_config
        # 显式指定 secrets 路径，避免受前面路径测试的 config/默认路径影响
        os.environ["MOBIUS_SECRETS_FILE"] = str(tmp / "data" / "s.json")
        (tmp / "data" / "s.json").write_text(json.dumps({
            "pg": {"password": "pgnew"}, "deepseek": {"api_key": "sk-new"},
            "core": {"api_key": "corenew"},
            "langbot": {"global_api_key": "gnew", "integration_api_key": "inew"},
        }), encoding="utf-8")
        cfg = {
            "core": {"api_key": "old"},
            "llm": {"api_key": "old"},
            "integrations": {"langbot": {"api_key": "old"}},
            "db_dsn": "postgresql://postgres:old@127.0.0.1:55432/mobius_core",
            "services": {"gateway": {"env": {"MOBIUS_TASK_DSN": "postgresql://postgres:old@127.0.0.1:55432/mobius"}}},
        }
        ok = sec.apply_to_config(cfg, tmp)
        check("apply 返回 True", ok)
        check("core.api_key 覆盖", cfg["core"]["api_key"] == "corenew")
        check("llm.api_key 覆盖", cfg["llm"]["api_key"] == "sk-new")
        check("integration key 优先", cfg["integrations"]["langbot"]["api_key"] == "inew")
        check("db_dsn 口令覆盖", "pgnew" in cfg["db_dsn"], cfg["db_dsn"])
        check("gateway DSN 覆盖", "pgnew" in cfg["services"]["gateway"]["env"]["MOBIUS_TASK_DSN"])
        os.environ.pop("MOBIUS_SECRETS_FILE", None)
        check("无 secrets 文件回退", sec.apply_to_config({"core": {}}, tmp / "nope") is False)
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
