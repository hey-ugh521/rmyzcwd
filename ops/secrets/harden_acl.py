"""M3a：收紧 secrets.json ACL（安装器/运维可单独调用）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from core import secrets  # noqa: E402


def main() -> int:
    path = secrets.secrets_path()
    ok = secrets.harden_acl(path)
    print(f"secrets ACL: {'OK' if ok else 'FAILED'} -> {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
