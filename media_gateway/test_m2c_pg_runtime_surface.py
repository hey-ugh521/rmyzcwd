"""M2c/PG 运行时方法接线回归（防止手写清单漏挂）。"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import server  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


class Dummy:
    pass


def main() -> int:
    server._wire_gateway_runtime(Dummy, server.TaskStore)
    required = ("bootstrap", "_worker_loop", "_spawn_lease_renewal", "_lease_renewal_loop",
                "_reconcile_core_running", "_run_image", "_run_image_impl",
                "_resume_running", "_resume_running_impl", "_outbox_loop", "_send_core_event")
    missing = [n for n in required if not hasattr(Dummy, n)]
    check("动态复制覆盖全部运行时方法", not missing, str(missing))
    check("_run_image_impl 在 TaskStore", hasattr(server.TaskStore, "_run_image_impl"))
    check("_spawn_lease_renewal 在 TaskStore", hasattr(server.TaskStore, "_spawn_lease_renewal"))
    src = (HERE / "server.py").read_text(encoding="utf-8")
    check("worker spawn 在 try 内（异常不留 running）",
          "lease_task = None\n            try:\n                lease_task = self._spawn_lease_renewal" in src)
    check("PG 启动有必需方法断言", "_missing = [n for n in _required" in src)
    check("4xx 死信不堵 outbox", "不可重试，丢弃" in src)
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
