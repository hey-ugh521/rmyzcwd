# P0 异步媒体任务 · 方案审查（P0.1 + P0.2）

> 状态：**待批准 · 未动代码**
> 边界：**只解决媒体任务的异步执行**。不重构 Core，不统一人格/记忆/Session。
> 上游基线：`docs/ARCHITECTURE-v1.1.md`（§8 任务生命周期）

---

## P0.1 现状审查

### 1. 当前调用链（实测代码，`media_gateway/server.py`）

```
Agent (LangBot LocalAgent)
  │  MCP tools/call
  ▼
comfyui_generate ──► submit_generate(async_mode=False)
  │                      │
  │                      ▼
  │                 TaskStore.submit()  → 内存 dict + asyncio.Queue
  │                      │
  │                      ▼
  │                 worker 循环（并发 1）
  │                      │
  │                      ▼
  │                 ComfyUI /prompt → 轮询 /history
  │                      │
  │                 等待完成（最长 view_timeout=480s）
  ▼
返回结果（**整个链路口径上都在等**）
```

### 2. 现状事实（代码证据）

| 项 | 现状 | 位置 |
|---|---|---|
| 任务存储 | **纯内存** `self._tasks: dict` + `asyncio.Queue` | `TaskStore.__init__` |
| 持久化 | **无**（网关重启 → 全部任务丢失） | — |
| 状态取值 | `queued / running / succeeded / failed / canceled` | `TaskStore` |
| 已有异步工具 | ✅ `comfyui_task_submit` / `comfyui_task_status` / `comfyui_task_cancel` | L2437-2480 |
| 阻塞工具 | ✅ `comfyui_generate` = submit + 等待（view_timeout 480s） | L2370 |
| 幂等 | **无**（重复提交 = 重复出图，消耗 GPU） | — |
| 错误传播 | 失败时 `task.status=failed`，但 `comfyui_generate` 只返回异常文本 | — |
| 取消 | 排队中置 `canceled`；已提交 ComfyUI 走 `/interrupt` | `TaskStore.cancel` |
| Core 任务表 | ✅ 已有 `store.task_create/update/get/list/active`，`/api/tasks` 读、`/api/tasks/{id}/cancel` | `core/store.py`、`core/server.py` |

### 3. 缺陷结论（为什么必须改）

1. **线程池占用**：一次工具调用 130~300s，LangBot 的 blocking executor 被占满 → 残留线程 → `BlockingWorkCapacityError` → 会话抱死（已实测两次）。
2. **超时被迫层层抬高**：为了活下来打了 5 个超时补丁，容器重建即失效。
3. **无持久化**：网关重启后「任务消失」，用户以为在生成、实际什么都没发生。
4. **无幂等**：Agent 重试 → 重复出图（真实 GPU 成本）。
5. **状态词汇与架构基线不一致**：基线 §8 是 `created → queued → running → succeeded/failed/cancelled`，你要求的是 `pending → running → completed/failed/cancelled`。

---

## P0.2 设计

### 1. 三个工具的契约（最终形态）

| 工具 | 行为 | 时延上限 |
|---|---|---|
| `comfyui_task_submit` | 校验参数 → 落库 → 入队 → **立即返回** `task_id` | < 1s |
| `comfyui_task_status` | 读任务（含进度/错误/结果） | < 200ms |
| `comfyui_task_cancel` | 排队中直接取消；运行中请求中断 | < 1s |

`comfyui_generate` **保留为兼容包装**（submit + 有界等待），文档标注 deprecated；
Agent 侧统一改用 submit + 轮询（通过 MCP instructions + 工具描述引导）。

### 2. 状态机（ADR-002 提议）

```
        submit
          │
          ▼
     ┌─────────┐   worker 取到   ┌──────────┐
     │ pending │ ───────────────►│ running  │
     └─────────┘                 └──────────┘
          │                        │   │   │
   cancel │              complete  │   │   │ fail
          ▼                        ▼   │   ▼
     ┌───────────┐          ┌───────────┐ ┌────────┐
     │ cancelled │          │ completed │ │ failed │
     └───────────┘          └───────────┘ └────────┘
                              取消竞态：running 阶段请求取消 → 
                              若 ComfyUI 仍产出结果，记 completed 但标 cancel_requested=true
```

**词汇对齐（需 ADR-002 批准）**：
- 本轮采用你指定的 `pending / running / completed / failed / cancelled`
- 旧值映射：`queued→pending`、`succeeded→completed`、`canceled→cancelled`（读取时双向兼容）
- 架构基线 §8 的 `created` 阶段并入 `pending`（P0 不引入独立 created 态）

### 3. 持久化设计（任务状态不丢）

**双写职责划分**（避免把技术债从线程池搬到任务管理）：

| 职责 | 归属 | 存储 |
|---|---|---|
| **业务任务状态（权威）** | **Core** | `mobius.db.tasks`（已有表） |
| **执行快照（恢复用）** | Gateway | `media_gateway/data/tasks.db`（新增 SQLite） |

- Gateway 提交任务后，**先落本地执行快照，再向 Core 上报**（`POST /api/tasks` 或复用现有 store 接口）。
- Core 不可用 → **不阻塞出图**（可用性优先）：本地继续执行，上报进 `outbox` 表，恢复后按序补报（幂等键去重）。
- 核心原则：**Core 是"业务状态"唯一权威；Gateway 的本地库只是"执行恢复点"**，不承诺对外展示同一份状态。

**本地表结构（执行快照）**：

```sql
CREATE TABLE task_snapshot (
  task_id         TEXT PRIMARY KEY,
  idempotency_key TEXT UNIQUE,          -- 幂等：同 key 只建一次
  kind            TEXT NOT NULL,        -- image / voice
  status          TEXT NOT NULL,        -- pending/running/completed/failed/cancelled
  payload_json    TEXT NOT NULL,        -- 请求参数（prompt/model/seed/...）
  prompt_id       TEXT,                 -- ComfyUI prompt_id
  result_json     TEXT,
  error_code      TEXT,
  error_message   TEXT,
  cancel_requested INTEGER DEFAULT 0,
  attempts        INTEGER DEFAULT 0,
  created_at      REAL NOT NULL,
  started_at      REAL,
  finished_at     REAL,
  updated_at      REAL NOT NULL
);
CREATE INDEX idx_status ON task_snapshot(status);

CREATE TABLE outbox (                    -- Core 上报缓冲
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id        TEXT NOT NULL,
  op             TEXT NOT NULL,          -- create / update
  payload_json   TEXT NOT NULL,
  created_at     REAL NOT NULL,
  sent_at        REAL
);
```

### 4. 重启恢复算法

网关启动时：

1. `running` 的任务 → 用 `prompt_id` 查 ComfyUI `/history`：
   - 有结果 → 补记为 `completed`
   - ComfyUI 队列里有 → 继续等待
   - 都没有 → 标 `failed`，`error_code=lost_after_restart`
2. `pending` 的任务 → 重新入队（可继续执行）
3. `outbox` 未发送项 → 重试上报 Core

### 5. 幂等

- `submit` 新增可选 `idempotency_key`（不传则用 `sha256(payload)` 生成）。
- 命中已有 key → **直接返回原 task_id，不新建任务**（响应里带 `idempotent_hit: true`）。
- 保证「Agent 重试 / 网络抖动重发」不会重复出图。

### 6. 超时隔离

| 环节 | 超时 | 说明 |
|---|---|---|
| submit | 1s | 只做落库+入队 |
| status | 200ms | 只读本地库 |
| cancel | 1s | 只置标记 |
| Worker 执行 | 独立（`COMFY_TASK_TIMEOUT`，默认 900s） | 与工具调用超时**彻底分离** |
| 单次 ComfyUI 轮询 | 5s | 失败重试 3 次后判 failed |

### 7. 错误传播（不无限轮询）

`status` 返回结构：

```json
{
  "ok": true,
  "task": {
    "task_id": "g_xxx",
    "status": "failed",
    "error_code": "comfy_rejected | comfy_timeout | cuda_oom | model_missing | lost_after_restart",
    "error_message": "human readable",
    "retryable": false
  }
}
```

- `failed`/`cancelled` 为**终态**：Agent 必须停止轮询并如实告知用户。
- `retryable=true` 时允许 Agent 重新 submit（带同一 idempotency_key 不会重复建任务）。

### 8. Agent 侧改动（提示词级，不动 LangBot 源码）

- MCP `instructions` + 三个工具描述里明确：生图流程 = submit → 每 5~10 秒 status → 终态停止。
- 禁止对大任务使用 `comfyui_generate`（标注 deprecated）。
- 轮询上限：例如 60 次（约 10 分钟），超限按失败处理。

---

## 验收标准（你给的 7 项，逐项对应测法）

| # | 验收项 | 测法 | 通过标准 |
|---|---|---|---|
| 1 | submit 1~3s 返回 task_id | 连续 5 次 submit 计时 | 全部 < 1s（网关本地，仅落库入队） |
| 2 | 连续提交不长期占用线程池 | 连发 5 个任务后观察 LangBot 阻塞池 | 无 `BlockingWorkCapacityError`，工具调用耗时均 < 2s |
| 3 | Agent 识别三种状态 | 观察 monitoring_tool_calls 与 run 输出 | pending/running/completed 均被正确读取 |
| 4 | 冷启动不阻塞工具调用 | 杀 ComfyUI 后 submit + 轮询 | 提交立即返回；状态依次变化；最终出图 |
| 5 | 失败能传原因 | 构造非法模型名 / 断 ComfyUI | status 返回 `error_code`，Agent 如实报错 |
| 6 | 网关重启处理未完成任务 | 提交后重启网关 | `running` 任务被正确判定（补记/继续/标 failed） |
| 7 | 不改 LangBot 上游源码 | 全流程回放 | 仅靠配置/提示词完成，不新增容器补丁 |

---

## 五处超时补丁的撤销矩阵（逐个验证，不假设）

| 补丁 | 保护的是哪条路径 | 异步化后还需要吗 | 验证方法 |
|---|---|---|---|
| ① LangBot `handler.py` CALL_TOOL 180 | 任何 >180s 的工具调用 | 预期**不需要**（单次调用 <2s） | 还原为 180 → 跑一轮 submit+poll 全流程 |
| ② `context_builder.py` run 期限 900 | 整轮 run 时长 | **可能仍需要**（多轮轮询 + 推理可能 >300s） | 还原 300 → 观察长任务 run 是否超时；若超时则保留但降到 480 |
| ③ 运行时 `langbot_api.py` call_tool 600 | 插件→运行时调用 | 预期**不需要** | 同 ① |
| ④ 运行时 `LONG_RUNNING_OPERATION_TIMEOUT` 600 | 长操作转发 | 预期**不需要** | 同 ① |
| ⑤ 运行时 `runner/resources.py` 600 | Runner 工具调用 | 预期**不需要** | 同 ① |
| ⑥ `respback.py` 图片桥接 | 图片送达（与超时无关） | **必须保留** | 不属于超时补丁，单独保留 |

**撤销方式**：每次只还原一个 → 重启 → 跑「冷启动 + 正常生图」两条路径 → 记录结果 → 再还原下一个。
任何一项验证失败即回滚该项，并记录原因（不整体删除）。

---

## 范围边界（明确不做）

- ❌ 不重构 Core / 不统一人格、记忆、Session
- ❌ 不引入消息队列（Redis/Kafka）
- ❌ 不改 LangBot 上游逻辑（只通过配置/提示词）
- ❌ 不动 ComfyUI 工作流
- ❌ 不做多用户/权限

---

## 阶段拆分与交付

| 阶段 | 工作 | 交付物 |
|---|---|---|
| P0.3 | 实现持久化 TaskStore + 状态机 + 幂等 | 代码 + 单元测试 |
| P0.4 | Agent 侧改用 submit/status（instructions + 工具描述） | 配置改动 + 回放记录 |
| P0.5 | 回归 7 项验收 + 逐个撤销 5 个补丁 | 验收记录表 |
| P0.6 | 更新 `ARCHITECTURE-v1.1.md` §8 + ADR-002 | 文档 |

---

## 风险与回滚

| 风险 | 缓解 | 回滚 |
|---|---|---|
| 双写（本地 + Core）不一致 | Core 为准 + outbox 补报 + 幂等键 | 关闭上报开关，仅本地 |
| 轮询频繁导致 token 消耗 | 轮询间隔 5~10s，不可调更密 | 改回同步 wrapper |
| 状态词汇迁移影响 Console | 读取层双向兼容旧值 | 保留旧值映射 |
| 重启恢复误判 | 只对 `running` 做判定，`pending` 直接重排 | 全量标 failed 保守策略 |

---

## 附：审查意见采纳记录（2026-11-01）

| 审查项 | 原设计 | 修订后（已实施） |
|---|---|---|
| 幂等键 | `sha256(payload)` 直接当幂等键 | 拆成 `idempotency_key`（请求身份，缺省生成 `req_<uuid>`）+ `payload_hash`（内容指纹）；同 key 异 hash 返回 409 |
| Outbox 一致性 | 未明确 | 任务写入/状态变更与 outbox 事件在**同一 BEGIN IMMEDIATE 事务**内提交 |
| 重启恢复 | 查不到即 `failed` | 新增终态 `submission_unknown`；证据不足时禁止自动重提 |
| 轮询超限 | 「超限按失败处理」 | **不成立**：轮询预算耗尽只停止 Agent 轮询，向用户报告“仍在执行”，Gateway worker 继续；任务状态不受影响 |
| 状态冲突 | 未定义 | **禁止双向覆盖**：Gateway 只提交执行事实，Core 依据事件用 `task_id + version` 去重排序；冲突保留不覆盖 |

实施结果见 `docs/P0.3-implementation-report.md`。
