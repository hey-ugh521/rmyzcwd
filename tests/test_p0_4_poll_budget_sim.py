"""P0.4-A 轮询预算模拟测试（确定性层，完全离线）。

用模拟的状态响应 + 按提示词规则实现的参考 Agent 循环，验证：
  - 连续 running 响应下，有效轮询不超过 60 次
  - 达到预算后停止等待，并报告“仍在执行”
  - 不调用 cancel / submit
  - 被 poll_too_fast 拒绝的查询不计入预算
  - 后台任务状态不被改变（模拟后端继续 running）

运行：D:\\AI\\qqbot-mcp\\Scripts\\python.exe tests\\test_p0_4_poll_budget_sim.py
"""

from __future__ import annotations

import sys

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name} {detail}")


class MockBackend:
    """永远返回 running 的模拟任务；记录被谁调用过。"""

    MAX_VALID = 60
    MIN_INTERVAL_S = 5.0

    def __init__(self, status: str = "running") -> None:
        self.status = status
        self.attempts = 0
        self.valid = 0
        self.submit_calls = 0
        self.cancel_calls = 0
        self.last_valid_at: float | None = None
        self.clock = 0.0  # 虚拟时钟（秒）

    # ---- 模拟工具 ----
    def task_status(self) -> dict:
        self.attempts += 1
        if self.status in ("completed", "failed", "cancelled"):
            self.valid += 1
            return {"ok": True, "task": {"status": self.status}}
        if self.last_valid_at is not None:
            since = self.clock - self.last_valid_at
            if since < self.MIN_INTERVAL_S:
                retry_ms = max(int((self.MIN_INTERVAL_S - since) * 1000), 1)
                return {"ok": False, "error": "poll_too_fast",
                        "task_id": "sim-task", "retry_after_ms": retry_ms}
        self.valid += 1
        self.last_valid_at = self.clock
        return {"ok": True, "task": {"status": self.status}}

    def task_submit(self) -> None:
        self.submit_calls += 1

    def task_cancel(self) -> None:
        self.cancel_calls += 1

    # ---- 虚拟时间 ----
    def advance(self, seconds: float) -> None:
        self.clock += seconds


def run_agent_loop(backend: MockBackend, max_valid: int = 60) -> dict:
    """按 P0.4-A 提示词规则运行的参考实现。"""
    valid = 0
    attempts = 0
    reported = None
    while valid < max_valid:
        resp = backend.task_status()
        attempts += 1
        if resp.get("error") == "poll_too_fast":
            # 按 retry_after 等待，不计入预算
            backend.advance(resp["retry_after_ms"] / 1000.0)
            continue
        status = (resp.get("task") or {}).get("status")
        if status in ("completed", "failed", "cancelled"):
            reported = f"terminal:{status}"
            break
        valid += 1
        if valid >= max_valid:
            reported = "budget_exhausted"
            break
        backend.advance(MockBackend.MIN_INTERVAL_S)
    if reported is None:
        reported = "budget_exhausted"
    return {"valid": valid, "attempts": attempts, "reported": reported}


def main() -> int:
    # 场景 1：永远 running -> 预算耗尽后停止，不 cancel/submit
    b = MockBackend("running")
    r = run_agent_loop(b)
    check("1.1 有效轮询不超过 60", r["valid"] <= 60, str(r["valid"]))
    check("1.2 恰好用满 60 次预算", r["valid"] == 60, str(r["valid"]))
    check("1.3 报告中说明仍在执行", r["reported"] == "budget_exhausted", r["reported"])
    check("1.4 未调用 submit", b.submit_calls == 0)
    check("1.5 未调用 cancel", b.cancel_calls == 0)
    check("1.6 后台任务仍为 running", b.status == "running")
    check("1.7 尝试次数 >= 有效次数", r["attempts"] >= r["valid"], f"{r['attempts']}/{r['valid']}")

    # 场景 2：前 30 次 running 后完成
    b2 = MockBackend("running")
    calls = {"n": 0}
    original = b2.task_status

    def flaky() -> dict:
        calls["n"] += 1
        if calls["n"] == 30:
            b2.status = "completed"
        return original()

    b2.task_status = flaky  # type: ignore[method-assign]
    r2 = run_agent_loop(b2)
    check("2.1 任务完成时停止轮询", r2["reported"] == "terminal:completed", r2["reported"])
    check("2.2 完成前有效次数 <= 60", r2["valid"] <= 60, str(r2["valid"]))

    # 场景 3：被拒绝的查询不计入预算（模拟“每秒查一次”）
    b3 = MockBackend("running")
    valid = 0
    rejected = 0
    while valid < 60:
        resp = b3.task_status()
        if resp.get("error") == "poll_too_fast":
            rejected += 1
            b3.advance(0.5)  # 恶意快速重试，只推进 0.5s
            continue
        valid += 1
        b3.advance(0.5 if valid % 3 == 0 else MockBackend.MIN_INTERVAL_S)
    check("3.1 被拒查询不计入有效预算", valid == 60, str(valid))
    check("3.2 存在被拒记录（说明限流生效）", rejected > 0, str(rejected))

    print(f"\n结果：{len(PASSED)} 通过，{len(FAILED)} 失败")
    for name, detail in FAILED:
        print(f"  [FAIL] {name} {detail}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())

