import json
import sqlite3

DB = "/app/data/langbot.db"
RUNNER = "plugin:langbot-team/LocalAgent/default"

RULES = """

## 轮询节奏（P0.4-A · 必须遵守）

- 对同一任务，两次**有效**状态查询之间至少间隔 **5 秒**；优先 5–10 秒。
- 调用 `comfyui_task_status` 若返回 `poll_too_fast`：
  - 必须按返回的 `retry_after_ms` 等待后再查，**不得立即重试**；
  - 被拒绝的查询**不计入**轮询预算。
- 轮询预算只统计**有效查询**（正常返回任务状态的那次），上限仍为 60 次。
- `completed` / `failed` / `cancelled` 可立即读取，不受 5 秒间隔限制。
- 不要把 `poll_too_fast` 当作任务失败或任务状态变化。
"""

con = sqlite3.connect(DB, timeout=30)
row = con.execute("select uuid, config from legacy_pipelines").fetchone()
cfg = json.loads(row[1])
prompt = cfg["ai"]["runner_config"][RUNNER]["prompt"][0]["content"]
if "轮询节奏（P0.4-A" in prompt:
    print("提示词已包含 P0.4-A 规则，跳过")
else:
    cfg["ai"]["runner_config"][RUNNER]["prompt"][0]["content"] = prompt + RULES
    con.execute(
        "update legacy_pipelines set config=? where uuid=?",
        (json.dumps(cfg, ensure_ascii=False), row[0]),
    )
    con.commit()
    print(f"已追加 P0.4-A 规则：{len(prompt)} -> {len(prompt+RULES)} 字符")
con.close()
