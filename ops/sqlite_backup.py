#!/usr/bin/env python3
"""Möbius SQLite 每日备份 + 恢复验证。

设计约束（来自 2026-10-30 事故与架构评审）：
  1. langbot.db 由容器进程持有，**宿主机绝不直接写**（双写曾导致库损坏）。
     备份必须通过 `docker exec` 在容器内执行 VACUUM INTO，宿主机只读结果文件。
  2. 备份完成不等于可用：必须在一个**独立临时目录**实际打开并查询，
     与源库行数比对，并跑 PRAGMA integrity_check。
  3. 幂等：同一天重复运行不重复备份（除非 --force）。

用法：
    python ops/sqlite_backup.py                # 每日一次（有状态文件守卫）
    python ops/sqlite_backup.py --force        # 强制重做
    python ops/sqlite_backup.py --keep 7       # 保留 7 天
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(REPO, "data", "sqlite_backup_state.json")
DEFAULT_DEST = r"E:\backup\sqlite"

LANGBOT_CONTAINER = "langbot"
CONTAINER_DB = "/app/data/langbot.db"
CONTAINER_TMP_DIR = "/app/data/.backup"

MOBIUS_DB = os.path.join(REPO, "data", "mobius.db")


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# --------------------------------------------------------------------------- 源库
def mobius_counts() -> dict[str, int]:
    con = sqlite3.connect(f"file:{MOBIUS_DB}?mode=ro", uri=True, timeout=20)
    try:
        tabs = [r[0] for r in con.execute(
            "select name from sqlite_master where type='table' and name not like 'sqlite_%'"
        )]
        out = {}
        for t in tabs:
            try:
                out[t] = con.execute(f'select count(*) from "{t}"').fetchone()[0]
            except sqlite3.Error:
                pass
        return out
    finally:
        con.close()


def langbot_counts() -> dict[str, int]:
    script = (
        "import sqlite3, json;"
        f"c=sqlite3.connect('{CONTAINER_DB}', timeout=20);"
        "tabs=[r[0] for r in c.execute(\"select name from sqlite_master where type='table' "
        "and name not like 'sqlite_%'\")];"
        "print(json.dumps({t:(c.execute('select count(*) from \"'+t+'\"').fetchone()[0]) for t in tabs}));"
        "c.close()"
    )
    res = _run(["docker", "exec", LANGBOT_CONTAINER, "/app/.venv/bin/python", "-c", script])
    if res.returncode != 0:
        raise RuntimeError(f"读取 langbot 行数失败: {(res.stderr or res.stdout)[:300]}")
    return json.loads(res.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- 备份
def backup_mobius(dest_file: str) -> None:
    """宿主机 Core 的库：用 SQLite 在线备份 API（并发安全）。"""
    src = sqlite3.connect(MOBIUS_DB, timeout=30)
    try:
        dst = sqlite3.connect(dest_file)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def backup_langbot(dest_file: str, name: str) -> None:
    """容器内 VACUUM INTO，再拷出到宿主机。"""
    _run(["docker", "exec", LANGBOT_CONTAINER, "sh", "-c", f"mkdir -p {CONTAINER_TMP_DIR}"])
    container_out = f"{CONTAINER_TMP_DIR}/{name}"
    script = (
        "import os, sqlite3;"
        f"p='{container_out}';"
        "os.path.exists(p) and os.remove(p);"
        f"c=sqlite3.connect('{CONTAINER_DB}', timeout=30);"
        "c.execute('VACUUM INTO ?', (p,));"
        "c.close();"
        f"print(os.path.getsize(p))"
    )
    res = _run(["docker", "exec", LANGBOT_CONTAINER, "/app/.venv/bin/python", "-c", script], timeout=180)
    if res.returncode != 0:
        raise RuntimeError(f"容器内 VACUUM INTO 失败: {(res.stderr or res.stdout)[:300]}")
    cp = _run(["docker", "cp", f"{LANGBOT_CONTAINER}:{container_out}", dest_file], timeout=180)
    if cp.returncode != 0:
        raise RuntimeError(f"docker cp 失败: {(cp.stderr or cp.stdout)[:300]}")
    # 清理容器内临时文件
    _run(["docker", "exec", LANGBOT_CONTAINER, "sh", "-c", f"rm -f {container_out}"])


# --------------------------------------------------------------------------- 验证
def verify_restorable(dest_file: str, expected: dict[str, int]) -> dict:
    """在独立临时目录恢复并比对，证明备份真的可用。"""
    result = {"integrity": None, "tables_checked": 0, "mismatch": [], "ok": False}
    with tempfile.TemporaryDirectory(prefix="mobius-restore-") as tmp:
        restored = os.path.join(tmp, os.path.basename(dest_file))
        shutil.copy2(dest_file, restored)
        con = sqlite3.connect(f"file:{restored}?mode=ro", uri=True, timeout=30)
        try:
            integ = con.execute("PRAGMA integrity_check").fetchone()[0]
            result["integrity"] = integ
            if integ != "ok":
                return result
            # 抽样比对：优先比大表，最多 8 张，避免全库扫描过久
            items = sorted(expected.items(), key=lambda kv: -kv[1])[:8]
            for table, want in items:
                try:
                    got = con.execute(f'select count(*) from "{table}"').fetchone()[0]
                except sqlite3.Error:
                    continue  # 视图/虚拟表等跳过
                result["tables_checked"] += 1
                if got != want:
                    result["mismatch"].append({"table": table, "expected": want, "restored": got})
            result["ok"] = not result["mismatch"]
            return result
        finally:
            con.close()


# --------------------------------------------------------------------------- 状态/清理
def load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def cleanup(dest_dir: str, keep: int) -> list[str]:
    removed = []
    if not os.path.isdir(dest_dir):
        return removed
    for dbname in ("mobius", "langbot"):
        files = sorted(
            (os.path.join(dest_dir, f) for f in os.listdir(dest_dir)
             if f.startswith(dbname + "-") and f.endswith(".db")),
            reverse=True,
        )
        for old in files[keep:]:
            os.remove(old)
            removed.append(os.path.basename(old))
    return removed


# --------------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=DEFAULT_DEST)
    ap.add_argument("--keep", type=int, default=14)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    state = load_state()
    if not args.force and state.get("last_date") == today and state.get("ok"):
        log(f"今日（{today}）已完成备份，跳过。--force 可强制重做")
        return 0

    os.makedirs(args.dest, exist_ok=True)
    summary: dict = {"date": today, "ok": False, "items": {}, "errors": []}

    for dbname, db_path_exists in (("mobius", os.path.exists(MOBIUS_DB)), ("langbot", True)):
        name = f"{dbname}-{today}.db"
        dest_file = os.path.join(args.dest, name)
        try:
            log(f"备份 {dbname} ...")
            if dbname == "mobius":
                if not db_path_exists:
                    log("  mobius.db 不存在，跳过")
                    continue
                expected = mobius_counts()
                backup_mobius(dest_file)
            else:
                expected = langbot_counts()
                backup_langbot(dest_file, name)

            size_mb = os.path.getsize(dest_file) / 1024 / 1024
            log(f"  已写入 {name}（{size_mb:.2f} MB），开始恢复验证 ...")
            ver = verify_restorable(dest_file, expected)
            summary["items"][dbname] = {
                "file": dest_file, "size_mb": round(size_mb, 2), "verify": ver,
            }
            if ver["ok"]:
                log(f"  恢复验证通过：integrity=ok，比对 {ver['tables_checked']} 张表一致")
            else:
                log(f"  ❌ 恢复验证失败：integrity={ver['integrity']} mismatch={ver['mismatch'][:3]}")
                summary["errors"].append(f"{dbname}: 恢复验证失败")
        except Exception as exc:  # noqa: BLE001
            log(f"  ❌ {dbname} 备份失败：{type(exc).__name__}: {exc}")
            summary["errors"].append(f"{dbname}: {exc}")

    removed = cleanup(args.dest, args.keep)
    if removed:
        log(f"清理过期备份：{', '.join(removed)}")

    summary["ok"] = not summary["errors"]
    summary["removed"] = removed
    save_state({"last_date": today, "ok": summary["ok"], "detail": summary})
    log("完成" if summary["ok"] else "存在失败项")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
