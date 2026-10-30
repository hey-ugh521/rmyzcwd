# Möbius Core Unification Architecture v1.1

> 状态：**修订稿 · 待冻结**
> 基线：v1.0 草案（2026-10-30，评审通过方向，返回修订）
> 本文件定义**控制权、数据所有权与状态迁移边界**，不重新实现已有功能。
> **v1.1 相对 v1.0 的修订见附录 A（逐条对应评审编号）。**

---

## 0. 冻结条件

架构基线在以下条件全部满足后才冻结：

1. 评审提出的 P0 与 P1 修订全部落入本文档（本轮已完成）。
2. P2 项进入实施待办（不阻塞冻结，但必须在对应阶段实现前补齐）。
3. §12 安全阻塞项**实际运行验证通过**——文档中列出要求 ≠ 已完成整改。

---

## 1. 架构原则（最高约束）

### 1.1 单一事实来源（Single Source of Truth）

> 每类业务状态只能存在一个权威写入者。其他服务可以缓存、读取、转发或投影该状态，
> **不得形成独立的权威副本**。

| 状态 | 权威写入者 | 其他服务允许的行为 |
|---|---|---|
| Identity（人格、角色、权限） | **Core** | 读取、缓存、投影 |
| Session（会话、上下文） | **Core** | 读取、投影 |
| Memory（情景、语义） | **Core** | 读取、投影 |
| Task（生命周期、队列） | **Core** | 读取、投影 |
| Audit / Events | **Core** | **只允许上报**，不得自行修改事件状态 |
| Media | Core 负责任务编排；执行后端负责实际执行 | 后端只执行，不拥有任务状态 |
| QQ 消息 | LangBot 负责传输；Core 负责业务处理 | LangBot 不得独立决定业务语义 |
| 模型推理 | Provider Adapter 负责适配；Core 负责决策 | Adapter 不得内嵌业务规则 |

**判据**：任何服务若能在 Core 不知情的情况下改变左列某类状态，即视为架构违规。

### 1.2 模型可替换原则（v1.1 修正判据）

> 人格属于 Core，不属于模型；记忆属于 Core，不属于模型；会话状态属于 Core，不属于模型；
> 任务规划属于 Core，不属于模型。DeepSeek 只是当前选用的推理后端。

**可检验判据（修正后）**：

> Core 的**业务域、认知编排与统一接口层**不得依赖任何厂商专有字段。
> 厂商专有字段只允许出现在 Provider Adapter 内部，并由**契约测试**保证不向上层泄漏。

- ✅ 允许：`core/adapters/model/deepseek.py` 出现 `reasoning_effort`
- ❌ 禁止：`core/cognition/`、`core/task/`、事件 schema 出现厂商字段
- ❌ 禁止：Core 业务逻辑读取 `ModelResponse.provider_meta` 中的厂商字段（见 §7.1）

> v1.0 的"`core/` 内 grep 不到厂商字段"与 §4 把 adapter 放在 `core/adapters/` 自相矛盾，此处修正。

### 1.3 四不原则

- Core 拥有任务状态，不代表 Core 必须亲自执行每个任务。
- Core 拥有人格，不代表人格必须由一个巨大的 System Prompt 表达。
- Core 拥有记忆，不代表每轮都要把所有记忆塞进上下文。
- Core 拥有决策权，不代表每个任务都必须使用同一个模型。

---

## 2. 现状与目标

### 2.1 迁移前权威位置

| 能力 | 当前权威位置 |
|---|---|
| 人格 | LangBot Pipeline（prompt 内嵌） |
| 用户记忆 | LongTermMemory 插件 |
| 对话上下文 | LangBot transcript |
| 任务状态 | Media Gateway 内存 / Core（分裂） |
| 媒体调度 | Media Gateway |
| 审计 | Core |
| 事件 | 不存在统一事件 |

### 2.2 目标

全部收敛到 Core（见 §1.1）。LangBot 最终只保留：QQ 收发、格式转换、平台事件处理。

---

## 3. 六服务职责边界

| 服务 | 目标职责 | 明确不做 |
|---|---|---|
| **Möbius Core** | 控制平面 + 状态权威 | 不做 QQ 协议、不跑 ComfyUI 工作流 |
| **Media Gateway** | 协议面适配、执行代理 | **不拥有任务状态** |
| **LangBot** | QQ 消息适配器 | 不做人格、记忆、任务规划 |
| **NapCat** | QQ 协议端 | 不解释业务 |
| **ComfyUI** | 图像生成执行 | 不感知任务语义 |
| **Open WebUI Pipe / Console** | 入口层 | 不做业务决策 |

---

## 4. Core 模块划分

```
core/
├── identity/     人格、角色、权限
├── session/      会话解析、上下文组装、消息仓库访问
├── memory/       情景记忆、语义记忆、检索、遗忘、冲突裁决
├── cognition/    模型调用、决策、工具编排
├── task/         生命周期状态机、队列、租约
├── audit/        操作与执行记录
├── events/       事件写入、outbox、事件链查询
├── media/        媒体编排
└── adapters/
    ├── model/    Provider Adapter（唯一允许出现厂商字段的位置）
    └── ingress/  入口适配（QQ via LangBot、WebUI、Console）
```

**约束**：`cognition/` 不直接调用厂商 SDK；`adapters/model/` 不含业务规则；厂商字段不得越过 adapter 边界（契约测试保证）。

---

## 5. 数据模型：四类存储必须分离（v1.1 新增，对应 B-02）

v1.0 把"消息正文"同时要求"最小化"和"进入事件 payload"，存在定义缺口。v1.1 明确四类数据各有归属：

| 数据 | 存储位置 | 原则 |
|---|---|---|
| **Event Log**（发生了什么） | `events` | **不存正文**，只存引用与摘要 |
| **Conversation Message Store**（会话正文） | `conversation_messages` | 受权限与保留策略约束；Core 构建上下文的权威来源 |
| **Session**（会话状态/上下文装配） | `sessions` + 按需组装 | 不重复永久保存完整上下文 |
| **Memory**（长期价值信息） | `memory` + `memory_history` | 保留来源与原始证据关联 |
| **附件** | 文件存储 | 数据库只存引用与元数据 |

**四者不能混成一张表。**

### 5.1 Event Log 结构

```json
{
  "event_id": "uuid",
  "event_version": 1,
  "event_type": "message.received",
  "idempotency_key": "qq:<bot_id>:<platform_message_id>",
  "correlation_id": "uuid",
  "trace_id": "uuid",
  "conversation_id": "uuid",
  "actor_id": "uuid",
  "source": { "platform": "qq", "channel_id": "group_id", "user_id": "user_id" },
  "payload": {
    "message_type": "text",
    "content_ref": "conversation_message_uuid",
    "content_hash": "sha256:..."
  },
  "occurred_at": "ISO-8601",
  "received_at": "ISO-8601"
}
```

字段说明：

| 字段 | 用途 |
|---|---|
| `event_version` | 事件结构版本，支持演进 |
| `idempotency_key` | 防 LangBot 重试导致重复写入（**唯一约束**） |
| `actor_id` | 事件主体（谁触发的） |
| `correlation_id` | 关联上游请求（如一次 HTTP 请求引发的多事件） |
| `occurred_at` / `received_at` | 区分"事件发生"与"Core 接收"时间 |
| `content_ref` + `content_hash` | 指向 `conversation_messages`，正文不入事件表 |

### 5.2 事件链

```
message.received
  → conversation.resolved
  → context.assembled
  → llm.requested
  → tool.proposed
  → tool.executed
  → task.created
  → task.running
  → task.completed
  → message.sent
```

### 5.3 事务边界（v1.1 新增）

**事件写入与业务状态更新必须在同一事务内完成**，避免"消息已处理但事件未落库"：

```sql
BEGIN;
  INSERT INTO events (...);              -- 事件
  UPDATE sessions / tasks SET ...;       -- 状态
  INSERT INTO outbox (...);              -- 待发送消息
COMMIT;
```

### 5.4 可靠投递与去重（v1.1 新增，对应 M-03）

- **去重**：`events.idempotency_key` 唯一索引；重复上报返回已存在事件，不重复处理。
- **崩溃恢复**：事务保证事件与状态一致；重启后扫描 `outbox` 中未完成项续发。
- **发送重试**：由独立发送器读 `outbox`，按 `idempotency_key` 幂等发送，失败按退避重试并记录 `attempt`。
- **顺序**：同一 `conversation_id` 内按单调递增序列处理。
- 不引入消息队列；SQLite 事务 + outbox 足够当前规模。

### 5.5 保留策略（v1.1 修订）

| 数据 | 保留 | 说明 |
|---|---|---|
| 任务事件（task.*） | **30 天热存储**，之后归档 | 归档后不参与日常查询 |
| 安全审计事件 | **长期保留** | 不随任务事件归档 |
| 消息正文（`conversation_messages`） | 按会话策略 | **有明确权威存储位置**，不因事件归档而丢失 |
| 模型上下文（context.assembled 的完整 messages） | 不永久保存 | 只记录摘要（token 数、消息条数、trace 关联） |
| trace_id | 与事件同寿命 | 跨事件串联 |

**审计脱敏要求（v1.1 新增）**：长期保留的审计内容**不得包含**明文密钥、完整 Authorization Header、敏感消息正文、未脱敏的模型请求。

---

## 6. 会话与身份

### 6.1 会话 ID 规则

| 场景 | conversation_id |
|---|---|
| QQ 群聊 | `qq:group:<group_id>` |
| QQ 私聊 | `qq:private:<user_id>` |
| Open WebUI | `web:user:<user_id>` |
| Console | `console:user:<user_uuid>`（v1.1 修正：不再固定 `console:admin`） |

- `conversation_id` 生成后**稳定不变**，不随重启变化。
- `message_type` 仅用于消息处理策略，**不参与会话 ID 生成**。

### 6.2 群聊身份与记忆隔离（v1.1 新增，对应 H-02）

群聊共享会话，**不代表群内成员共享私人记忆**。

| 概念 | 含义 |
|---|---|
| `conversation_id` | 群聊公共会话 |
| `actor_id` | 实际发言者 |
| `user_memory_scope` | 用户个人记忆域 |
| `group_memory_scope` | 群聊共享记忆域 |

> 会话上下文与用户记忆权限相互独立。群聊会话可以共享公共上下文，
> 但用户个人记忆不得因共享会话而自动向其他成员暴露。

私聊与群聊的上下文**不做隐式合并**；跨域引用必须显式且经权限检查。

### 6.3 权限矩阵（v1.1 新增，对应 M-04）

**默认拒绝**。v1 不实现复杂 RBAC，但以下矩阵必须成立：

| 操作 | owner | admin | user | guest |
|---|---|---|---|---|
| 修改角色卡 | ✅ | ✅ | ❌ | ❌ |
| 读取本人记忆 | ✅ | ✅ | ✅ | ❌ |
| 读取他人记忆 | ✅ | ❌ | ❌ | ❌ |
| 删除记忆 | ✅ | ✅（需审计） | 仅本人 | ❌ |
| 查看审计 | ✅ | ✅ | ❌ | ❌ |
| 调用生图 / TTS | ✅ | ✅ | ✅（限额） | ❌ |
| 群内管理操作 | ✅ | ✅ | ❌ | ❌ |
| 修改配置 / 凭证 | ✅ | ❌ | ❌ | ❌ |

---

## 7. Model Backend Contract

### 7.1 统一请求 / 响应（v1.1 修订：provider_meta 不得上浮）

```python
ModelRequest(
    messages, tools,
    reasoning="off|low|medium|high",   # 归一化，非厂商值
    max_tokens, temperature, stop,
)
ModelResponse(
    text, tool_calls, usage, finish_reason,
    provider_meta,   # 仅诊断用：不得参与业务决策
)
```

**约束**：

- Core 只能读取归一化字段。
- `provider_meta` 只能进入 Adapter 内部诊断日志，日志必须脱敏。
- **禁止** Core 依据 `provider_meta` 做业务判断。

### 7.2 能力协商（v1.1 扩充）

| 能力 | 含义 |
|---|---|
| `text` | 文本输入输出 |
| `vision` | 图像输入 |
| `tool_call` | 工具调用 |
| `structured_output` | 结构化输出 |
| `streaming` | 流式响应 |
| `reasoning_control` | 推理强度控制（并非所有后端支持） |
| `context_window` | 上下文长度上限 |
| `max_output_tokens` | 最大输出长度 |

- Core 声明 `needs: [...]`；Adapter 声明 `supports: [...]`。
- 不满足时由 **Core** 决定，不由 Adapter 自行决定。

### 7.3 降级策略（v1.1 新增）

不能满足能力要求时，Core 三选一：

1. **换后端**（其他 adapter 支持该能力）
2. **可接受的替代表示**（如把图片换成经用户授权生成的文字描述）
3. **明确返回能力不足**（失败，而不是静默降级）

> **禁止**：默认删掉图片继续推理——这会让模型在缺少关键输入时产出"看似正常"的错误回答。

### 7.4 反面教材（保留）

2026-10-30 事故：LangBot 把配置里的 `provider_default` 原样作为 `reasoning_effort` 发给 DeepSeek，
返回 422，该会话所有消息失败。根因是厂商值穿透控制层。

### 7.5 契约测试（v1.1 新增）

- 静态检查：`core/cognition/`、`core/task/`、`core/events/` 不得出现厂商字段名。
- 行为测试：构造只实现部分能力的 mock adapter，验证 Core 的降级决策正确。
- 泄漏测试：`ModelResponse` 序列化到事件/审计时不得包含 `provider_meta`。

---

## 8. 任务统一生命周期（v1.1 补充幂等与恢复，对应 M-02）

```
created → queued → running → succeeded | failed | cancelled
```

| 字段 | 用途 |
|---|---|
| `idempotency_key` | 防重复创建（同一请求只建一个任务） |
| `attempt` / `max_attempts` | 重试计数与上限 |
| `started_at` / `completed_at` | 实际执行时间 |
| `last_error` | 最近失败原因 |
| `worker_id` | 执行者标识 |
| `lease_expires_at` | 租约超时（防半死任务永久 running） |

**恢复语义**：

- Media Gateway 崩溃后，Core 依据 `attempt` / `lease_expires_at` 判断：任务是否真正执行过？可否安全重试？是否已产生结果？是否需人工处理？
- **生图任务重复执行有真实成本**（GPU 时间 + 可能的 API 费用），必须用 `idempotency_key` + 结果登记双重保护。
- **取消竞争**：`cancelled` 仅表示"不再等待结果"，不代表后端已停止计算。取消后若结果仍到达，标记为 `cancelled_but_completed`，保留产物但通知用户已取消。

---

## 9. 记忆迁移

### 9.1 冻结前置

1. 导出快照（含来源、时间、profile traits）。
2. 记录冻结时间戳并写入审计。

### 9.2 冻结期

- LongTermMemory **只读**。
- 必要变更进入 `memory_pending` 暂存区（标记来源与时间），迁移后按序合并。

### 9.3 迁移

1. 导出 → 去重 → 保留 provenance → 导入 Core `memory`。
2. 抽样比对（见 §10.3 修订后的检索指标）。

### 9.4 记忆证据分级（v1.1 修订，对应 H-01：取消绝对覆盖链）

> **记忆证据分级原则**
>
> 记忆来源分三类，代表**不同证据性质**，不构成对所有记忆类型通用的绝对优先级：
>
> 1. **用户显式陈述**：用户主动表达的事实、偏好、目标、意图与修正。
> 2. **系统观察记录**：系统实际观察到的行为、事件与交互结果。
> 3. **模型归纳结果**：模型基于已有信息生成的总结、推断与假设（派生信息）。
>
> - 对**主观偏好、自我描述与意图**：用户显式陈述优先。
> - 对**客观交互行为**：以实际观察记录为依据。
> - 模型归纳结果**必须保留来源关联**，不得无条件覆盖原始证据。
> - 冲突时：**保留原始记录**，记录冲突状态、适用时间、判断依据与裁决结果。
> - **无法裁决时标记为待确认，不自动覆盖。**

**字段（v1.1 新增）**：

| 字段 | 含义 |
|---|---|
| `confidence` | 置信度（0–1），表达不确定性 |
| `valid_from` | 该记忆开始有效的时间 |
| `valid_until` | 失效时间（可空 = 仍有效） |
| `source_type` | `user_asserted` / `observed` / `model_inferred` |
| `source_ref` | 原始证据引用（消息 ID / 事件 ID） |
| `superseded_by` | 被哪条新记录取代（保留旧记录，不物理删除） |

存储：`memory`（当前有效值）+ `memory_history`（全量变更与冲突记录）。
Core 是唯一写入者与唯一裁决者。

---

## 10. LangBot Adapter 与影子模式

### 10.1 最终形态

LangBot 只做：接收 QQ 事件 → 转成 Core 事件 → 上报 → 收到 Core 回复 → 发送。
不保留人格 prompt、不保留业务记忆、不决定任务规划。

### 10.2 影子模式：无副作用 + 数据/费用边界（v1.1 扩充，对应 H-04）

**硬性禁止**：

| 限制 | 说明 |
|---|---|
| ❌ 不发送消息 | 禁止调用 QQ 发送接口 |
| ❌ 不写正式记忆 | 只能写 `shadow_*` 隔离区 |
| ❌ 不执行有副作用的工具 | 生成类工具只记录"会调用什么" |
| ❌ 不创建真实任务 | 只写影子表 |
| ❌ 不重复上报事件 | 影子事件用独立 `shadow_events` |

**允许**：身份解析、上下文组装、模型推理、结果记录、延迟测量。

**数据与费用边界（v1.1 新增）**：

| 项 | 约束 |
|---|---|
| 独立开关 | `SHADOW_COGNITION=on/off`，与正式路径独立 |
| 费用上限 | 每日调用次数/费用硬上限，超限自动关停影子并告警 |
| 数据隔离 | 影子结果仅入隔离存储，永不进入正式记忆/事件/审计 |
| 保留期 | 影子数据独立保留期（建议 7 天），到期清除 |
| 隐私边界 | 与正式推理相同的数据权限；用户消息再次发送给外部模型须在隐私说明中披露 |
| 上下文范围 | 默认只用当日上下文；读取完整历史须单独开关 |

**工具语义区分（v1.1 新增）**：

- `tool.proposed`：影子记录"模型建议调用什么"
- `tool.executed`：**影子模式永不产生此事件**

### 10.3 切换判据（v1.1 修订，对应 M-01）

两周是**观察窗口**，不是切换依据。指标分"硬门槛"与"观察项"：

| 指标 | 类型 | 判据 | 说明 |
|---|---|---|---|
| 身份解析正确率 | **硬门槛** | **100%**（抽样比对） | 有误即不切换 |
| 事件链完整率 | **硬门槛** | ≥99%，口径：有 `message.received` 的事件中成功产生 `message.sent` 或明确 `*_failed` 的占比 | 排除主动中止 |
| 关键故障恢复 | **硬门槛** | **全部通过**（非 90%） | 见故障清单 |
| 无重复发送 / 无重复任务 | **硬门槛** | 0 次 | 注入故障后验证 |
| 任务状态不丢失 | **硬门槛** | 0 次 | 重启后未完成任务可恢复 |
| P95 延迟 | 观察项 | ≤ 当前路径 + 20% | 超了需说明 |
| 记忆检索质量 | 观察项 + 人工复核 | 关键事实召回率、相关性、来源可追溯性、隐私隔离正确率 | **不以"与旧系统结果一致"为标准** |
| 工具决策 | 观察项 + 人工复核 | 满足任务目标、权限、安全约束；重点看**错误调用/越权调用/漏执行** | **不以"与旧模型决策一致"为标准** |

**关键故障清单（必须全部通过）**：断网、模型超时、工具失败、数据库锁、进程重启、投递失败重试。

---

## 11. 迁移阶段与回滚（v1.1 重写，对应 B-01）

### 11.1 原则修正

> **单开关回滚是"控制路径回滚"，不代表数据自动回滚。**
> 每个阶段必须同时定义：状态兼容、数据保全、回滚后的增量处理。

### 11.2 阶段 × 回滚 × 数据处理

| 阶段 | 控制路径回滚 | 数据处理要求 |
|---|---|---|
| **E1 Events** | 停止新事件写入 | 保留已产生事件，**不直接删表**；如需清理走归档 |
| **E2 Session** | `CORE_SESSION=off`，切回 LangBot transcript | Core 新建的会话/消息必须**导出或可重放**；回滚前确认旧路径可读取 |
| **E3 Memory** | 恢复 LongTermMemory 写入 | Core 新记忆必须**导出/同步/暂存**，不得直接丢弃；重新开启旧写入前完成反向合并 |
| **E4 Cognition** | `CORE_COGNITION=off`，切回 LocalAgent | **正在运行的任务由 Core 继续管理**；不得因模型路径回滚而重复执行；已完成任务状态留在 Core |
| **E5 Adapter** | 恢复旧 Pipeline | 保留 Core 历史状态，明确兼容与双向同步策略 |

### 11.3 状态分叉处理（v1.1 新增）

每个阶段定义一个 **回滚数据处置动作**，三选一，缺一不可：

1. **反向同步**：把 Core 新增数据导回旧系统（E2/E3 用）
2. **冻结保留**：Core 侧数据保留为只读历史，旧系统不再读取（E5 用）
3. **丢弃但审计**：明确丢弃范围，写入审计（仅限可重放数据，如影子数据）

**不允许**：既回滚控制路径、又让两侧同时写入权威状态（这会重新制造双大脑）。

### 11.4 并行约束（已拍板）

| 可以并行 | 不可以并行 |
|---|---|
| E3 记忆导出/清洗 与 E4 Adapter 开发 | E3 与 E4 同时切换权威 |
| 各阶段测试、影子验证 | 上一阶段未稳定就切换下一阶段 |

**正式切换依赖稳定的 Identity / Session（E2 通过验收）。**

---

## 12. 安全前置（阻塞项 · 未验证前不得移除）

> **文档中列出要求 ≠ 运行时已完成整改。以下各项必须逐条实测通过后，才能从阻塞清单移除。**

| 项 | 现状 | 要求 |
|---|---|---|
| NapCat OneBot HTTP :13000 | 真实局域网可达，**token 为空** | 随机 token + 宿主端口只绑 127.0.0.1 |
| NapCat WS :13001 | token 为空 | 同上 |
| NapCat WebUI :6099 | 局域网可达，弱 token | 强随机 token + 只绑本机 |
| Media Gateway :8899 | 无鉴权 | **独立服务凭证**（不复用 Core 主 key） |
| LangBot :5300 | 局域网可达 | 只绑本机 |
| Core :8900 | 已有鉴权（401） | 保持；key 强度复核 |
| JWT secret | 明文在 config.yaml | 环境变量/Secret；轮换会导致重新登录 |
| 凭证分级 | 单一 key | Core 管理 / LangBot 服务 / Console 用户 三级 |

**NapCat 三端口必须一并收口**：HTTP(13000)、WS(13001)、WebUI(6099)。只封一个不等于风险消失。

---

## 13. 决策记录

| # | 决策 | 结论 | 落点 |
|---|---|---|---|
| A | events 表位置 | Core 的 `mobius.db`；Core 唯一写入，LangBot 只上报 | §5 |
| B | LocalAgent 影子期 | 允许双跑，影子路径**无副作用**；切换用指标判据 | §10.2/10.3 |
| C | 记忆迁移期冻结 | 冻结 LongTermMemory 写入；快照 + staging | §9 |
| D | 记忆冲突策略 | 证据分级（非绝对覆盖）+ confidence/valid_from/valid_until；保留原始证据与裁决 | §9.4 |
| E | E2/E3 关系 | 并行准备、串行切换；切换依赖 E2 稳定 | §11.4 |
| F | 事件保留 | 30 天热存；任务事件归档；安全审计长期；正文有独立权威存储 | §5.5 |
| G | 多模型协作 | 不进入 v1 | §15 |
| H | Console 访问 DB | 不允许，只走 API | §15 |
| I | 事件与正文分离 | Event Log / Message Store / Session / Memory 四分离 | §5 |
| J | 回滚定义 | 控制路径回滚 ≠ 数据回滚；每阶段定义数据处置 | §11 |
| K | 群聊记忆隔离 | conversation / actor / user_scope / group_scope 四概念 | §6.2 |
| L | 权限 | 默认拒绝 + 权限矩阵 | §6.3 |

---

## 14. 未决问题

1. 影子期样本量下限（已有 6 项指标，样本量待定）。
2. 归档介质与查询方式（U 盘归档 vs Core 内归档表）。
3. 凭证三级命名与轮换周期。
4. `outbox` 发送器的重试上限与死信处理策略。
5. 群聊记忆的保留期限（隐私敏感，需产品决策）。

---

## 15. 本文档不做的事

- 不重写已有的生图/语音/任务能力。
- 不引入消息队列、微服务拆分、K8s。
- 不改变 QQ 用户侧交互方式。
- 不在 v1 支持多模型协作（同一任务多后端）。

---

## 附录 A：v1.1 修订对照

| 评审编号 | 问题 | v1.1 处理 |
|---|---|---|
| **B-01** | 回滚不解决状态分叉 | §11 重写：控制回滚 vs 数据回滚分离；新增 §11.3 状态分叉处置三选一 |
| **B-02** | 事件/正文/会话存储边界不清 | §5 重写为四类存储分离；§5.1 事件结构含 `content_ref`/`content_hash` |
| **H-01** | 来源三级是绝对覆盖链 | §9.4 改为证据分级原则（取消绝对排序）+ confidence/valid_from/valid_until |
| **H-02** | 群聊缺用户隔离 | §6.2 新增四概念与隔离原则；§6.1 明确 message_type 不参与 ID |
| **H-03** | Contract 不足以保证可替换 | §7.1 禁止 provider_meta 上浮；§7.2 能力扩到 8 项；§7.3 降级三选一；§7.5 契约测试；§1.2 判据修正 |
| **H-04** | 影子成本与数据边界缺失 | §10.2 增加开关/费用上限/隔离/保留期/隐私；新增 tool.proposed vs tool.executed |
| **M-01** | 三项验收指标不合理 | §10.3 改为硬门槛 vs 观察项；检索/工具决策改为人工复核判据；关键故障 100% |
| **M-02** | 任务缺幂等与恢复 | §8 新增 8 个字段 + 恢复语义 + 取消竞争规则 |
| **M-03** | 事件缺去重与可靠投递 | §5.3 事务边界；§5.4 outbox + 幂等键 + 顺序规则 |
| **M-04** | 权限只有角色名 | §6.3 新增权限矩阵（默认拒绝） |
| 细节 1 | console:admin 单一会话 | §6.1 改为 `console:user:<uuid>` |
| 细节 2 | 审计长期保留未脱敏 | §5.5 新增审计脱敏要求 |
| 提醒 | 安全要求 ≠ 已完成整改 | §0 冻结条件 + §12 顶部声明 |
