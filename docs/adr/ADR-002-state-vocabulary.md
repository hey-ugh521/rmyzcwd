# ADR-002：统一任务状态词汇

- 状态：已采纳（2026-10-30 起逐步落地，2026-11-02 补齐 submission_unknown 路径）
- 背景：Media Gateway、Core、LangBot 三处曾各自使用不同状态词（succeeded / success / done / error），导致跨组件判断不一致、前端显示与探针统计对不上。

## 决策

统一采用六个状态词，跨组件一致：

| 状态 | 含义 |
|---|---|
| `pending` | 已入库、未开始执行 |
| `running` | 执行中 |
| `completed` | 成功完成，产物可用 |
| `failed` | 明确失败（有错误码/错误信息） |
| `cancelled` | 用户或系统取消 |
| `submission_unknown` | **提交后结果不确定**（已发出但未拿到确定答复 / 恢复期无法证明是否执行过） |

规则：
1. `submission_unknown` 是终态，但**禁止自动重提**，必须人工确认。
2. Core 侧历史词汇（`succeeded`/`succeeded`/`canceled`）保留为映射目标：Gateway 事件投影时 `completed→succeeded`、`cancelled→canceled`。
3. 终态任务不参与轮询节流（避免读取结果被限流挡住）。

## 落地位置

| 组件 | 位置 |
|---|---|
| Gateway | `media_gateway/taskstore.py` `STATUS_*`、`mark_submission_unknown()` |
| Gateway 提交路径 | `_run_image`：`ConnectTimeout→failed`、其他超时→`_SubmissionUnknown→submission_unknown` |
| Core | `core/store.py` `task_apply_event()` 状态映射 |
| 探针 | `phase` 使用 `received/replied/run_*` 等独立词汇，不与任务状态混用 |

## 验证

- `media_gateway/test_submission_unknown.py`：黑洞 ComfyUI 下提交超时 → `submission_unknown`；轮询 61 次不取消任务；并发两条互不影响。
- Gateway 事件投影测试（stage2）：状态映射正确，重放按 seq 去重。
