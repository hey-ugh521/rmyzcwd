import json
import sqlite3

DB = "/app/data/langbot.db"
RUNNER = "plugin:langbot-team/LocalAgent/default"

MEDIA_RULES = """

## 生图任务执行规则（P0.4 · 必须遵守）

生成图片时**不要**调用 `comfyui_generate`（它是同步阻塞接口，仅在历史兼容时保留）。

正确流程：

1. 用 `comfyui_task_submit` 提交任务。**每次用户主动发起的生图请求，必须自己生成一个新的
   `idempotency_key`**（例如 `qq-<时间戳>-<随机4位>`），并在后续重试中**复用同一个 key**。
   不要把 prompt 的哈希当作幂等键。
2. 提交后立即拿到 `task_id`。若 submit 调用超时且**没有拿到 task_id**，只能用**同一个幂等键**
   安全重试，**不得**换 key 新建任务。
3. 用 `comfyui_task_status` 轮询，间隔 5–10 秒，**最多 60 次**（约 10 分钟）。
4. 按状态行事：

| 状态 | 你要做什么 |
|---|---|
| `pending` | 等待，继续轮询 |
| `running` | 等待，继续轮询 |
| `completed` | 读取 `result`，把产物交付给用户（图片 URL 要原样保留） |
| `failed` | 如实报告失败，附 `error_code` / `error_message` |
| `cancelled` | 如实报告任务已取消 |
| `submission_unknown` | 报告"结果不确定"，**绝对不要自动重新提交**，请用户确认 |

5. 轮询达到 60 次仍未结束：**停止轮询**，告诉用户"任务仍在后台执行"，把 `task_id` 交给用户，
   以便稍后查询。**轮询耗尽不等于任务失败**，也不要取消后台任务。
6. 失败或取消时**不要**悄悄退回 `comfyui_generate` 同步调用；如实告诉用户发生了什么和下一步选择。

汇报风格：以上状态与错误信息要如实体现在回复里，不要编造"已完成"。
"""

con = sqlite3.connect(DB, timeout=30)
row = con.execute("select uuid, config from legacy_pipelines").fetchone()
cfg = json.loads(row[1])
prompt = cfg["ai"]["runner_config"][RUNNER]["prompt"][0]["content"]
if "生图任务执行规则（P0.4" in prompt:
    print("提示词已包含 P0.4 规则，跳过")
else:
    cfg["ai"]["runner_config"][RUNNER]["prompt"][0]["content"] = prompt + MEDIA_RULES
    con.execute(
        "update legacy_pipelines set config=? where uuid=?",
        (json.dumps(cfg, ensure_ascii=False), row[0]),
    )
    con.commit()
    print(f"已追加 P0.4 规则：{len(prompt)} -> {len(prompt + MEDIA_RULES)} 字符")
con.close()
