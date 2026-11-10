"""M3b 生命周期管理（纯标准库，CLI/安装器可复用）。

命令：
    python shared/lifecycle.py status
    python shared/lifecycle.py backup [--out DIR]
    python shared/lifecycle.py restore --from BACKUP --scratch
    python shared/lifecycle.py upgrade --installer FILE [--dry-run]
    python shared/lifecycle.py uninstall --dry-run [--remove-data]

设计约束：
- 只依赖标准库 + shared.paths，不 import Core/Gateway 业务包；
- PG 口令只经 PGPASSWORD 传给子进程，绝不打印；
- 恢复默认只做 scratch 校验；真实覆盖需显式 --force（M3c 再接服务停机）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT_FILE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_FILE))
from shared import paths as shared_paths  # noqa: E402

DEFAULT_DBS = ("mobius_core", "mobius", "langbot")
SAFE_DB = __import__("re").compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LifecycleError(RuntimeError):
    pass


@dataclass
class PgTarget:
    host: str
    port: int
    user: str
    password: str = ""

    @classmethod
    def from_dsn(cls, dsn: str, password: str = "") -> "PgTarget":
        p = urllib.parse.urlsplit(dsn or "")
        if not p.hostname:
            raise LifecycleError("db_dsn 缺少 host")
        return cls(host=p.hostname, port=p.port or 5432,
                   user=p.username or "postgres", password=password or (p.password or ""))


@dataclass
class PgTools:
    bin_dir: Path
    target: PgTarget

    def exe(self, name: str) -> Path:
        suffix = ".exe" if os.name == "nt" else ""
        return self.bin_dir / f"{name}{suffix}"

    def run(self, args: list[str], *, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        if self.target.password:
            env["PGPASSWORD"] = self.target.password
        try:
            return subprocess.run(args, input=input_bytes, env=env,
                                  capture_output=True, timeout=300)
        except FileNotFoundError as exc:
            raise LifecycleError(f"找不到 PG 工具：{args[0]}") from exc

    def pg_dump(self, db: str, out: Path) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        r = self.run([
            str(self.exe("pg_dump")), "-h", self.target.host, "-p", str(self.target.port),
            "-U", self.target.user, "-Fc", "-d", db, "-f", str(out),
        ])
        if r.returncode != 0:
            raise LifecycleError(f"pg_dump {db} 失败：{r.stderr.decode('utf-8', 'replace')[:300]}")
        if not out.is_file() or out.stat().st_size == 0:
            raise LifecycleError(f"pg_dump {db} 输出为空")

    def createdb(self, db: str) -> None:
        r = self.run([str(self.exe("createdb")), "-h", self.target.host,
                      "-p", str(self.target.port), "-U", self.target.user, db])
        if r.returncode != 0:
            raise LifecycleError(f"createdb {db} 失败：{r.stderr.decode('utf-8', 'replace')[:300]}")

    def dropdb(self, db: str) -> None:
        self.run([str(self.exe("dropdb")), "--if-exists", "-h", self.target.host,
                  "-p", str(self.target.port), "-U", self.target.user, db])

    def pg_restore(self, db: str, dump: Path) -> None:
        r = self.run([str(self.exe("pg_restore")), "-h", self.target.host,
                      "-p", str(self.target.port), "-U", self.target.user,
                      "--no-owner", "--no-privileges", "-d", db, str(dump)])
        if r.returncode != 0:
            raise LifecycleError(f"pg_restore {dump.name} 失败：{r.stderr.decode('utf-8', 'replace')[:300]}")

    def table_count(self, db: str) -> int:
        r = self.run([str(self.exe("psql")), "-h", self.target.host, "-p", str(self.target.port),
                      "-U", self.target.user, "-d", db, "-tAc",
                      "select count(*) from information_schema.tables where table_schema='public'"])
        if r.returncode != 0:
            raise LifecycleError(f"psql {db} 失败：{r.stderr.decode('utf-8', 'replace')[:300]}")
        return int((r.stdout or b"0").decode("utf-8", "replace").strip() or 0)


@dataclass
class Context:
    root: Path
    cfg: dict
    config_path: Path
    secrets_path: Path | None
    pg: PgTools
    dbs: list[str]
    backups_dir: Path
    version: str


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def _secret_path(root: Path, cfg: dict) -> Path | None:
    env = os.environ.get("MOBIUS_SECRETS_FILE", "").strip()
    if env:
        return Path(env)
    rel = str(cfg.get("secrets_file") or "").strip()
    if rel:
        p = Path(rel)
        return p if p.is_absolute() else root / p
    for p in (root / "data" / "secrets.json", root / "secrets.json"):
        if p.is_file():
            return p
    return None


def load_context(root: str | Path | None = None) -> Context:
    root = Path(root or os.environ.get("MOBIUS_ROOT") or shared_paths.app_root()).resolve()
    cfg_path = root / "config.json"
    cfg = _load_json(cfg_path)
    shared_paths.expand_config(cfg, root)
    secrets = _load_json(_secret_path(root, cfg) or Path("__none__"))
    pg_cfg = (cfg.get("services") or {}).get("postgres") or {}
    dsn = str(cfg.get("db_dsn") or "")
    pg_password = str(((secrets.get("pg") or {}).get("password")) or "")
    target = PgTarget.from_dsn(dsn, pg_password)
    exe = str(pg_cfg.get("exe") or "")
    bin_dir = Path(exe).parent if exe else Path("")
    if not bin_dir or not str(bin_dir):
        # 允许 PATH 中的 pg_dump
        bin_dir = Path("")
    dbs = list((cfg.get("lifecycle") or {}).get("backup_dbs") or DEFAULT_DBS)
    dsn_db = urllib.parse.urlsplit(dsn).path.lstrip("/")
    if dsn_db and dsn_db not in dbs:
        dbs.insert(0, dsn_db)
    backups_dir = Path(str((cfg.get("lifecycle") or {}).get("backup_dir")
                          or root / "backups")).expanduser()
    if not backups_dir.is_absolute():
        backups_dir = root / backups_dir
    return Context(root=root, cfg=cfg, config_path=cfg_path,
                   secrets_path=_secret_path(root, cfg), pg=PgTools(bin_dir, target),
                   dbs=dbs, backups_dir=backups_dir,
                   version=str(cfg.get("version") or os.environ.get("MOBIUS_VERSION") or "unknown"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _harden_dir(path: Path) -> None:
    """备份含 secrets，尽量收紧到当前用户；失败不阻断（安装器会再校验 ACL）。"""
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME") or ""
            if user:
                subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
                               capture_output=True, text=True, timeout=10)
        else:
            os.chmod(path, 0o700)
    except Exception:  # noqa: BLE001
        pass


def create_backup(ctx: Context, out_dir: str | Path | None = None) -> dict:
    """创建备份目录（config/secrets + 各 PG dump + manifest/SHA256SUMS）。"""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = Path(out_dir) if out_dir else ctx.backups_dir
    dest = dest / f"Mobius-backup-{stamp}"
    dest.mkdir(parents=True, exist_ok=False)
    _harden_dir(dest)
    (dest / "pg").mkdir()
    files: list[dict] = []

    for name, src in (("config.json", ctx.config_path),
                      ("secrets.json", ctx.secrets_path)):
        if src and Path(src).is_file():
            shutil.copy2(src, dest / name)
            files.append({"name": name, "sha256": _sha256(dest / name),
                          "bytes": (dest / name).stat().st_size})

    pg_files = []
    for db in ctx.dbs:
        if not SAFE_DB.match(db):
            raise LifecycleError(f"非法数据库名：{db!r}")
        out = dest / "pg" / f"{db}.dump"
        ctx.pg.pg_dump(db, out)
        pg_files.append({"db": db, "file": f"pg/{db}.dump", "sha256": _sha256(out),
                         "bytes": out.stat().st_size})
    manifest = {
        "format": "mobius-backup-v1",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "mobius_version": ctx.version,
        "root": str(ctx.root),
        "dbs": ctx.dbs,
        "files": files,
        "pg": pg_files,
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    lines = [f"{x.get('sha256', '')}  {x.get('file') or x.get('name')}" for x in pg_files + files]
    (dest / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "backup": str(dest), "manifest": manifest}


def list_backups(ctx: Context) -> list[dict]:
    if not ctx.backups_dir.is_dir():
        return []
    out = []
    for p in sorted(ctx.backups_dir.glob("Mobius-backup-*")):
        m = _load_json(p / "manifest.json")
        out.append({"path": str(p), "created_at": m.get("created_at"), "dbs": m.get("dbs"),
                    "version": m.get("mobius_version")})
    return out


def verify_backup(path: str | Path) -> dict:
    p = Path(path)
    m = _load_json(p / "manifest.json")
    if m.get("format") != "mobius-backup-v1":
        raise LifecycleError(f"不是 Möbius 备份：{p}")
    errors = []
    for item in list(m.get("pg") or []) + list(m.get("files") or []):
        rel = item.get("file") or item.get("name")
        fp = p / rel
        if not fp.is_file():
            errors.append(f"缺少 {rel}")
        elif item.get("sha256") and _sha256(fp) != item["sha256"]:
            errors.append(f"校验失败 {rel}")
    if errors:
        raise LifecycleError("；".join(errors))
    return {"ok": True, "backup": str(p), "manifest": m}


def restore_scratch(ctx: Context, backup: str | Path) -> dict:
    """把备份恢复到临时库并校验表数，随后 drop；不触碰生产库。"""
    p = Path(backup)
    info = verify_backup(p)
    m = info["manifest"]
    results = []
    stamp = dt.datetime.now().strftime("%Y%m%d%H%M%S")
    created: list[str] = []
    try:
        for item in m.get("pg") or []:
            src_db = str(item["db"])
            if not SAFE_DB.match(src_db):
                raise LifecycleError(f"非法数据库名：{src_db!r}")
            scratch = f"mobius_restore_{stamp}_{src_db}"[:63]
            ctx.pg.createdb(scratch)
            created.append(scratch)
            ctx.pg.pg_restore(scratch, p / item["file"])
            n = ctx.pg.table_count(scratch)
            results.append({"db": src_db, "scratch": scratch, "tables": n})
    finally:
        for db in created:
            ctx.pg.dropdb(db)
    return {"ok": True, "backup": str(p), "restored": results}


def upgrade_plan(ctx: Context, installer: str | Path) -> dict:
    installer = Path(installer).resolve()
    if not installer.is_file():
        raise LifecycleError(f"安装器不存在：{installer}")
    return {
        "ok": True,
        "root": str(ctx.root),
        "installer": str(installer),
        "steps": ["backup", "stop-services", "install", "health-check", "rollback-on-failure"],
    }


def pre_upgrade_backup(ctx: Context) -> dict:
    return create_backup(ctx)


def write_journal(ctx: Context, payload: dict) -> Path:
    journal = ctx.root / "upgrade-journal.json"
    journal.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return journal


def status(ctx: Context) -> dict:
    return {
        "ok": True,
        "root": str(ctx.root),
        "version": ctx.version,
        "config": str(ctx.config_path),
        "secrets": str(ctx.secrets_path) if ctx.secrets_path else None,
        "pg": {"host": ctx.pg.target.host, "port": ctx.pg.target.port,
               "user": ctx.pg.target.user, "bin": str(ctx.pg.bin_dir)},
        "dbs": ctx.dbs,
        "backups_dir": str(ctx.backups_dir),
        "backups": list_backups(ctx),
    }


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Möbius lifecycle")
    ap.add_argument("--root", default="")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    b = sub.add_parser("backup")
    b.add_argument("--out", default="")
    r = sub.add_parser("restore")
    r.add_argument("--from", dest="src", required=True)
    r.add_argument("--scratch", action="store_true", help="只恢复到临时库校验（默认）")
    r.add_argument("--force", action="store_true", help="覆盖生产库（M3c 才开放）")
    u = sub.add_parser("upgrade")
    u.add_argument("--installer", required=True)
    u.add_argument("--dry-run", action="store_true")
    x = sub.add_parser("uninstall")
    x.add_argument("--dry-run", action="store_true")
    x.add_argument("--remove-data", action="store_true")
    args = ap.parse_args()
    ctx = load_context(args.root or None)
    if args.cmd == "status":
        print(json.dumps(status(ctx), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "backup":
        print(json.dumps(create_backup(ctx, args.out or None), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "restore":
        if args.force:
            raise SystemExit("真实覆盖恢复在 M3c 接入停服/回滚后再开放；当前仅 --scratch")
        print(json.dumps(restore_scratch(ctx, args.src), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "upgrade":
        plan = upgrade_plan(ctx, args.installer)
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        backup = pre_upgrade_backup(ctx)
        journal = write_journal(ctx, {"phase": "backup_done", "backup": backup["backup"],
                                      "installer": plan["installer"],
                                      "created_at": dt.datetime.now().isoformat(timespec="seconds")})
        print(json.dumps({"ok": True, "backup": backup["backup"], "journal": str(journal),
                          "note": "停服/安装/健康检查由 M3c 安装器接通"}, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "uninstall":
        print(json.dumps({"ok": True, "dry_run": True, "root": str(ctx.root),
                          "remove_data": bool(args.remove_data),
                          "note": "卸载执行器在 M3c 与安装器/autostart 一起接通"},
                         ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(_cli())
    except LifecycleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
