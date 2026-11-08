"""Möbius 密钥 SSOT 轮换脚本。

secrets.json 是密钥的唯一可编辑源（SSOT）；本脚本负责把各消费方视图
（.env / config.json / LangBot data/config.yaml / LangBot PG / NapCat 配置）
生成或刷新成同一份值，避免轮换时手改多处。

命令：
  init     secrets.json 不存在时，从现有消费方视图反推生成；已存在则不动
  show     只打印掩码、文件路径与 drift 摘要，绝不打印完整值
  check    逐项比较消费方与 SSOT；一致退出 0，有 DRIFT 退出 1
  sync     把 SSOT 原子写回所有消费方（PG 不可连时告警，文件视图照常更新）
  rotate   生成或指定单个密钥并写回 SSOT；--sync 时再写回消费方

路径解析（与 core/secrets.py 共用约定）：
  MOBIUS_SECRETS_FILE
  → config.json 顶层 secrets_file（相对路径按仓库根解析）
  → <仓库根>/data/secrets.json（存在时）
  → D:\\AI\\mobius\\secrets.json
仓库根 = 本脚本上两级（ops/secrets/rotate.py -> mobius）；可用 MOBIUS_ROOT 覆盖。

测试用环境变量覆盖（不改任何生产文件）：
  MOBIUS_SECRETS_FILE  指定 SSOT 文件
  MOBIUS_CONFIG        config.json
  MOBIUS_ENV_FILE      .env（默认 <仓库根>/.env，否则 D:\\AI\\mobius\\.env）
  MOBIUS_LANGBOT_DIR   LangBot 根（默认取 config services.langbot.cwd）
  MOBIUS_NAPCAT_DIR    NapCat 根（默认取 config services.napcat.cwd）
  MOBIUS_PG_HOST/PORT  PG 连接（默认 127.0.0.1:55432）
  MOBIUS_BACKUP_DIR    备份根（默认 <仓库根>/backups/security，测试用）

安全约定：
  - 任何输出都经过掩码，不打印完整密钥；
  - 写文件前先备份到 backups/security/<timestamp>/，再“临时文件 + os.replace”原子写；
  - 生成内容与现有内容一致时不写，保持 mtime 不变（sync/rotate 幂等）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets as pysecrets
import shutil
import string
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SECRETS_FALLBACK = Path(r"D:\AI\mobius\secrets.json")
DEFAULT_ENV_FALLBACK = Path(r"D:\AI\mobius\.env")
DEFAULT_PG_HOST = "127.0.0.1"
DEFAULT_PG_PORT = "55432"
SECRETS_VERSION = 1

SECTION_ORDER = ("version", "pg", "deepseek", "core", "langbot", "napcat")
FIELD_ORDER: tuple[tuple[str, str], ...] = (
    ("pg", "password"),
    ("deepseek", "api_key"),
    ("core", "api_key"),
    ("langbot", "global_api_key"),
    ("langbot", "secret"),
    ("langbot", "recovery_key"),
    ("langbot", "integration_api_key"),
    ("napcat", "http_token"),
    ("napcat", "ws_token"),
    ("napcat", "webui_token"),
)

# .env 映射：环境变量名 -> secrets 路径；最后一项表示可选（缺失不算 drift）
ENV_TARGETS: tuple[tuple[str, tuple[str, str], bool], ...] = (
    ("PG_PASSWORD", ("pg", "password"), False),
    ("DEEPSEEK_API_KEY", ("deepseek", "api_key"), False),
    ("NAPCAT_HTTP_TOKEN", ("napcat", "http_token"), False),
    ("LANGBOT_API_KEY", ("langbot", "integration_api_key"), True),
)

# LangBot data/config.yaml 目标：secrets 路径 -> 候选 YAML 路径（兼容扁平旧结构）
YAML_TARGETS: tuple[tuple[tuple[str, str], tuple[tuple[str, ...], ...]], ...] = (
    (("langbot", "global_api_key"), (("api", "global_api_key"), ("global_api_key",))),
    (("langbot", "secret"), (("system", "jwt", "secret"), ("secret",))),
    (("langbot", "recovery_key"), (("system", "recovery_key"), ("recovery_key",))),
    (("pg", "password"), (("database", "postgresql", "password"), ("database", "password"))),
)

# rotate 支持的密钥名 -> (secrets 路径, 生成类型)
ROTATE_KEYS: dict[str, tuple[tuple[str, str], str]] = {
    "pg": (("pg", "password"), "alnum32"),
    "deepseek": (("deepseek", "api_key"), "deepseek"),
    "core": (("core", "api_key"), "alnum24"),
    "langbot-global": (("langbot", "global_api_key"), "alnum24"),
    "langbot-secret": (("langbot", "secret"), "hex32"),
    "langbot-recovery": (("langbot", "recovery_key"), "alnum8"),
    "langbot-integration": (("langbot", "integration_api_key"), "alnum24"),
    "napcat-http": (("napcat", "http_token"), "hex32"),
    "napcat-ws": (("napcat", "ws_token"), "hex32"),
    "napcat-webui": (("napcat", "webui_token"), "hex12"),
}

_ALNUM = string.ascii_letters + string.digits
_HEX = string.hexdigits.lower()[:16]
_YAML_KEY_RE = re.compile(r"^([ \t]*)([A-Za-z_][\w-]*):([ \t]*)(.*)$")
_EXPAND_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


# ---------------------------------------------------------------------------
# 路径解析（与 core/secrets.py 保持一致）
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    env = os.environ.get("MOBIUS_ROOT", "").strip()
    if env and Path(env).is_dir():
        return Path(env)
    return REPO_ROOT


def config_path(root: Path | None = None) -> Path:
    env = os.environ.get("MOBIUS_CONFIG", "").strip()
    if env:
        return Path(env)
    return (root or repo_root()) / "config.json"


def secrets_path(root: Path | None = None) -> Path:
    """按优先级解析 secrets.json 路径（不保证文件存在）。"""
    env = os.environ.get("MOBIUS_SECRETS_FILE", "").strip()
    if env:
        return Path(env)
    root = root or repo_root()
    cfg_file = config_path(root)
    try:
        cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
        rel = str(cfg.get("secrets_file") or "").strip()
        if rel:
            p = Path(rel)
            return p if p.is_absolute() else (root / p)
    except (OSError, ValueError):
        pass
    candidate = root / "data" / "secrets.json"
    if candidate.is_file():
        return candidate
    return DEFAULT_SECRETS_FALLBACK


def env_path(root: Path | None = None) -> Path:
    env = os.environ.get("MOBIUS_ENV_FILE", "").strip()
    if env:
        return Path(env)
    root = root or repo_root()
    candidate = root / ".env"
    if candidate.is_file():
        return candidate
    return DEFAULT_ENV_FALLBACK


def backup_root() -> Path:
    env = os.environ.get("MOBIUS_BACKUP_DIR", "").strip()
    if env:
        return Path(env)
    return repo_root() / "backups" / "security"


def pg_host() -> str:
    return os.environ.get("MOBIUS_PG_HOST", "").strip() or DEFAULT_PG_HOST


def pg_port() -> str:
    return os.environ.get("MOBIUS_PG_PORT", "").strip() or DEFAULT_PG_PORT


def build_dsn(dbname: str, password: str) -> str:
    return (
        f"postgresql://postgres:{quote(password, safe='')}"
        f"@{pg_host()}:{pg_port()}/{dbname}"
    )


def load_config(root: Path | None = None) -> dict:
    data = read_json(config_path(root))
    return data if isinstance(data, dict) else {}


def expand_vars(value: str, cfg: dict, root: Path) -> str:
    """展开 config.json 的 ${VAR}（paths 优先，其次环境变量）。"""
    if not isinstance(value, str):
        return value
    paths = cfg.get("paths") if isinstance(cfg.get("paths"), dict) else {}

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "ROOT":
            return str(root)
        if name == "USERPROFILE":
            return os.environ.get("USERPROFILE", "")
        if isinstance(paths.get(name), str):
            return paths[name]
        return os.environ.get(name, match.group(0))

    return _EXPAND_RE.sub(repl, value)


def _service_cwd(service: str, root: Path | None = None) -> Path | None:
    cfg = load_config(root)
    cwd = ((cfg.get("services") or {}).get(service) or {}).get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return None
    return Path(expand_vars(cwd.strip(), cfg, root or repo_root()))


def langbot_dir(root: Path | None = None) -> Path | None:
    env = os.environ.get("MOBIUS_LANGBOT_DIR", "").strip()
    if env:
        return Path(env)
    return _service_cwd("langbot", root)


def napcat_dir(root: Path | None = None) -> Path | None:
    env = os.environ.get("MOBIUS_NAPCAT_DIR", "").strip()
    if env:
        return Path(env)
    return _service_cwd("napcat", root)


def langbot_yaml_path(root: Path | None = None) -> Path | None:
    d = langbot_dir(root)
    if d is None:
        return None
    for candidate in (d / "data" / "config.yaml", d / "config.yaml"):
        if candidate.is_file():
            return candidate
    return d / "data" / "config.yaml"


# ---------------------------------------------------------------------------
# 基础 IO
# ---------------------------------------------------------------------------


def read_json(path: Path | None) -> object | None:
    if path is None:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def dump_json_text(data: object, original_text: str | None = None) -> str:
    """按仓库现有风格（2 空格缩进、非 ASCII 原样）序列化，保留末尾换行习惯。"""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if original_text is not None:
        if original_text.endswith("\n"):
            text += "\n"
    else:
        text += "\n"
    return text


def read_text(path: Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _split_lines(text: str) -> tuple[list[str], str, bool]:
    """返回 (无换行行列表, 换行符, 是否以换行结尾)。"""
    if text == "":
        return [], "\n", False
    nl = "\r\n" if "\r\n" in text else "\n"
    normalized = text.replace("\r\n", "\n")
    trailing = normalized.endswith("\n")
    if trailing:
        normalized = normalized[:-1]
    return normalized.split("\n"), nl, trailing


def _join_lines(lines: list[str], nl: str, trailing: bool) -> str:
    return nl.join(lines) + (nl if trailing else "")


def parse_env_text(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, value = s.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def read_env(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    text = read_text(path)
    return parse_env_text(text) if text is not None else {}


class WriteContext:
    """一次命令运行内的写上下文：统一备份时间戳与是否允许备份。"""

    def __init__(self, allow_backup: bool = True) -> None:
        self.allow_backup = allow_backup
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.root = backup_root()

    def backup(self, path: Path) -> Path | None:
        if not self.allow_backup or not path.is_file():
            return None
        dest = self.root / self.stamp / _backup_rel(path)
        counter = 1
        while dest.exists():
            dest = dest.with_name(f"{dest.name}.{counter}")
            counter += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        return dest


def _backup_rel(path: Path) -> Path:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root().resolve())
    except ValueError:
        pass
    parts = list(resolved.parts)
    if parts and parts[0].endswith(("\\", "/")):
        parts = parts[1:]
    safe = [re.sub(r"[^A-Za-z0-9_.-]+", "_", part) for part in parts]
    drive = resolved.drive.replace(":", "")
    if drive:
        safe.insert(0, drive)
    return Path("_external", *safe)


def write_if_changed(
    path: Path, text: str, ctx: WriteContext, *, allow_backup: bool | None = None
) -> bool:
    """内容相同则不写（保持 mtime）；否则备份 + 原子写。返回是否发生写入。"""
    path = Path(path)
    original = read_text(path)
    if original == text:
        return False
    backup_allowed = ctx.allow_backup if allow_backup is None else allow_backup
    if backup_allowed and original is not None:
        ctx.backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return True


# ---------------------------------------------------------------------------
# secrets.json 模型与掩码
# ---------------------------------------------------------------------------


def empty_secrets() -> dict:
    return {
        "version": SECRETS_VERSION,
        "pg": {"password": ""},
        "deepseek": {"api_key": ""},
        "core": {"api_key": ""},
        "langbot": {
            "global_api_key": "",
            "secret": "",
            "recovery_key": "",
            "integration_api_key": "",
        },
        "napcat": {"http_token": "", "ws_token": "", "webui_token": ""},
    }


def get_secret(data: dict, path: tuple[str, str]) -> str:
    node: object = data
    for key in path:
        if not isinstance(node, dict):
            return ""
        node = node.get(key)
    return node if isinstance(node, str) else ""


def set_secret(data: dict, path: tuple[str, str], value: str) -> None:
    node = data
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[path[-1]] = value


def ensure_shape(data: dict) -> dict:
    """补齐固定 schema（保留已有值/未知键），保证输出结构稳定。"""
    if not isinstance(data, dict):
        data = {}
    base = empty_secrets()
    for section in SECTION_ORDER:
        if section == "version":
            continue
        current = data.get(section)
        if not isinstance(current, dict):
            current = {}
            data[section] = current
        for key, default in base[section].items():
            if not isinstance(current.get(key), str):
                current[key] = default
    if not isinstance(data.get("version"), int):
        data["version"] = SECRETS_VERSION
    return data


def mask(value: str) -> str:
    """密钥掩码：只保留极短前后缀，长度不足以提示时全部打码。"""
    if not value:
        return "<empty>"
    n = len(value)
    if n <= 6:
        return "*" * n
    if n <= 10:
        return value[:2] + "…" + value[-2:]
    if value.startswith("sk-"):
        return value[:6] + "…" + value[-3:]
    return value[:3] + "…" + value[-3:]


def load_secrets(path: Path) -> dict | None:
    data = read_json(path)
    if not isinstance(data, dict):
        return None
    return ensure_shape(data)


# ---------------------------------------------------------------------------
# YAML（LangBot config.yaml）行级读写：保持其余内容与缩进不变
# ---------------------------------------------------------------------------


def _yaml_find(
    lines: list[str], candidates: tuple[tuple[str, ...], ...]
) -> tuple[int, str, str] | None:
    """按缩进栈定位映射路径，返回 (行号, 缩进, 冒号后的原文)。"""
    stack: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _YAML_KEY_RE.match(line)
        if not match:
            continue
        indent = len(match.group(1))
        key = match.group(2)
        while stack and indent <= stack[-1][0]:
            stack.pop()
        path = tuple(k for _, k in stack) + (key,)
        if path in candidates:
            return index, match.group(1), match.group(3) + match.group(4)
        stack.append((indent, key))
    return None


def _split_yaml_scalar(suffix: str) -> tuple[str, str, str, str]:
    """拆分 冒号后的空白/引号/值/尾注释，返回 (前导空白, 引号, 值, 尾部)。"""
    match = re.match(r"^([ \t]*)(.*)$", suffix, re.S)
    lead, rest = match.group(1), match.group(2)
    if rest[:1] in ("'", '"'):
        quote_char = rest[0]
        buf: list[str] = []
        index = 1
        while index < len(rest):
            char = rest[index]
            if quote_char == "'":
                if char == "'" and index + 1 < len(rest) and rest[index + 1] == "'":
                    buf.append("'")
                    index += 2
                    continue
                if char == "'":
                    return lead, quote_char, "".join(buf), rest[index + 1:]
            else:
                if char == "\\" and index + 1 < len(rest):
                    buf.append(rest[index + 1])
                    index += 2
                    continue
                if char == '"':
                    return lead, quote_char, "".join(buf), rest[index + 1:]
            buf.append(char)
            index += 1
        # 引号未闭合：按普通标量处理，避免吞掉整行
        return lead, "", rest, ""
    scalar = re.match(r"^([^\s#]*)(.*)$", rest, re.S)
    return lead, "", scalar.group(1), scalar.group(2)


def _yaml_read_value(suffix: str) -> str:
    return _split_yaml_scalar(suffix)[2]


def _yaml_rewrite_value(suffix: str, value: str) -> str:
    lead, quote_char, current, tail = _split_yaml_scalar(suffix)
    if quote_char == "" and current == value:
        return suffix
    if quote_char == "":
        safe = re.fullmatch(r"[A-Za-z0-9_.@/+=:-]*", value or "")
        if safe:
            return f"{lead}{value}{tail}"
        quote_char = "'"
    if quote_char == "'":
        escaped = value.replace("'", "''")
    else:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f"{lead}{quote_char}{escaped}{quote_char}{tail}"


def read_yaml_value(text: str, candidates: tuple[tuple[str, ...], ...]) -> str | None:
    lines = text.splitlines()
    found = _yaml_find(lines, candidates)
    if found is None:
        return None
    return _yaml_read_value(found[2])


def write_yaml_value(
    text: str, candidates: tuple[tuple[str, ...], ...], value: str
) -> tuple[str, bool]:
    """返回 (新文本, 是否找到目标行)。找不到目标行时原样返回。"""
    lines, nl, trailing = _split_lines(text)
    found = _yaml_find(lines, candidates)
    if found is None:
        return text, False
    index, indent, suffix = found
    lines[index] = f"{indent}{_yaml_path_key(lines[index])}:{_yaml_rewrite_value(suffix, value)}"
    return _join_lines(lines, nl, trailing), True


def _yaml_path_key(line: str) -> str:
    match = _YAML_KEY_RE.match(line)
    return match.group(2) if match else ""


# ---------------------------------------------------------------------------
# 消费方期望值
# ---------------------------------------------------------------------------


def config_expectations(secrets_data: dict) -> list[tuple[str, str, tuple[str, ...]]]:
    """返回 (展示字段, 期望值, config JSON 路径)。空密钥不参与。"""
    out: list[tuple[str, str, tuple[str, ...]]] = []
    pg = get_secret(secrets_data, ("pg", "password"))
    deepseek = get_secret(secrets_data, ("deepseek", "api_key"))
    core = get_secret(secrets_data, ("core", "api_key"))
    integration = get_secret(secrets_data, ("langbot", "integration_api_key"))
    if core:
        out.append(("core.api_key", core, ("core", "api_key")))
    if deepseek:
        out.append(("llm.api_key", deepseek, ("llm", "api_key")))
    if integration:
        out.append(
            (
                "integrations.langbot.api_key",
                integration,
                ("integrations", "langbot", "api_key"),
            )
        )
    if pg:
        out.append(("db_dsn", build_dsn("mobius_core", pg), ("db_dsn",)))
        out.append(
            (
                "services.gateway.env.MOBIUS_TASK_DSN",
                build_dsn("mobius", pg),
                ("services", "gateway", "env", "MOBIUS_TASK_DSN"),
            )
        )
    return out


def _nested_get(node: object, path: tuple[str, ...]) -> object:
    current = node
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _nested_set(node: dict, path: tuple[str, ...], value: str) -> None:
    current = node
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[path[-1]] = value


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


class Result:
    __slots__ = ("status", "consumer", "field", "detail")

    def __init__(self, status: str, consumer: str, field: str = "", detail: str = "") -> None:
        self.status = status
        self.consumer = consumer
        self.field = field
        self.detail = detail

    def render(self) -> str:
        detail = f"  ({self.detail})" if self.detail else ""
        return f"[{self.status:<5}] {self.consumer} {self.field}{detail}".rstrip()


def check_env(secrets_data: dict, root: Path | None = None) -> list[Result]:
    path = env_path(root)
    results: list[Result] = []
    text = read_text(path)
    if text is None:
        results.append(Result("DRIFT", ".env", "(file)", f"文件不存在: {path}"))
        return results
    current = parse_env_text(text)
    for env_key, secret_path, optional in ENV_TARGETS:
        expected = get_secret(secrets_data, secret_path)
        field = f"{env_key}"
        if not expected:
            results.append(Result("SKIP", ".env", field, "secrets 中值为空"))
            continue
        if env_key not in current:
            if optional:
                results.append(Result("SKIP", ".env", field, "可选，当前未配置"))
            else:
                results.append(Result("DRIFT", ".env", field, "缺失"))
            continue
        actual = current[env_key]
        if actual == expected:
            results.append(Result("OK", ".env", field))
        else:
            results.append(
                Result("DRIFT", ".env", field, f"env={mask(actual)} secrets={mask(expected)}")
            )
    return results


def check_config(secrets_data: dict, root: Path | None = None) -> list[Result]:
    path = config_path(root)
    results: list[Result] = []
    raw = read_text(path)
    if raw is None:
        results.append(Result("DRIFT", "config.json", "(file)", f"文件不存在: {path}"))
        return results
    cfg = read_json(path)
    if not isinstance(cfg, dict):
        results.append(Result("DRIFT", "config.json", "(file)", "JSON 解析失败"))
        return results
    for field, expected, json_path in config_expectations(secrets_data):
        current = _nested_get(cfg, json_path)
        if not isinstance(current, str):
            results.append(Result("DRIFT", "config.json", field, f"缺失 (期望 {mask(expected)})"))
        elif current == expected:
            results.append(Result("OK", "config.json", field))
        else:
            results.append(
                Result(
                    "DRIFT",
                    "config.json",
                    field,
                    f"config={mask(current)} secrets={mask(expected)}",
                )
            )
    return results


def check_langbot_yaml(secrets_data: dict, root: Path | None = None) -> list[Result]:
    path = langbot_yaml_path(root)
    results: list[Result] = []
    if path is None or not path.is_file():
        results.append(
            Result(
                "DRIFT",
                "langbot/config.yaml",
                "(file)",
                f"文件不存在: {path or '(LangBot 目录未解析到)'}",
            )
        )
        return results
    text = read_text(path)
    if text is None:
        results.append(Result("DRIFT", "langbot/config.yaml", "(file)", "读取失败"))
        return results
    for secret_path, candidates in YAML_TARGETS:
        expected = get_secret(secrets_data, secret_path)
        field = ".".join(secret_path)
        if not expected:
            results.append(Result("SKIP", "langbot/config.yaml", field, "secrets 中值为空"))
            continue
        current = read_yaml_value(text, candidates)
        if current is None:
            results.append(Result("DRIFT", "langbot/config.yaml", field, "YAML 中未找到该键"))
        elif current == expected:
            results.append(Result("OK", "langbot/config.yaml", field))
        else:
            results.append(
                Result(
                    "DRIFT",
                    "langbot/config.yaml",
                    field,
                    f"yaml={mask(current)} secrets={mask(expected)}",
                )
            )
    return results


def _napcat_onebot_files(napcat: Path) -> list[Path]:
    if not napcat.is_dir():
        return []
    return sorted(p for p in napcat.rglob("onebot11_*.json") if p.is_file())


def _napcat_webui_files(napcat: Path) -> list[Path]:
    if not napcat.is_dir():
        return []
    return sorted(p for p in napcat.rglob("webui.json") if p.is_file())


def check_napcat(secrets_data: dict, root: Path | None = None) -> list[Result]:
    results: list[Result] = []
    napcat = napcat_dir(root)
    if napcat is None or not napcat.is_dir():
        results.append(
            Result(
                "DRIFT",
                "napcat",
                "(dir)",
                f"目录不存在: {napcat or '(config services.napcat.cwd 未解析到)'}",
            )
        )
        return results
    http_expected = get_secret(secrets_data, ("napcat", "http_token"))
    ws_expected = get_secret(secrets_data, ("napcat", "ws_token"))
    webui_expected = get_secret(secrets_data, ("napcat", "webui_token"))

    onebots = _napcat_onebot_files(napcat)
    if not onebots:
        results.append(Result("DRIFT", "napcat", "onebot11_*.json", "文件不存在"))
    for path in onebots:
        consumer = f"napcat/{path.name}"
        data = read_json(path)
        if not isinstance(data, dict):
            results.append(Result("DRIFT", consumer, "(file)", "JSON 解析失败"))
            continue
        network = data.get("network") if isinstance(data.get("network"), dict) else {}
        for section, expected, secret_label in (
            ("httpServers", http_expected, "napcat.http_token"),
            ("websocketServers", ws_expected, "napcat.ws_token"),
        ):
            servers = network.get(section)
            if not isinstance(servers, list) or not servers:
                results.append(Result("SKIP", consumer, f"{section}[]", "无条目"))
                continue
            if not expected:
                results.append(Result("SKIP", consumer, secret_label, "secrets 中值为空"))
                continue
            for index, server in enumerate(servers):
                field = f"{section}[{index}].token"
                if not isinstance(server, dict):
                    results.append(Result("DRIFT", consumer, field, "条目不是对象"))
                    continue
                actual = server.get("token")
                if not isinstance(actual, str) or not actual:
                    results.append(Result("DRIFT", consumer, field, "缺失"))
                elif actual == expected:
                    results.append(Result("OK", consumer, field))
                else:
                    results.append(
                        Result(
                            "DRIFT",
                            consumer,
                            field,
                            f"config={mask(actual)} secrets={mask(expected)}",
                        )
                    )

    webuis = _napcat_webui_files(napcat)
    if not webuis:
        results.append(Result("DRIFT", "napcat", "webui.json", "文件不存在"))
    for path in webuis:
        consumer = f"napcat/{path.name}"
        data = read_json(path)
        if not isinstance(data, dict):
            results.append(Result("DRIFT", consumer, "(file)", "JSON 解析失败"))
            continue
        if not webui_expected:
            results.append(Result("SKIP", consumer, "token", "secrets 中值为空"))
            continue
        actual = data.get("token")
        if not isinstance(actual, str) or not actual:
            results.append(Result("DRIFT", consumer, "token", "缺失"))
        elif actual == webui_expected:
            results.append(Result("OK", consumer, "token"))
        else:
            results.append(
                Result(
                    "DRIFT",
                    consumer,
                    "token",
                    f"config={mask(actual)} secrets={mask(webui_expected)}",
                )
            )
    return results


def _safe_exc(exc: BaseException, secrets_data: dict) -> str:
    message = f"{type(exc).__name__}: {exc}"
    for path in FIELD_ORDER:
        value = get_secret(secrets_data, path)
        if value and len(value) >= 6:
            message = message.replace(value, "***")
    return message[:240]


def _langbot_db_name(root: Path | None = None) -> str:
    path = langbot_yaml_path(root)
    if path is not None and path.is_file():
        text = read_text(path)
        if text:
            name = read_yaml_value(text, (("database", "postgresql", "database"),))
            if name:
                return name
    return "langbot"


def _pg_connect(secrets_data: dict, root: Path | None = None):
    import psycopg  # 延迟导入：未安装时 check 走 SKIP、sync 走 WARN

    password = get_secret(secrets_data, ("pg", "password"))
    if not password:
        raise RuntimeError("secrets.pg.password 为空")
    dbname = _langbot_db_name(root)
    dsn = build_dsn(dbname, password)
    return psycopg.connect(dsn, connect_timeout=3)


def _deepseek_providers(conn) -> list[dict]:
    rows = conn.execute(
        "select uuid, name, api_keys, base_url from model_providers"
    ).fetchall()
    out = []
    for uuid, name, api_keys, base_url in rows:
        haystack = f"{name or ''} {base_url or ''}".lower()
        if "deepseek" in haystack:
            out.append({"uuid": uuid, "name": name, "api_keys": api_keys})
    return out


def _onebot_bots(conn) -> list[dict]:
    rows = conn.execute(
        "select uuid, name, adapter_config from bots where adapter = 'onebot-v11'"
    ).fetchall()
    return [{"uuid": row[0], "name": row[1], "config": dict(row[2] or {})} for row in rows]


def check_db(secrets_data: dict, root: Path | None = None) -> list[Result]:
    results: list[Result] = []
    deepseek = get_secret(secrets_data, ("deepseek", "api_key"))
    ws_token = get_secret(secrets_data, ("napcat", "ws_token"))
    try:
        conn = _pg_connect(secrets_data, root)
    except Exception as exc:  # noqa: BLE001 - 不可连时按约定 SKIP
        reason = _safe_exc(exc, secrets_data)
        if isinstance(exc, ImportError):
            reason = "psycopg 未安装"
        results.append(Result("SKIP", "langbot-db", "(connect)", reason))
        return results
    try:
        with conn:
            providers = _deepseek_providers(conn)
            if not providers:
                results.append(
                    Result("SKIP", "langbot-db", "model_providers", "未找到 DeepSeek provider")
                )
            elif not deepseek:
                results.append(
                    Result("SKIP", "langbot-db", "model_providers", "secrets 中 deepseek 为空")
                )
            else:
                for provider in providers:
                    field = f"model_providers[{provider['name']}].api_keys"
                    actual = provider["api_keys"]
                    wanted = [deepseek]
                    if isinstance(actual, list) and actual == wanted:
                        results.append(Result("OK", "langbot-db", field))
                    else:
                        preview = actual if isinstance(actual, list) else "?"
                        results.append(
                            Result(
                                "DRIFT",
                                "langbot-db",
                                field,
                                f"db={mask(preview[0]) if preview and isinstance(preview[0], str) else preview} "
                                f"secrets={mask(deepseek)}",
                            )
                        )
            bots = _onebot_bots(conn)
            if not bots:
                results.append(Result("SKIP", "langbot-db", "bots", "无 onebot-v11 bot"))
            elif not ws_token:
                results.append(
                    Result("SKIP", "langbot-db", "bots", "secrets 中 napcat.ws_token 为空")
                )
            else:
                for bot in bots:
                    field = f"bots[{bot['name']}].adapter_config.access-token"
                    actual = bot["config"].get("access-token")
                    if actual == ws_token:
                        results.append(Result("OK", "langbot-db", field))
                    else:
                        results.append(
                            Result(
                                "DRIFT",
                                "langbot-db",
                                field,
                                f"db={mask(actual) if isinstance(actual, str) else actual} "
                                f"secrets={mask(ws_token)}",
                            )
                        )
    except Exception as exc:  # noqa: BLE001
        results.append(Result("SKIP", "langbot-db", "(query)", _safe_exc(exc, secrets_data)))
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return results


def collect_checks(
    secrets_data: dict, root: Path | None = None, *, include_db: bool = True
) -> list[Result]:
    results: list[Result] = []
    results.extend(check_env(secrets_data, root))
    results.extend(check_config(secrets_data, root))
    results.extend(check_langbot_yaml(secrets_data, root))
    results.extend(check_napcat(secrets_data, root))
    if include_db:
        results.extend(check_db(secrets_data, root))
    return results


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


class SyncReport:
    def __init__(self) -> None:
        self.updates: list[tuple[str, list[str]]] = []
        self.unchanged: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def print(self) -> None:
        for consumer, fields in self.updates:
            print(f"[UPDATE] {consumer}: {', '.join(fields)}")
        for consumer in self.unchanged:
            print(f"[UNCHANGED] {consumer}")
        for warning in self.warnings:
            print(f"[WARN] {warning}")
        for error in self.errors:
            print(f"[ERROR] {error}")


def sync_env(
    secrets_data: dict, ctx: WriteContext, root: Path | None = None
) -> tuple[bool, list[str], list[str]]:
    path = env_path(root)
    text = read_text(path)
    original_exists = text is not None
    if text is None:
        text = ""
    lines, nl, trailing = _split_lines(text)
    changed_fields: list[str] = []
    warnings: list[str] = []
    for env_key, secret_path, _optional in ENV_TARGETS:
        expected = get_secret(secrets_data, secret_path)
        field = ".".join(secret_path)
        if not expected:
            warnings.append(f".env: secrets.{field} 为空，跳过 {env_key}")
            continue
        replaced = False
        for index, line in enumerate(lines):
            match = re.match(r"^([ \t]*)([A-Za-z_][A-Za-z0-9_]*)([ \t]*)=", line)
            if not match or match.group(2) != env_key:
                continue
            new_line = f"{match.group(1)}{env_key}={expected}"
            if new_line != line:
                lines[index] = new_line
                changed_fields.append(env_key)
            replaced = True
            break
        if not replaced:
            lines.append(f"{env_key}={expected}")
            changed_fields.append(env_key)
    if not lines:
        return False, [], warnings
    if not original_exists:
        trailing = True
    new_text = _join_lines(lines, nl, trailing)
    if new_text == text:
        return False, [], warnings
    write_if_changed(path, new_text, ctx)
    return True, changed_fields, warnings


def sync_config(
    secrets_data: dict, ctx: WriteContext, root: Path | None = None
) -> tuple[bool, list[str], list[str]]:
    path = config_path(root)
    raw = read_text(path)
    if raw is None:
        return False, [], [f"config.json 不存在，跳过: {path}"]
    cfg = read_json(path)
    if not isinstance(cfg, dict):
        return False, [], [f"config.json 解析失败，跳过: {path}"]
    changed_fields: list[str] = []
    for field, expected, json_path in config_expectations(secrets_data):
        current = _nested_get(cfg, json_path)
        if current != expected:
            _nested_set(cfg, json_path, expected)
            changed_fields.append(field)
    if not changed_fields:
        return False, [], []
    new_text = dump_json_text(cfg, raw)
    write_if_changed(path, new_text, ctx)
    return True, changed_fields, []


def sync_langbot_yaml(
    secrets_data: dict, ctx: WriteContext, root: Path | None = None
) -> tuple[bool, list[str], list[str]]:
    path = langbot_yaml_path(root)
    if path is None or not path.is_file():
        return False, [], [f"LangBot config.yaml 不存在，跳过: {path or '(未解析到目录)'}"]
    text = read_text(path)
    if text is None:
        return False, [], [f"LangBot config.yaml 读取失败，跳过: {path}"]
    changed_fields: list[str] = []
    warnings: list[str] = []
    new_text = text
    for secret_path, candidates in YAML_TARGETS:
        expected = get_secret(secrets_data, secret_path)
        field = ".".join(secret_path)
        if not expected:
            warnings.append(f"langbot/config.yaml: secrets.{field} 为空，跳过")
            continue
        updated, found = write_yaml_value(new_text, candidates, expected)
        if not found:
            warnings.append(f"langbot/config.yaml: 未找到 {field} 对应行，跳过")
            continue
        if updated != new_text:
            new_text = updated
            changed_fields.append(field)
    if not changed_fields:
        return False, [], warnings
    write_if_changed(path, new_text, ctx)
    return True, changed_fields, warnings


def _set_onebot_tokens(data: dict, secrets_data: dict) -> list[str]:
    changed: list[str] = []
    network = data.get("network")
    if not isinstance(network, dict):
        return changed
    for section, secret_path, label in (
        ("httpServers", ("napcat", "http_token"), "httpServers"),
        ("websocketServers", ("napcat", "ws_token"), "websocketServers"),
    ):
        expected = get_secret(secrets_data, secret_path)
        if not expected:
            continue
        servers = network.get(section)
        if not isinstance(servers, list):
            continue
        for index, server in enumerate(servers):
            if not isinstance(server, dict):
                continue
            if server.get("token") != expected:
                server["token"] = expected
                changed.append(f"{label}[{index}].token")
    return changed


def sync_napcat(
    secrets_data: dict, ctx: WriteContext, root: Path | None = None
) -> tuple[bool, list[str], list[str]]:
    napcat = napcat_dir(root)
    if napcat is None or not napcat.is_dir():
        return False, [], [f"NapCat 目录不存在，跳过: {napcat or '(未解析到目录)'}"]
    changed_fields: list[str] = []
    warnings: list[str] = []
    changed_any = False
    found_any = False
    for path in _napcat_onebot_files(napcat):
        found_any = True
        raw = read_text(path)
        data = read_json(path)
        if raw is None or not isinstance(data, dict):
            warnings.append(f"napcat/{path.name}: JSON 读取失败，跳过")
            continue
        fields = _set_onebot_tokens(data, secrets_data)
        if not fields:
            continue
        write_if_changed(path, dump_json_text(data, raw), ctx)
        changed_any = True
        changed_fields.extend(f"{path.name}:{field}" for field in fields)
    for path in _napcat_webui_files(napcat):
        found_any = True
        raw = read_text(path)
        data = read_json(path)
        if raw is None or not isinstance(data, dict):
            warnings.append(f"napcat/{path.name}: JSON 读取失败，跳过")
            continue
        expected = get_secret(secrets_data, ("napcat", "webui_token"))
        if not expected:
            warnings.append("napcat/webui.json: secrets.napcat.webui_token 为空，跳过")
            continue
        if data.get("token") != expected:
            data["token"] = expected
            write_if_changed(path, dump_json_text(data, raw), ctx)
            changed_any = True
            changed_fields.append(f"{path.name}:token")
    if not changed_any and not found_any and not warnings:
        warnings.append("napcat: 未找到 onebot11_*.json / webui.json")
    return changed_any, changed_fields, warnings


def sync_db(
    secrets_data: dict, root: Path | None = None
) -> tuple[list[str], list[str]]:
    """更新 LangBot PG；失败只警告不抛错（文件视图仍已更新）。"""
    updates: list[str] = []
    warnings: list[str] = []
    deepseek = get_secret(secrets_data, ("deepseek", "api_key"))
    ws_token = get_secret(secrets_data, ("napcat", "ws_token"))
    if not deepseek and not ws_token:
        return updates, warnings
    try:
        conn = _pg_connect(secrets_data, root)
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"langbot-db: 连接失败，DB 视图未更新（{_safe_exc(exc, secrets_data)}）；文件视图已更新"
        )
        return updates, warnings
    try:
        import json as _json

        with conn:
            providers = _deepseek_providers(conn)
            if deepseek:
                if not providers:
                    warnings.append("langbot-db: 未找到 DeepSeek provider，api_keys 未更新")
                for provider in providers:
                    wanted = [deepseek]
                    if provider["api_keys"] == wanted:
                        continue
                    conn.execute(
                        "update model_providers set api_keys = %s::json, updated_at = now() "
                        "where uuid = %s",
                        (_json.dumps(wanted, ensure_ascii=False), provider["uuid"]),
                    )
                    updates.append(f"model_providers[{provider['name']}].api_keys")
            if ws_token:
                bots = _onebot_bots(conn)
                if not bots:
                    warnings.append("langbot-db: 无 onebot-v11 bot，access-token 未更新")
                for bot in bots:
                    config = bot["config"]
                    if config.get("access-token") == ws_token:
                        continue
                    config["access-token"] = ws_token
                    conn.execute(
                        "update bots set adapter_config = %s::json, updated_at = now() "
                        "where uuid = %s",
                        (_json.dumps(config, ensure_ascii=False), bot["uuid"]),
                    )
                    updates.append(f"bots[{bot['name']}].adapter_config.access-token")
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"langbot-db: 更新失败（{_safe_exc(exc, secrets_data)}）；文件视图已更新"
        )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return updates, warnings


def run_sync(secrets_data: dict, root: Path | None = None, *, allow_backup: bool = True) -> int:
    ctx = WriteContext(allow_backup=allow_backup)
    report = SyncReport()

    for name, func in (
        (".env", sync_env),
        ("config.json", sync_config),
        ("langbot/config.yaml", sync_langbot_yaml),
        ("napcat", sync_napcat),
    ):
        try:
            changed, fields, warnings = func(secrets_data, ctx, root)
        except Exception as exc:  # noqa: BLE001 - 单个消费方失败不阻断其它视图
            report.errors.append(f"{name}: 写回失败（{type(exc).__name__}: {exc}）")
            continue
        report.warnings.extend(warnings)
        if changed:
            report.updates.append((name, fields))
        else:
            report.unchanged.append(name)

    db_updates: list[str] = []
    db_warnings: list[str] = []
    try:
        db_updates, db_warnings = sync_db(secrets_data, root)
    except Exception as exc:  # noqa: BLE001 - DB 侧任何异常都不得阻断文件视图同步
        db_warnings.append(
            f"langbot-db: 更新失败（{type(exc).__name__}: {exc}）；文件视图已更新"
        )
    if db_updates:
        report.updates.append(("langbot-db", db_updates))
    else:
        report.unchanged.append("langbot-db")
    report.warnings.extend(db_warnings)

    report.print()
    if not report.errors:
        print("同步完成：SSOT 已写回所有可用消费方。")
        return 0
    print("同步失败：部分消费方未能写回。")
    return 1


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def _dsn_password(dsn: object) -> str:
    if not isinstance(dsn, str) or not dsn:
        return ""
    try:
        return urlsplit(dsn).password or ""
    except ValueError:
        return ""


def derive_secrets(root: Path | None = None) -> tuple[dict, list[str], list[str]]:
    """从现有消费方视图反推 secrets.json 内容。

    返回 (data, derived_fields, missing_fields)。缺失字段留空并提示用 rotate 补，
    不猜造新值，避免 init 后 sync 把未知值写坏生产配置。
    """
    cfg = load_config(root)
    env_file = read_env(env_path(root))
    langbot_text = ""
    yaml_path = langbot_yaml_path(root)
    if yaml_path is not None and yaml_path.is_file():
        langbot_text = read_text(yaml_path) or ""
    napcat_text_values = _derive_napcat_values(root)

    def from_cfg(*path: str) -> str:
        value = _nested_get(cfg, path)
        return value if isinstance(value, str) else ""

    def from_yaml(candidates: tuple[tuple[str, ...], ...]) -> str:
        if not langbot_text:
            return ""
        value = read_yaml_value(langbot_text, candidates)
        return value or ""

    derived: dict[tuple[str, str], str] = {}
    derived[("pg", "password")] = _first(
        _dsn_password(cfg.get("db_dsn")),
        env_file.get("PG_PASSWORD", ""),
        from_yaml((("database", "postgresql", "password"), ("database", "password"))),
    )
    derived[("deepseek", "api_key")] = _first(
        from_cfg("llm", "api_key"),
        env_file.get("DEEPSEEK_API_KEY", ""),
    )
    derived[("core", "api_key")] = from_cfg("core", "api_key")
    derived[("langbot", "global_api_key")] = _first(
        from_yaml((("api", "global_api_key"), ("global_api_key",))),
        env_file.get("LANGBOT_API_KEY", ""),
    )
    derived[("langbot", "secret")] = from_yaml((("system", "jwt", "secret"), ("secret",)))
    derived[("langbot", "recovery_key")] = from_yaml(
        (("system", "recovery_key"), ("recovery_key",))
    )
    derived[("langbot", "integration_api_key")] = _first(
        from_cfg("integrations", "langbot", "api_key"),
        env_file.get("LANGBOT_API_KEY", ""),
    )
    derived[("napcat", "http_token")] = _first(
        env_file.get("NAPCAT_HTTP_TOKEN", ""),
        napcat_text_values.get("http_token", ""),
    )
    derived[("napcat", "ws_token")] = napcat_text_values.get("ws_token", "")
    derived[("napcat", "webui_token")] = napcat_text_values.get("webui_token", "")

    data = empty_secrets()
    found: list[str] = []
    missing: list[str] = []
    for path in FIELD_ORDER:
        value = derived.get(path, "")
        label = ".".join(path)
        if value:
            set_secret(data, path, value)
            found.append(label)
        else:
            missing.append(label)
    return data, found, missing


def _derive_napcat_values(root: Path | None = None) -> dict[str, str]:
    values = {"http_token": "", "ws_token": "", "webui_token": ""}
    napcat = napcat_dir(root)
    if napcat is None or not napcat.is_dir():
        return values
    for path in _napcat_onebot_files(napcat):
        data = read_json(path)
        if not isinstance(data, dict):
            continue
        network = data.get("network") if isinstance(data.get("network"), dict) else {}
        for section, key in (
            ("httpServers", "http_token"),
            ("websocketServers", "ws_token"),
        ):
            if values[key]:
                continue
            servers = network.get(section)
            if isinstance(servers, list):
                for server in servers:
                    if isinstance(server, dict) and isinstance(server.get("token"), str) and server["token"]:
                        values[key] = server["token"]
                        break
    for path in _napcat_webui_files(napcat):
        data = read_json(path)
        if isinstance(data, dict) and isinstance(data.get("token"), str) and data["token"]:
            values["webui_token"] = data["token"]
            break
    return values


def _first(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def print_secrets_summary(secrets_data: dict, path: Path) -> None:
    print(f"secrets: {path}")
    print(f"version: {secrets_data.get('version')}")
    for section, key in FIELD_ORDER:
        value = get_secret(secrets_data, (section, key))
        print(f"  {section}.{key:<20} {mask(value)}")


def run_init(root: Path | None = None) -> int:
    path = secrets_path(root)
    if path.is_file():
        existing = load_secrets(path)
        if existing is None:
            print(f"[ERROR] secrets.json 已存在但无法解析，未做修改: {path}")
            return 1
        print(f"secrets.json 已存在，保持不动: {path}")
        print_secrets_summary(existing, path)
        return 0
    data, found, missing = derive_secrets(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ctx = WriteContext(allow_backup=False)
    write_if_changed(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", ctx)
    print(f"已生成 secrets.json: {path}")
    if found:
        print(f"从现有视图推导: {', '.join(found)}")
    for label in missing:
        hint = "（需人工补：rotate --key deepseek --value <真实 key>）" if label == "deepseek.api_key" else ""
        print(f"[WARN] 无法推导 {label}，已留空{hint}")
    print_secrets_summary(data, path)
    return 0


# ---------------------------------------------------------------------------
# rotate
# ---------------------------------------------------------------------------


def generate_value(kind: str) -> str:
    if kind == "alnum32":
        return "".join(pysecrets.choice(_ALNUM) for _ in range(32))
    if kind == "alnum24":
        return "".join(pysecrets.choice(_ALNUM) for _ in range(24))
    if kind == "alnum8":
        return "".join(pysecrets.choice(_ALNUM) for _ in range(8))
    if kind == "hex32":
        return pysecrets.token_hex(16)
    if kind == "hex12":
        return pysecrets.token_hex(6)
    if kind == "deepseek":
        raise ValueError("DeepSeek key 无法安全生成，请用 --value 指定真实 sk- key")
    raise ValueError(f"未知生成类型: {kind}")


def run_rotate(
    key: str,
    value: str | None,
    root: Path | None = None,
    *,
    sync: bool = False,
    allow_backup: bool = True,
) -> int:
    if key not in ROTATE_KEYS:
        print(f"[ERROR] 未知 key: {key}")
        print(f"可用: {', '.join(ROTATE_KEYS)}")
        return 2
    secret_path, kind = ROTATE_KEYS[key]
    if value is None:
        try:
            value = generate_value(kind)
        except ValueError as exc:
            print(f"[ERROR] {exc}")
            return 2
    else:
        value = value.strip()
        if not value:
            print("[ERROR] --value 不能为空")
            return 2

    path = secrets_path(root)
    existing = load_secrets(path) if path.is_file() else None
    if existing is None:
        if path.is_file():
            print(f"[ERROR] secrets.json 无法解析，未做修改: {path}")
            return 1
        print(f"[WARN] secrets.json 不存在，将创建骨架: {path}")
        existing = empty_secrets()
    data = ensure_shape(dict(existing))
    set_secret(data, secret_path, value)
    data["version"] = SECRETS_VERSION
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    ctx = WriteContext(allow_backup=allow_backup)
    changed = write_if_changed(path, text, ctx)
    label = ".".join(secret_path)
    if changed:
        print(f"已轮换 {label} -> {mask(value)}  ({path})")
    else:
        print(f"未变化 {label} = {mask(value)}  ({path})")
    if not sync:
        print("提示：加 --sync 立即写回各消费方。")
        return 0
    return run_sync(data, root, allow_backup=allow_backup)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def cmd_show(root: Path | None = None) -> int:
    path = secrets_path(root)
    data = load_secrets(path)
    if data is None:
        print(f"[ERROR] secrets.json 不存在或无法解析: {path}")
        return 1
    print_secrets_summary(data, path)
    checks = collect_checks(data, root, include_db=False)
    drift = [item for item in checks if item.status == "DRIFT"]
    if drift:
        print(f"drift: {len(drift)} 项与消费方文件视图不一致（运行 check 查看明细）")
    else:
        print("drift: 0（文件视图一致）")
    return 0


def cmd_check(root: Path | None = None) -> int:
    path = secrets_path(root)
    data = load_secrets(path)
    if data is None:
        print(f"[ERROR] secrets.json 不存在或无法解析: {path}")
        return 1
    checks = collect_checks(data, root)
    for item in checks:
        print(item.render())
    counts = {status: 0 for status in ("OK", "DRIFT", "SKIP")}
    for item in checks:
        counts[item.status] = counts.get(item.status, 0) + 1
    print(
        f"\n结果: OK={counts.get('OK', 0)} DRIFT={counts.get('DRIFT', 0)} "
        f"SKIP={counts.get('SKIP', 0)}"
    )
    return 1 if counts.get("DRIFT", 0) else 0


def cmd_sync(root: Path | None = None, *, allow_backup: bool = True) -> int:
    path = secrets_path(root)
    data = load_secrets(path)
    if data is None:
        print(f"[ERROR] secrets.json 不存在或无法解析: {path}")
        return 1
    return run_sync(data, root, allow_backup=allow_backup)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rotate.py",
        description="Möbius 密钥 SSOT 轮换脚本（init/show/check/sync/rotate）",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="从现有视图反推生成 secrets.json（已存在则不动）")
    sub.add_parser("show", help="打印掩码、路径与 drift 摘要")
    sub.add_parser("check", help="检查消费方与 SSOT 是否一致")
    sync_parser = sub.add_parser("sync", help="把 SSOT 写回所有消费方")
    sync_parser.add_argument("--no-backup", action="store_true", help="不备份直接写")
    rotate_parser = sub.add_parser("rotate", help="轮换单个密钥")
    rotate_parser.add_argument(
        "--key",
        required=True,
        choices=sorted(ROTATE_KEYS),
        help="要轮换的密钥名",
    )
    rotate_parser.add_argument("--value", default=None, help="显式指定新值（否则自动生成）")
    rotate_parser.add_argument("--sync", action="store_true", help="写回 SSOT 后立即同步消费方")
    rotate_parser.add_argument("--no-backup", action="store_true", help="不备份直接写")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            return run_init()
        if args.command == "show":
            return cmd_show()
        if args.command == "check":
            return cmd_check()
        if args.command == "sync":
            return cmd_sync(allow_backup=not args.no_backup)
        if args.command == "rotate":
            return run_rotate(
                args.key,
                args.value,
                sync=args.sync,
                allow_backup=not args.no_backup,
            )
    except KeyboardInterrupt:  # pragma: no cover
        print("[ERROR] 已中断")
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI 兜底，不抛栈
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
