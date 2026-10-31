"""P0.4-B：comfyui_task_status 轮询限流（Gateway 工具层）。

契约（2026-11-01 授权）：
  | 条件                    | 行为                                   |
  | 首次查询                 | 正常返回                               |
  | 距上次有效查询 < 5 秒     | 返回 poll_too_fast + retry_after_ms    |
  | 过早查询响应              | 含 task_id、retry_after_ms             |
  | 有效查询                 | 正常返回                               |
  | 终态任务                 | 立即读取，不受间隔限制                  |

约束：
  - poll_too_fast 不改变任务状态
  - 被拒绝的查询不重置 last_valid（避免等待窗口不断延长）
  - 不 sleep（不占用执行资源）
  - 不改 Worker / 状态机 / 持久化 / Outbox
"""

import shutil
import time

SERVER = r"C:\Users\lenovo\Desktop\_说明文档\mobius\media_gateway\server.py"
src = open(SERVER, encoding="utf-8").read()


def replace_once(old, new, label):
    global src
    n = src.count(old)
    if n != 1:
        raise SystemExit(f"[ABORT] {label}: 命中 {n} 次")
    src = src.replace(old, new, 1)


# 1) Settings 增加最小轮询间隔
replace_once(
    "        self.task_timeout = float(_env(\"MOBIUS_TASK_TIMEOUT\", 900))",
    "        self.task_timeout = float(_env(\"MOBIUS_TASK_TIMEOUT\", 900))\n"
    "        # P0.4-B：同一任务的两次有效状态查询最小间隔（毫秒）\n"
    "        self.poll_min_interval_ms = float(_env(\"MOBIUS_POLL_MIN_INTERVAL_MS\", 5000))",
    "Settings.poll_min_interval_ms",
)

# 2) 在 MCP 服务器定义前插入限流状态与判定函数
anchor = '''# ---------------------------------------------------------------------------
# MCP 服务器 + REST 路由
# ---------------------------------------------------------------------------'''
gate_helper = '''# ---------------------------------------------------------------------------
# P0.4-B 轮询限流（仅作用于 comfyui_task_status 的读取路径）
# 说明：状态放在内存里（Gateway 重启后重置为「首次查询」），
#       不改任务状态机与持久化结构。
# ---------------------------------------------------------------------------

_poll_state: dict[str, dict[str, float]] = {}


def _poll_gate(task_id: str, status: str) -> dict[str, Any] | None:
    """返回 None 表示放行；返回 dict 表示要告知调用方 poll_too_fast。"""
    now = time.time()
    state = _poll_state.setdefault(task_id, {"last_valid": 0.0, "attempts": 0.0, "valid": 0.0})
    state["attempts"] += 1

    # 终态任务：允许立即读取，不受间隔限制
    if status in TERMINAL:
        state["valid"] += 1
        state["last_valid"] = now
        return None

    interval_s = max(settings.poll_min_interval_ms, 0.0) / 1000.0
    if state["last_valid"] and interval_s > 0:
        since = now - state["last_valid"]
        if since < interval_s:
            retry_ms = max(int(round((interval_s - since) * 1000)), 1)
            # 关键：不更新 last_valid，避免被拒绝的查询把等待窗口不断推后
            log.warning(
                "poll_too_fast task=%s since=%.2fs retry=%dms (valid=%d attempts=%d)",
                task_id, since, retry_ms, int(state["valid"]), int(state["attempts"]),
            )
            return {
                "ok": False,
                "error": "poll_too_fast",
                "task_id": task_id,
                "retry_after_ms": retry_ms,
                "interval_ms": int(interval_s * 1000),
                "valid_queries": int(state["valid"]),
                "attempted_queries": int(state["attempts"]),
            }

    state["valid"] += 1
    state["last_valid"] = now
    return None


def _poll_stats(task_id: str) -> dict[str, int]:
    state = _poll_state.get(task_id) or {"last_valid": 0.0, "attempts": 0.0, "valid": 0.0}
    return {
        "valid_queries": int(state["valid"]),
        "attempted_queries": int(state["attempts"]),
    }


# ---------------------------------------------------------------------------
# MCP 服务器 + REST 路由
# ---------------------------------------------------------------------------'''
replace_once(anchor, gate_helper, "插入限流辅助逻辑")

# 3) status 工具接入限流 + 返回统计
old_tool = '''async def comfyui_task_status(task_id: str) -> str:
    try:
        task = store.get(task_id)
        if task is None:
            return _json({"ok": False, "error": "unknown_task", "task_id": task_id})
        return _json({"ok": True, "task": task.public()})
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)'''
new_tool = '''async def comfyui_task_status(task_id: str) -> str:
    try:
        task = store.get(task_id)
        if task is None:
            return _json({"ok": False, "error": "unknown_task", "task_id": task_id})
        throttled = _poll_gate(task_id, task.status)
        if throttled is not None:
            return _json(throttled)
        return _json({"ok": True, "task": task.public(), "poll": _poll_stats(task_id)})
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)'''
replace_once(old_tool, new_tool, "status 工具限流")

backup = f"{SERVER}.bak-p04b-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(SERVER, backup)
open(SERVER, "w", encoding="utf-8").write(src)
print("P0.4-B 已写入；备份:", backup)
