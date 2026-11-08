"""tests/test_secrets_rotate.py — 密钥 SSOT 轮换脚本回归测试（独立脚本）。

被测：ops/secrets/rotate.py（init / show / check / sync / rotate）。
隔离：全部使用临时目录与假视图（.env、config.json、LangBot config.yaml、
NapCat onebot11_*.json / webui.json）；STD 的 PG 指向必然连不上的
127.0.0.1:59999，绝不连接真实 PG、绝不读写真实生产配置。

覆盖：
  1. 路径解析优先级（与 core/secrets.py 同一约定）
  2. init 从现有视图反推 + 幂等（第二次不改文件、mtime 不变）
  3. show 只输出掩码，不泄露完整密钥
  4. check 检出 drift（exit 1）→ sync 修复 → check 通过（exit 0）
  5. rotate 生成规则、--value、未知 key、--sync、--no-backup
  6. JSON / YAML 行级替换：只改目标字段，保留其余内容与缩进
  7. PG 不可连：check 标记 SKIP，sync 告警但文件视图仍更新且 exit 0
  8. 原子写与备份：无 .tmp 残留、备份内容为改动前版本、无备份模式不产生备份
  9. secrets.example.json schema 与取值形状

运行：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe tests\\test_secrets_rotate.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ops" / "secrets" / "rotate.py"
EXAMPLE = ROOT / "ops" / "secrets" / "secrets.example.json"
sys.path.insert(0, str(ROOT))

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []

PG_HOST = "127.0.0.1"
PG_PORT = "59999"  # 必然无监听：不碰真实 PG
MISSING_MARKER = "MISSING-MARKER-NOT-IN-OUTPUT"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name}  {detail}")


def _pad(prefix: str, length: int) -> str:
    seed = "0123456789abcdefghijklmnopqrstuvwxyz"
    value = prefix + (seed * 4)
    return value[:length]


# 假视图里使用的“真实值”（全部是测试假数据）
FIX = {
    "pg": _pad("pg", 32),
    "deepseek": "sk-" + _pad("ds", 32),
    "core": _pad("core", 24),
    "langbot_global": _pad("global", 24),
    "langbot_secret": "0123456789abcdef" * 2,
    "langbot_recovery": "recov123",
    "langbot_integration": _pad("integ", 24),
    "napcat_http": _pad("http", 32),
    "napcat_ws": _pad("ws", 32),
    "napcat_webui": "webui1234567",
}

YAML_TEMPLATE = """api:
    extra_webhook_prefix: ''
    global_api_key: {langbot_global}
    host: 0.0.0.0
    port: 5300
database:
    postgresql:
        database: langbot
        host: 127.0.0.1
        password: {pg}
        port: 59999
        user: postgres
    sqlite:
        path: data/langbot.db
system:
    jwt:
        expire: 604800
        secret: {langbot_secret}
    recovery_key: {langbot_recovery}
    other:
        password: keep-this-password
"""


def _secrets_data(values: dict[str, str] | None = None) -> dict:
    v = dict(FIX)
    if values:
        v.update(values)
    return {
        "version": 1,
        "pg": {"password": v["pg"]},
        "deepseek": {"api_key": v["deepseek"]},
        "core": {"api_key": v["core"]},
        "langbot": {
            "global_api_key": v["langbot_global"],
            "secret": v["langbot_secret"],
            "recovery_key": v["langbot_recovery"],
            "integration_api_key": v["langbot_integration"],
        },
        "napcat": {
            "http_token": v["napcat_http"],
            "ws_token": v["napcat_ws"],
            "webui_token": v["napcat_webui"],
        },
    }


class Fixture:
    """一套临时仓库 + 四个假消费方视图。"""

    def __init__(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="secrets-rotate-test-"))
        self.repo = self.base / "repo"
        self.repo.mkdir()
        (self.repo / "data").mkdir()
        self.secrets_file = self.repo / "data" / "secrets.json"
        self.config_file = self.repo / "config.json"
        self.env_file = self.repo / ".env"
        self.langbot_dir = self.base / "langbot"
        (self.langbot_dir / "data").mkdir(parents=True)
        self.yaml_file = self.langbot_dir / "data" / "config.yaml"
        self.napcat_dir = self.base / "napcat"
        (self.napcat_dir / "config").mkdir(parents=True)
        self.onebot_file = self.napcat_dir / "config" / "onebot11_123456.json"
        self.webui_file = self.napcat_dir / "config" / "webui.json"
        self.backup_dir = self.base / "backups"

    # -- 环境 ------------------------------------------------------------
    def env(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith("MOBIUS_")}
        env.update(
            {
                "MOBIUS_ROOT": str(self.repo),
                "MOBIUS_SECRETS_FILE": str(self.secrets_file),
                "MOBIUS_CONFIG": str(self.config_file),
                "MOBIUS_ENV_FILE": str(self.env_file),
                "MOBIUS_LANGBOT_DIR": str(self.langbot_dir),
                "MOBIUS_NAPCAT_DIR": str(self.napcat_dir),
                "MOBIUS_PG_HOST": PG_HOST,
                "MOBIUS_PG_PORT": PG_PORT,
                "MOBIUS_BACKUP_DIR": str(self.backup_dir),
                "PYTHONIOENCODING": "utf-8",
            }
        )
        return env

    # -- 假视图 ----------------------------------------------------------
    def write_views(self, values: dict[str, str] | None = None) -> None:
        v = dict(FIX)
        if values:
            v.update(values)
        config = {
            "core": {"api_key": v["core"], "host": "127.0.0.1", "port": 8900},
            "llm": {"api_key": v["deepseek"], "base_url": "https://api.deepseek.com/v1"},
            "integrations": {
                "langbot": {"api_key": v["langbot_integration"], "bot_id": "fake-bot"}
            },
            "db_dsn": (
                f"postgresql://postgres:{v['pg']}@{PG_HOST}:{PG_PORT}/mobius_core"
            ),
            "services": {
                "gateway": {
                    "env": {
                        "MOBIUS_TASK_DSN": (
                            f"postgresql://postgres:{v['pg']}@{PG_HOST}:{PG_PORT}/mobius"
                        ),
                        "MOBIUS_KEEP_ME": "keep-value",
                    }
                },
                "langbot": {"cwd": str(self.langbot_dir)},
                "napcat": {"cwd": str(self.napcat_dir)},
            },
            "extra_top": {"keep": True},
        }
        self.config_file.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.env_file.write_text(
            "\n".join(
                [
                    f"PG_PASSWORD={v['pg']}",
                    f"DEEPSEEK_API_KEY={v['deepseek']}",
                    f"NAPCAT_HTTP_TOKEN={v['napcat_http']}",
                    f"PG_PORT={PG_PORT}",
                    "# 本行注释必须保留",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.yaml_file.write_text(
            YAML_TEMPLATE.format(**v), encoding="utf-8"
        )
        onebot = {
            "network": {
                "httpServers": [
                    {"name": "http", "enable": True, "port": 3000, "token": v["napcat_http"]}
                ],
                "websocketServers": [
                    {"name": "langbot-ws", "enable": True, "port": 3001, "token": v["napcat_ws"]}
                ],
                "websocketClients": [],
            },
            "musicSignUrl": "",
            "keep": 123,
        }
        self.onebot_file.write_text(
            json.dumps(onebot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.webui_file.write_text(
            json.dumps(
                {"host": "::", "port": 6099, "token": v["napcat_webui"], "theme": "auto"},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def write_secrets(self, data: dict) -> None:
        self.secrets_file.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def read_secrets(self) -> dict:
        return json.loads(self.secrets_file.read_text(encoding="utf-8"))

    # -- 命令 ------------------------------------------------------------
    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=str(ROOT),
            env=self.env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )

    def close(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


def _all_files(fixture: Fixture) -> list[Path]:
    return [
        fixture.config_file,
        fixture.env_file,
        fixture.yaml_file,
        fixture.onebot_file,
        fixture.webui_file,
    ]


def _stats(files: list[Path]) -> dict[str, tuple[int, int]]:
    return {str(path): (path.stat().st_mtime_ns, path.stat().st_size) for path in files}


# ---------------------------------------------------------------------------
# 1. 路径解析
# ---------------------------------------------------------------------------


def test_path_resolution() -> None:
    spec = importlib.util.spec_from_file_location("mobius_secrets_rotate", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    tmp = Path(tempfile.mkdtemp(prefix="secrets-path-test-"))
    saved = {key: os.environ.get(key) for key in ("MOBIUS_SECRETS_FILE", "MOBIUS_CONFIG")}
    try:
        for key in saved:
            os.environ.pop(key, None)
        root = tmp
        # (a) 环境变量最优先
        explicit = tmp / "explicit-secrets.json"
        os.environ["MOBIUS_SECRETS_FILE"] = str(explicit)
        check("环境变量 MOBIUS_SECRETS_FILE 优先", module.secrets_path(root) == explicit)

        # (b) config.json 顶层 secrets_file（相对仓库根）
        os.environ.pop("MOBIUS_SECRETS_FILE", None)
        config = {"secrets_file": "data/from-config.json"}
        (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        expected = root / "data" / "from-config.json"
        check("config.secrets_file 相对路径", module.secrets_path(root) == expected)

        # (c) 默认 data/secrets.json（存在时）
        (root / "config.json").unlink()
        (root / "data").mkdir(exist_ok=True)
        (root / "data" / "secrets.json").write_text("{}", encoding="utf-8")
        check(
            "默认 <仓库根>/data/secrets.json",
            module.secrets_path(root) == root / "data" / "secrets.json",
        )

        # (d) 最终回退 D:\AI\mobius\secrets.json
        (root / "data" / "secrets.json").unlink()
        check(
            "最终回退 D:\\AI\\mobius\\secrets.json",
            module.secrets_path(root) == Path(r"D:\AI\mobius\secrets.json"),
            str(module.secrets_path(root)),
        )

        # (e) 与 core/secrets.py 的路径约定一致（若该模块可导入）
        try:
            from core import secrets as core_secrets

            os.environ["MOBIUS_SECRETS_FILE"] = str(explicit)
            same_env = module.secrets_path(root) == core_secrets.secrets_path(root)
            os.environ.pop("MOBIUS_SECRETS_FILE", None)
            (root / "data" / "secrets.json").write_text("{}", encoding="utf-8")
            same_default = module.secrets_path(root) == core_secrets.secrets_path(root)
            check("路径解析与 core/secrets.py 同口径", same_env and same_default)
        except Exception as exc:  # noqa: BLE001 - Core 层并发维护时不影响本测试
            print(f"  NOTE  跳过 core/secrets.py 约定比对：{type(exc).__name__}: {exc}")
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2. init 反推 + 幂等
# ---------------------------------------------------------------------------


def test_init_derives_and_is_idempotent() -> None:
    fixture = Fixture()
    try:
        fixture.write_views()
        result = fixture.run("init")
        check("init 退出码 0", result.returncode == 0, result.stderr[-500:])

        data = fixture.read_secrets()
        pairs = {
            "pg.password": ("pg", "password", FIX["pg"]),
            "deepseek.api_key": ("deepseek", "api_key", FIX["deepseek"]),
            "core.api_key": ("core", "api_key", FIX["core"]),
            "langbot.global_api_key": ("langbot", "global_api_key", FIX["langbot_global"]),
            "langbot.secret": ("langbot", "secret", FIX["langbot_secret"]),
            "langbot.recovery_key": ("langbot", "recovery_key", FIX["langbot_recovery"]),
            "langbot.integration_api_key": (
                "langbot",
                "integration_api_key",
                FIX["langbot_integration"],
            ),
            "napcat.http_token": ("napcat", "http_token", FIX["napcat_http"]),
            "napcat.ws_token": ("napcat", "ws_token", FIX["napcat_ws"]),
            "napcat.webui_token": ("napcat", "webui_token", FIX["napcat_webui"]),
        }
        for label, (section, key, expected) in pairs.items():
            actual = data.get(section, {}).get(key)
            check(f"init 反推 {label}", actual == expected, f"{actual!r} != {expected!r}")
        check("init 写入 version=1", data.get("version") == 1)

        # 输出不含完整密钥
        merged = result.stdout + result.stderr
        leaked = [value for value in FIX.values() if value and value in merged]
        check("init 输出不含完整密钥", not leaked, str(leaked))

        # 幂等：第二次 init 不改文件、不改 mtime
        stat_before = fixture.secrets_file.stat()
        second = fixture.run("init")
        stat_after = fixture.secrets_file.stat()
        check("第二次 init 退出码 0", second.returncode == 0)
        check("第二次 init 提示已存在", "已存在" in second.stdout, second.stdout[-200:])
        check(
            "init 幂等：内容与 mtime 不变",
            stat_before.st_mtime_ns == stat_after.st_mtime_ns
            and stat_before.st_size == stat_after.st_size,
        )
        check("init 不创建备份（未修改任何消费方）", not fixture.backup_dir.exists())
    finally:
        fixture.close()


# ---------------------------------------------------------------------------
# 2b. init 来源优先级：config > .env > YAML > NapCat（按字段可用来源）
# ---------------------------------------------------------------------------


def test_init_source_priority() -> None:
    fixture = Fixture()
    try:
        fixture.write_views()
        env_pg = _pad("envpg", 32)
        env_deepseek = "sk-" + _pad("envds", 32)
        env_text = fixture.env_file.read_text(encoding="utf-8")
        env_text = re.sub(r"(?m)^PG_PASSWORD=.*$", f"PG_PASSWORD={env_pg}", env_text)
        env_text = re.sub(
            r"(?m)^DEEPSEEK_API_KEY=.*$", f"DEEPSEEK_API_KEY={env_deepseek}", env_text
        )
        fixture.env_file.write_text(env_text, encoding="utf-8")
        # napcat http：.env 与 onebot11 json 都提供，.env 应优先
        json_http = _pad("jsonhttp", 32)
        onebot = json.loads(fixture.onebot_file.read_text(encoding="utf-8"))
        onebot["network"]["httpServers"][0]["token"] = json_http
        fixture.onebot_file.write_text(
            json.dumps(onebot, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        result = fixture.run("init")
        check("优先级场景 init 退出码 0", result.returncode == 0, result.stderr[-300:])
        data = fixture.read_secrets()
        check(
            "init 优先级：PG 取 config db_dsn 而非 .env",
            data["pg"]["password"] == FIX["pg"],
            data["pg"]["password"],
        )
        check(
            "init 优先级：DeepSeek 取 config.llm 而非 .env",
            data["deepseek"]["api_key"] == FIX["deepseek"],
        )
        check(
            "init 优先级：NapCat http 取 .env 而非 onebot11 json",
            data["napcat"]["http_token"] == FIX["napcat_http"],
            data["napcat"]["http_token"],
        )
        check(
            "init 优先级：NapCat ws 取自 onebot11 json",
            data["napcat"]["ws_token"] == FIX["napcat_ws"],
        )
    finally:
        fixture.close()


# ---------------------------------------------------------------------------
# 3. show 掩码
# ---------------------------------------------------------------------------


def test_show_masks_values() -> None:
    fixture = Fixture()
    try:
        fixture.write_views()
        fixture.run("init")
        result = fixture.run("show")
        output = result.stdout + result.stderr
        check("show 退出码 0", result.returncode == 0, output[-300:])
        check("show 输出 secrets 路径", str(fixture.secrets_file) in output)
        check("show 输出 drift 状态", "drift" in output.lower(), output[-200:])
        check("show 使用掩码符号 …", "…" in output)
        leaked = [value for value in FIX.values() if value and value in output]
        check("show 不泄露任何完整密钥", not leaked, str(leaked))
        for label in ("pg.password", "deepseek.api_key", "napcat.webui_token"):
            check(f"show 列出 {label}", label in output)
    finally:
        fixture.close()


# ---------------------------------------------------------------------------
# 4. check 检出 drift -> sync 修复
# ---------------------------------------------------------------------------


def test_check_detects_drift_then_sync_repairs() -> None:
    fixture = Fixture()
    try:
        fixture.write_views()
        fixture.run("init")

        clean = fixture.run("check")
        check("check 初始一致退出码 0", clean.returncode == 0, clean.stdout[-800:])
        check("check 初始 DRIFT=0", "DRIFT=0" in clean.stdout, clean.stdout[-300:])
        check("check 输出逐项状态", "[OK" in clean.stdout and "[SKIP" in clean.stdout)

        # 人为制造 drift：config / .env / yaml / napcat 各改一处
        config = json.loads(fixture.config_file.read_text(encoding="utf-8"))
        config["llm"]["api_key"] = "sk-DRIFTED0000000000000000000000000"
        fixture.config_file.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        env_text = fixture.env_file.read_text(encoding="utf-8")
        fixture.env_file.write_text(
            re.sub(r"(?m)^PG_PASSWORD=.*$", "PG_PASSWORD=drifted-pg-password", env_text),
            encoding="utf-8",
        )
        yaml_text = fixture.yaml_file.read_text(encoding="utf-8")
        fixture.yaml_file.write_text(
            yaml_text.replace(FIX["langbot_global"], "drifted-global-key"),
            encoding="utf-8",
        )
        onebot = json.loads(fixture.onebot_file.read_text(encoding="utf-8"))
        onebot["network"]["websocketServers"][0]["token"] = "drifted-ws-token"
        fixture.onebot_file.write_text(
            json.dumps(onebot, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        drifted = fixture.run("check")
        check("check 检出 drift 退出码 1", drifted.returncode == 1, str(drifted.returncode))
        check("check 输出 DRIFT 行", "DRIFT" in drifted.stdout)
        check(
            "check 同时报告多个消费方 drift",
            all(name in drifted.stdout for name in (".env", "config.json", "langbot/config.yaml", "napcat/")),
            drifted.stdout[-1200:],
        )
        check(
            "check 不打印完整密钥",
            not any(value and value in drifted.stdout for value in FIX.values()),
        )

        synced = fixture.run("sync")
        check("sync 退出码 0", synced.returncode == 0, synced.stdout[-800:])
        check("sync 报告 UPDATE", "UPDATE" in synced.stdout, synced.stdout[-500:])
        check("sync 在 PG 不可连时告警", "WARN" in synced.stdout and "langbot-db" in synced.stdout)
        check(
            "sync 不打印完整密钥",
            not any(value and value in synced.stdout for value in FIX.values()),
        )

        fixed = fixture.run("check")
        check("sync 后 check 通过", fixed.returncode == 0, fixed.stdout[-800:])

        repaired_config = json.loads(fixture.config_file.read_text(encoding="utf-8"))
        check(
            "sync 修复 config.llm.api_key",
            repaired_config["llm"]["api_key"] == FIX["deepseek"],
        )
        check(
            "sync 保留 config 其它字段",
            repaired_config["services"]["gateway"]["env"]["MOBIUS_KEEP_ME"] == "keep-value"
            and repaired_config.get("extra_top") == {"keep": True},
        )
        check(
            "sync 修复 .env PG_PASSWORD",
            f"PG_PASSWORD={FIX['pg']}" in fixture.env_file.read_text(encoding="utf-8"),
        )
        check(
            "sync 修复 LangBot global_api_key",
            f"global_api_key: {FIX['langbot_global']}"
            in fixture.yaml_file.read_text(encoding="utf-8"),
        )
        repaired_onebot = json.loads(fixture.onebot_file.read_text(encoding="utf-8"))
        check(
            "sync 修复 NapCat websocket token",
            repaired_onebot["network"]["websocketServers"][0]["token"] == FIX["napcat_ws"],
        )

        # 同步幂等：再跑一次内容/mtime 均不变
        stats_before = _stats(_all_files(fixture))
        again = fixture.run("sync")
        stats_after = _stats(_all_files(fixture))
        check("第二次 sync 退出码 0", again.returncode == 0)
        check("第二次 sync 全部 UNCHANGED", "UNCHANGED" in again.stdout)
        check("sync 幂等：消费方 mtime/大小不变", stats_before == stats_after, str(stats_before))

        # 原子写无临时文件残留
        leftovers = [
            path
            for target in _all_files(fixture)
            for path in target.parent.glob(".*.tmp")
        ]
        check("无临时文件残留", not leftovers, str(leftovers))
    finally:
        fixture.close()


# ---------------------------------------------------------------------------
# 5. rotate 生成规则 / --value / --sync / --no-backup
# ---------------------------------------------------------------------------


def test_rotate_generation_and_sync() -> None:
    fixture = Fixture()
    try:
        fixture.write_views()
        fixture.run("init")
        original_secrets = fixture.secrets_file.read_text(encoding="utf-8")

        rules: dict[str, str] = {
            "pg": r"^[A-Za-z0-9]{32}$",
            "core": r"^[A-Za-z0-9]{24}$",
            "langbot-global": r"^[A-Za-z0-9]{24}$",
            "langbot-integration": r"^[A-Za-z0-9]{24}$",
            "langbot-secret": r"^[0-9a-f]{32}$",
            "langbot-recovery": r"^[A-Za-z0-9]{8}$",
            "napcat-http": r"^[0-9a-f]{32}$",
            "napcat-ws": r"^[0-9a-f]{32}$",
            "napcat-webui": r"^[0-9a-f]{12}$",
        }
        values: dict[str, str] = {}
        for key in rules:
            result = fixture.run("rotate", "--key", key)
            check(f"rotate --key {key} 退出码 0", result.returncode == 0, result.stderr[-300:])
            data = fixture.read_secrets()
            labels = {
                "pg": ("pg", "password"),
                "core": ("core", "api_key"),
                "langbot-global": ("langbot", "global_api_key"),
                "langbot-integration": ("langbot", "integration_api_key"),
                "langbot-secret": ("langbot", "secret"),
                "langbot-recovery": ("langbot", "recovery_key"),
                "napcat-http": ("napcat", "http_token"),
                "napcat-ws": ("napcat", "ws_token"),
                "napcat-webui": ("napcat", "webui_token"),
            }
            value = data[labels[key][0]][labels[key][1]]
            values[key] = value
            check(
                f"rotate 生成 {key} 形状正确",
                bool(re.fullmatch(rules[key], value)),
                value,
            )
            check(
                "rotate 输出掩码而非完整值",
                value not in (result.stdout + result.stderr),
            )

        rotated_secret_backups = [
            path for path in fixture.backup_dir.rglob("secrets.json") if path.is_file()
        ]
        check(
            "rotate 写前备份 secrets.json",
            any(
                path.read_text(encoding="utf-8") == original_secrets
                for path in rotated_secret_backups
            ),
            str(rotated_secret_backups[:2]),
        )

        # deepseek 无法自动生成，必须显式 --value
        no_value = fixture.run("rotate", "--key", "deepseek")
        check("rotate deepseek 无 --value 报错", no_value.returncode != 0, str(no_value.returncode))
        check("报错提示需要 --value", "--value" in (no_value.stdout + no_value.stderr))

        explicit = "sk-explicit0000000000000000000000000"
        with_value = fixture.run("rotate", "--key", "deepseek", "--value", explicit)
        check("rotate --value 退出码 0", with_value.returncode == 0, with_value.stderr[-300:])
        check(
            "rotate --value 写回 secrets.json",
            fixture.read_secrets()["deepseek"]["api_key"] == explicit,
        )
        check("rotate --value 输出不含完整密钥", explicit not in with_value.stdout)

        # 未加 --sync 时消费方不应被改写
        config_before = fixture.config_file.read_text(encoding="utf-8")
        check("rotate 无 --sync 不改消费方", explicit not in config_before)

        # --sync 一次把所有已轮换值写回各视图
        synced = fixture.run("rotate", "--key", "deepseek", "--value", explicit, "--sync")
        check("rotate --sync 退出码 0", synced.returncode == 0, synced.stdout[-800:])
        config_after = json.loads(fixture.config_file.read_text(encoding="utf-8"))
        check("rotate --sync 写回 config", config_after["llm"]["api_key"] == explicit)
        check(
            "rotate --sync 写回 .env PG",
            f"PG_PASSWORD={values['pg']}"
            in fixture.env_file.read_text(encoding="utf-8"),
        )
        check(
            "rotate --sync 写回 LangBot secret",
            f"secret: {values['langbot-secret']}"
            in fixture.yaml_file.read_text(encoding="utf-8"),
        )
        onebot = json.loads(fixture.onebot_file.read_text(encoding="utf-8"))
        check(
            "rotate --sync 写回 NapCat http token",
            onebot["network"]["httpServers"][0]["token"] == values["napcat-http"],
        )
        final_check = fixture.run("check")
        check("rotate --sync 后 check 通过", final_check.returncode == 0, final_check.stdout[-600:])

        # --no-backup 不产生备份目录
        backup_dirs_before = {path for path in fixture.backup_dir.rglob("*") if path.is_dir()}
        no_backup = fixture.run("rotate", "--key", "core", "--no-backup")
        check("rotate --no-backup 退出码 0", no_backup.returncode == 0)
        backup_dirs_after = {path for path in fixture.backup_dir.rglob("*") if path.is_dir()}
        check(
            "rotate --no-backup 未新增备份",
            backup_dirs_before == backup_dirs_after,
            f"{len(backup_dirs_before)} -> {len(backup_dirs_after)}",
        )

        # 未知 key
        unknown = fixture.run("rotate", "--key", "no-such-key")
        check("未知 key 退出码 2", unknown.returncode == 2, str(unknown.returncode))
    finally:
        fixture.close()


# ---------------------------------------------------------------------------
# 6. JSON / YAML 行级替换：只改目标，保留其余内容
# ---------------------------------------------------------------------------


def test_targeted_replacement_preserves_other_content() -> None:
    fixture = Fixture()
    try:
        fixture.write_views()
        fixture.run("init")
        # 先 sync 一次，让可选 .env LANGBOT_API_KEY 也落盘，后续才可断言“无变化不改写”
        first_sync = fixture.run("sync")
        check("基线 sync 退出码 0", first_sync.returncode == 0, first_sync.stdout[-400:])
        baseline_yaml = fixture.yaml_file.read_text(encoding="utf-8")
        baseline_env = fixture.env_file.read_text(encoding="utf-8")
        baseline_onebot = fixture.onebot_file.read_text(encoding="utf-8")
        baseline_webui = fixture.webui_file.read_text(encoding="utf-8")
        baseline_config = json.loads(fixture.config_file.read_text(encoding="utf-8"))

        # (a) 只改 core：config 其他字段与 yaml/env/napcat 都不应被重写
        data = fixture.read_secrets()
        data["core"]["api_key"] = "coreNEW00000000000000000"
        fixture.write_secrets(data)
        synced = fixture.run("sync")
        check("改 core 后 sync 退出码 0", synced.returncode == 0, synced.stdout[-400:])
        new_config = json.loads(fixture.config_file.read_text(encoding="utf-8"))
        check("core 已更新", new_config["core"]["api_key"] == "coreNEW00000000000000000")
        check(
            "config 非目标字段保持不变",
            new_config["llm"] == baseline_config["llm"]
            and new_config["services"] == baseline_config["services"]
            and new_config["extra_top"] == baseline_config["extra_top"],
        )
        check("无 yaml 目标变化时 yaml 字节不变", fixture.yaml_file.read_text(encoding="utf-8") == baseline_yaml)
        check("无 env 目标变化时 env 字节不变", fixture.env_file.read_text(encoding="utf-8") == baseline_env)
        check(
            "无 napcat 目标变化时 napcat 字节不变",
            fixture.onebot_file.read_text(encoding="utf-8") == baseline_onebot
            and fixture.webui_file.read_text(encoding="utf-8") == baseline_webui,
        )

        # (b) 只改 pg：DSN、.env、yaml 的 postgresql.password 更新，但无关 password 不动
        data = fixture.read_secrets()
        data["pg"]["password"] = "pgNEW000000000000000000000000000"
        fixture.write_secrets(data)
        synced = fixture.run("sync")
        check("改 pg 后 sync 退出码 0", synced.returncode == 0, synced.stdout[-400:])
        config_pg = json.loads(fixture.config_file.read_text(encoding="utf-8"))
        check(
            "pg 更新 db_dsn",
            config_pg["db_dsn"]
            == f"postgresql://postgres:pgNEW000000000000000000000000000@{PG_HOST}:{PG_PORT}/mobius_core",
            config_pg["db_dsn"],
        )
        check(
            "pg 更新 gateway DSN",
            config_pg["services"]["gateway"]["env"]["MOBIUS_TASK_DSN"]
            == f"postgresql://postgres:pgNEW000000000000000000000000000@{PG_HOST}:{PG_PORT}/mobius",
        )
        new_yaml = fixture.yaml_file.read_text(encoding="utf-8")
        check(
            "yaml database.postgresql.password 已更新",
            f"        password: pgNEW000000000000000000000000000" in new_yaml,
        )
        check("yaml 无关 password 保持不变", "        password: keep-this-password" in new_yaml)
        check("yaml 缩进与其余行保持不变", new_yaml.replace(
            "        password: pgNEW000000000000000000000000000",
            "        password: " + FIX["pg"],
            1,
        ) == baseline_yaml)
        check(
            "env 注释保留",
            "# 本行注释必须保留" in fixture.env_file.read_text(encoding="utf-8"),
        )
    finally:
        fixture.close()


# ---------------------------------------------------------------------------
# 7. PG 不可连：check SKIP、sync WARN 但仍更新文件
# ---------------------------------------------------------------------------


def test_db_unreachable_is_skip_not_crash() -> None:
    fixture = Fixture()
    try:
        fixture.write_views()
        fixture.run("init")
        result = fixture.run("check")
        check("DB 不可连 check 退出码 0", result.returncode == 0, result.stdout[-800:])
        check("DB 不可连 check 标记 SKIP", "SKIP" in result.stdout and "langbot-db" in result.stdout)
        check("DB 不可连 check 无 DRIFT", "DRIFT=0" in result.stdout, result.stdout[-300:])

        synced = fixture.run("sync")
        output = synced.stdout + synced.stderr
        check("DB 不可连 sync 退出码 0", synced.returncode == 0, output[-800:])
        check("DB 不可连 sync 明确告警", "WARN" in output and "langbot-db" in output)
        check(
            "DB 不可连 sync 仍更新文件视图",
            f"PG_PASSWORD={FIX['pg']}" in fixture.env_file.read_text(encoding="utf-8"),
        )
    finally:
        fixture.close()


# ---------------------------------------------------------------------------
# 8. 原子写与备份
# ---------------------------------------------------------------------------


def test_atomic_write_and_backup() -> None:
    fixture = Fixture()
    try:
        fixture.write_views()
        fixture.run("init")
        original_config = fixture.config_file.read_text(encoding="utf-8")

        # 改 deepseek 后 sync，config 应被备份为改动前版本
        data = fixture.read_secrets()
        data["deepseek"]["api_key"] = "sk-newkey0000000000000000000000000"
        fixture.write_secrets(data)
        synced = fixture.run("sync")
        check("备份场景 sync 退出码 0", synced.returncode == 0, synced.stdout[-400:])

        backups = [path for path in fixture.backup_dir.rglob("config.json") if path.is_file()]
        check("sync 写前生成备份", bool(backups), str(list(fixture.backup_dir.rglob('*'))))
        if backups:
            check(
                "备份内容为改动前版本",
                any(path.read_text(encoding="utf-8") == original_config for path in backups),
            )

        # 原子写：目标目录无 .tmp 残留
        leftovers = list(fixture.config_file.parent.glob(".*.tmp"))
        check("原子写无临时文件残留", not leftovers, str(leftovers))
        check(
            "写后文件可被正常解析",
            isinstance(json.loads(fixture.config_file.read_text(encoding="utf-8")), dict),
        )

        # 连续 sync 同一 secrets 不再产生新备份
        backup_stamps_before = sorted(
            path.name for path in fixture.backup_dir.iterdir() if path.is_dir()
        )
        noop = fixture.run("sync")
        backup_stamps_after = sorted(
            path.name for path in fixture.backup_dir.iterdir() if path.is_dir()
        )
        check("无变化 sync 退出码 0", noop.returncode == 0)
        check("无变化 sync 不产生新备份", backup_stamps_before == backup_stamps_after)
    finally:
        fixture.close()


# ---------------------------------------------------------------------------
# 9. 脱敏示例 schema
# ---------------------------------------------------------------------------


def test_example_schema() -> None:
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    check("示例存在且为合法 JSON", isinstance(data, dict))
    check("示例 version=1", data.get("version") == 1)
    expected_sections = {"version", "pg", "deepseek", "core", "langbot", "napcat"}
    check("示例顶层字段与 schema 一致", set(data) == expected_sections, str(sorted(data)))
    check("示例 pg 字段", set(data["pg"]) == {"password"})
    check("示例 deepseek 字段", set(data["deepseek"]) == {"api_key"})
    check("示例 core 字段", set(data["core"]) == {"api_key"})
    check(
        "示例 langbot 字段",
        set(data["langbot"])
        == {"global_api_key", "secret", "recovery_key", "integration_api_key"},
    )
    check(
        "示例 napcat 字段",
        set(data["napcat"]) == {"http_token", "ws_token", "webui_token"},
    )
    check(
        "示例取值形状",
        bool(re.fullmatch(r"[A-Za-z0-9]{32}", data["pg"]["password"]))
        and data["deepseek"]["api_key"].startswith("sk-")
        and bool(re.fullmatch(r"[0-9a-f]{32}", data["langbot"]["secret"]))
        and len(data["langbot"]["recovery_key"]) == 8
        and bool(re.fullmatch(r"[0-9a-f]{32}", data["napcat"]["http_token"]))
        and bool(re.fullmatch(r"[0-9a-f]{32}", data["napcat"]["ws_token"]))
        and bool(re.fullmatch(r"[0-9a-f]{12}", data["napcat"]["webui_token"])),
    )
    merged = json.dumps(data, ensure_ascii=False)
    leaked = [value for value in FIX.values() if value and value in merged]
    check("示例不含测试用密钥", not leaked)


# ---------------------------------------------------------------------------


def main() -> int:
    print("==> 密钥 SSOT 轮换脚本回归测试")
    tests = (
        ("路径解析", test_path_resolution),
        ("init 反推与幂等", test_init_derives_and_is_idempotent),
        ("init 来源优先级", test_init_source_priority),
        ("show 掩码", test_show_masks_values),
        ("check 检出 drift / sync 修复", test_check_detects_drift_then_sync_repairs),
        ("rotate 生成规则与写回", test_rotate_generation_and_sync),
        ("JSON/YAML 行级替换", test_targeted_replacement_preserves_other_content),
        ("PG 不可连 SKIP/WARN", test_db_unreachable_is_skip_not_crash),
        ("原子写与备份", test_atomic_write_and_backup),
        ("脱敏示例 schema", test_example_schema),
    )
    for title, func in tests:
        print(f"\n[{title}]")
        try:
            func()
        except Exception as exc:  # noqa: BLE001 - 单个用例异常不中断其余用例
            import traceback

            traceback.print_exc()
            FAILED.append((f"{title} 用例异常", f"{type(exc).__name__}: {exc}"))
            print(f"  [FAIL] {title} 用例异常  {type(exc).__name__}: {exc}")

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    for name, detail in FAILED:
        print(f"  FAIL: {name}  {detail}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
