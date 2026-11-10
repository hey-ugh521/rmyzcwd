"""M3b 生命周期命令测试（临时目录 + fake PG 工具，不连生产 PG）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared import lifecycle  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


class FakePg:
    def __init__(self):
        self.dumped: list[str] = []
        self.created: list[str] = []
        self.restored: list[tuple[str, str]] = []
        self.dropped: list[str] = []

    def pg_dump(self, db: str, out: Path):
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes((f"DUMP-{db}").encode())
        self.dumped.append(db)

    def createdb(self, db: str):
        self.created.append(db)

    def pg_restore(self, db: str, dump: Path):
        self.restored.append((db, dump.name))

    def table_count(self, db: str) -> int:
        return 3

    def dropdb(self, db: str):
        self.dropped.append(db)


def make_ctx(tmp: Path):
    (tmp / "data").mkdir()
    cfg = {
        "version": "9.9.9-test",
        "db_dsn": "postgresql://postgres:secret@127.0.0.1:55432/mobius_core",
        "services": {"postgres": {"exe": str(tmp / "pg" / "bin" / "pg_ctl.exe")}},
        "lifecycle": {"backup_dir": str(tmp / "backups")},
    }
    (tmp / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    (tmp / "data" / "secrets.json").write_text(
        json.dumps({"pg": {"password": "secret"}, "core": {"api_key": "k"}}), encoding="utf-8"
    )
    os.environ["MOBIUS_ROOT"] = str(tmp)
    os.environ.pop("MOBIUS_SECRETS_FILE", None)
    return lifecycle.load_context(tmp)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="m3-lifecycle-"))
    try:
        ctx = make_ctx(tmp)
        fake = FakePg()
        fake.target = ctx.pg.target
        fake.bin_dir = ctx.pg.bin_dir
        ctx.pg = fake
        print("[context]")
        check("context root", ctx.root == tmp.resolve())
        check("dbs 含 core DSN", "mobius_core" in ctx.dbs)
        check("dbs 默认含 mobius/langbot", {"mobius", "langbot"}.issubset(set(ctx.dbs)))
        check("version 读取 config", ctx.version == "9.9.9-test")
        check("secrets path 找到", ctx.secrets_path == tmp / "data" / "secrets.json")

        print("\n[backup]")
        out = lifecycle.create_backup(ctx)
        bp = Path(out["backup"])
        check("backup 目录生成", bp.is_dir() and (bp / "manifest.json").is_file())
        check("config/secrets 已备份", (bp / "config.json").is_file() and (bp / "secrets.json").is_file())
        check("PG dump 全部生成", len(ctx.pg.dumped) == len(ctx.dbs)
              and all((bp / "pg" / f"{db}.dump").is_file() for db in ctx.dbs))
        check("SHA256SUMS 存在", (bp / "SHA256SUMS").is_file())
        check("manifest format", out["manifest"]["format"] == "mobius-backup-v1")
        check("backup 可列出", any(x["path"] == str(bp) for x in lifecycle.list_backups(ctx)))
        check("verify_backup 通过", lifecycle.verify_backup(bp)["ok"] is True)

        print("\n[scratch restore]")
        res = lifecycle.restore_scratch(ctx, bp)
        check("scratch 每个 db 都恢复并校验", len(res["restored"]) == len(ctx.dbs)
              and all(x["tables"] == 3 for x in res["restored"]))
        check("scratch 库已创建并 drop", len(ctx.pg.created) == len(ctx.dbs)
              and set(ctx.pg.created) == set(ctx.pg.dropped))
        check("pg_restore 使用 dump", len(ctx.pg.restored) == len(ctx.dbs))

        print("\n[tamper/upgrade/status]")
        victim = bp / "pg" / f"{ctx.dbs[0]}.dump"
        victim.write_bytes(b"tampered")
        try:
            lifecycle.verify_backup(bp)
            check("篡改备份被拒绝", False)
        except lifecycle.LifecycleError:
            check("篡改备份被拒绝", True)
        inst = tmp / "MobiusSetup.exe"
        inst.write_bytes(b"installer")
        plan = lifecycle.upgrade_plan(ctx, inst)
        check("upgrade plan 步骤完整", plan["steps"][0] == "backup" and "rollback-on-failure" in plan["steps"])
        st = lifecycle.status(ctx)
        dumped = json.dumps(st)
        check("status 不包含口令字段", "PGPASSWORD" not in dumped and '"password"' not in dumped)
        check("status 含备份清单", st["backups_dir"] == str(tmp / "backups"))
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
