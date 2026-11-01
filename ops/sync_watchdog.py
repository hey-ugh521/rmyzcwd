"""watchdog.py 版本同步/校验（P1）。

运行时路径：C:/Users/lenovo/Desktop/_说明文档/qqbot/watchdog.py
仓库副本：  mobius/ops/watchdog/watchdog.py

用法：
  python ops/sync_watchdog.py --check     # 校验一致（exit 0/1）
  python ops/sync_watchdog.py --capture   # 运行时 → 仓库（提交前用）
  python ops/sync_watchdog.py --apply     # 仓库 → 运行时（改完仓库后部署）
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys

RUNTIME = r"C:\Users\lenovo\Desktop\_说明文档\qqbot\watchdog.py"
CANONICAL = r"C:\Users\lenovo\Desktop\_说明文档\mobius\ops\watchdog\watchdog.py"


def sha(path: str) -> str | None:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except FileNotFoundError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--capture", action="store_true")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    a, b = sha(RUNTIME), sha(CANONICAL)
    if args.capture:
        shutil.copy2(RUNTIME, CANONICAL)
        print(f"已捕获运行时 → 仓库：{sha(CANONICAL)[:12]}…")
        return 0
    if args.apply:
        shutil.copy2(CANONICAL, RUNTIME)
        print(f"已部署仓库 → 运行时：{sha(RUNTIME)[:12]}…")
        return 0
    if args.check or not (args.capture or args.apply):
        if a is None:
            print("❌ 运行时 watchdog.py 不存在")
            return 1
        if b is None:
            print("❌ 仓库副本不存在，请先 --capture")
            return 1
        if a != b:
            print(f"❌ 不一致：运行时 {a[:12]}… / 仓库 {b[:12]}…（--capture 或 --apply 同步）")
            return 1
        print(f"✅ 一致（sha256={a[:12]}…）")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
