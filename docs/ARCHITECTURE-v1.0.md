# Möbius Core Unification Architecture v1.0（草案）

> 状态：**草案 · 待评审**
> 起草：2026-10-30
> 本文件只定义**控制权与数据所有权**，不重新实现任何已有功能。
> 已有可稳定运行的 QQ 收发 / 模型调用 / 生图 / 任务能力是资产，不是待重写的对象。

---

## 0. 本文档要解决的问题

当前系统"能用"，但存在**双大脑**：QQ 消息的认知路径（人格、会话、记忆、编排）由 LangBot 决定，
而我们已经有独立的 Möbius Core 承担媒体调度、审计与部分任务。

这不是"功能缺失"，而是**权威分裂**。本文档定义如何把权威收敛到 Core，同时保证：

- 每一步都可回滚；
- 每一步都有可观测的验收判据（不是"跑通"）；
- 不引入新的重型基础设施（不引入 Kafka/RabbitMQ）。

---

## 1. 架构原则（最高约束）

以下三条为**硬约束**，后续所有设计评审以此为准。与功能需求冲突时，以本节为准。

### 1.1 单一事实来源原则（Single Source of Truth）

> 每类业务状态只能存在一个权威写入者。其他服务可以缓存、读取、转发或投影该状态，
> **不得形成独立的权威副本**。

| 状态 | 权威写入者 | 其他服务允许的行为 |
|---|---|---|
| Identity（人格、角色、权限） | **Core** | 读取、缓存、投影 |
| Session（会话、上下文） | **Core** | 读取、投影 |
| Memory（情景、语义） | **Core** | 读取、投影 |
| Task（生命周期、队列） | **Core** | 读取、投影 |
| Audit / Events（事件、操作、执行记录） | **Core** | **只允许上报**，不得自行修改事件状态 |
| Media（生图、TTS） | Core 负责任务编排；ComfyUI 等后端负责实际执行 | 后端只执行，不拥有任务状态 |
| QQ 消息 | LangBot 负责传输；Core 负责业务处理 | LangBot 不得独立决定业务语义 |
| 模型推理 | Provider Adapter 负责适配；Core 负责决策 | Adapter 不得内嵌业务规则 |

**判据**：任何服务若能在 Core 不知情的情况下改变上表左列某类状态，即视为架构违规。

### 1.2 模型可替换原则

> 人格属于 Core，不属于模型；记忆属于 Core，不属于模型；会话状态属于 Core，不属于模型；
> 任务规划属于 Core，不属于模型。DeepSeek 只是当前选用的推理后端。

"可替换"必须可检验，否则是口号：

1. `core/` 目录内 **grep 不到任何厂商专有字段名**（如 `reasoning_effort`、`chat_template_kwargs`）。
2. 更换模型只改**配置 + 新增 adapter**，不改 Core 业务逻辑。
3. 去掉 DeepSeek 后 Core 仍能启动，并对依赖推理的请求返回明确的降级错误。

### 1.3 四不原则（能力归属 ≠ 执行位置）

- Core 拥有任务状态，**不代表** Core 必须亲自执行每个任务。
- Core 拥有人格，**不代表**人格必须由一个巨大的 System Prompt 表达。
- Core 拥有记忆，**不代表**每轮都要把所有记忆塞进上下文。
- Core 拥有决策权，**不代表**每个任务都必须使用同一个模型。

---

## 2. 现状与目标

### 2.1 当前权威位置（迁移前）

| 能力 | 当前权威位置 |
|---|---|
| 人格 | LangBot Pipeline（prompt 内嵌） |
| 用户记忆 | LongTermMemory 插件 |
| 对话上下文 | LangBot transcript |
| 任务状态 | Media Gateway 内存 / Core（分裂） |
| 媒体调度 | Media Gateway |
| 审计 | Core |
| 事件 | 不存在统一事件 |

### 2.2 目标权威位置

全部收敛到 Core（见 1.1 表）。LangBot 最终只保留：QQ 收发、消息格式转换、平台事件处理。

---

## 3. 职责边界（六个服务）

| 服务 | 目标职责 | 明确不做 |
|---|---|---|
| **Möbius Core** | 控制平面 + 状态权威：Identity / Session / Memory / Cognition / TaskEngine / Audit / Events / Media 编排 | 不做 QQ 协议、不直接跑 ComfyUI 工作流 |
| **Media Gateway** | 协议面适配：MCP 工具暴露、ComfyUI/TTS 代理、执行细节 | **不拥有任务状态**（任务状态交由 Core） |
| **LangBot** | QQ 消息适配器：收发、格式转换、平台事件 | 不做人格、不做记忆、不做任务规划 |
| **NapCat** | QQ 协议端 | 不解释业务 |
| **ComfyUI** | 图像生成执行引擎 | 不感知任务语义 |
| **Open WebUI Pipe / Console** | 入口层 | 不做业务决策 |

---

## 4. Core 模块划分

```
core/
├── identity/     人格、角色、权限
├── session/      会话解析、上下文组装
├── memory/       情景记忆、语义记忆、检索、遗忘
├── cognition/    模型调用、决策、工具编排（调用 adapter）
├── task/         生命周期状态机、队列
├── audit/        操作与执行记录
├── events/       事件写入、事件链查询
├── media/        媒体编排（生图/TTS 的调度与结果登记）
└── adapters/     
    ├── model/    推理后端适配（deepseek 等）
    └── ingress/  入口适配（QQ via LangBot、WebUI、Console）
```

**约束**：`cognition/` 不直接调用厂商 SDK；`adapters/model/` 不含业务规则。

---

## 5. 统一事件模型

### 5.1 事件结构

```json
{
  "event_id": "uuid",
  "event_type": "message.received",
  "source": {
    "platform": "qq",
    "channel_id": "group_id | private_user_id",
    "user_id": "user_id"
  },
  "conversation_id": "uuid",
  "payload": { "content": "帮我生成一张图片" },
  "timestamp": "ISO-8601",
  "trace_id": "uuid"
}
```

### 5.2 事件链（一次完整处理）

```
message.received
  → conversation.resolved
  → context.assembled
  → llm.requested
  → tool.invoked
  → task.created
  → task.running
  → task.completed
  → message.sent
```

### 5.3 存储决策（决策 A）

- 事件表放在 **Core 的 `mobius.db`**，表名 `events`。
- **Core 是唯一事件写入权威**；LangBot 只能通过上报接口提交事件，不能改写状态。
- 规模不需要消息队列：SQLite 单表 + `trace_id` 索引足够当前量级。
- 保留期与归档策略见 §11。

---

## 6. 会话与身份模型

### 6.1 会话解析规则（Core 负责）

输入：`platform + channel_id + user_id + message_type`

| 场景 | conversation_id 生成 |
|---|---|
| QQ 群聊 | `qq:group:<group_id>` |
| QQ 私聊 | `qq:private:<user_id>` |
| Open WebUI | `web:user:<user_id>` |
| Console | `console:admin` |

**约束**：`conversation_id` 一旦生成即稳定，不随重启变化。

### 6.2 身份模型

- **人格（Identity）** 属于 Core：角色卡（12 张）为唯一来源。
- 平台身份（QQ 号、Web 用户）→ Core 身份 的映射由 Core 持有。
- 权限模型：`owner / admin / user / guest`（当前仅实现 owner，其余预留）。

---

## 7. Model Backend Contract（决策核心）

### 7.1 统一请求/响应

```python
ModelRequest(
    messages,          # Core 统一消息格式
    tools,             # Core 描述的工具；adapter 负责转换
    reasoning="off|low|medium|high",   # 归一化，不是厂商值
    max_tokens,
    temperature,
    stop,
)
ModelResponse(
    text, tool_calls, usage, finish_reason, raw
)
```

### 7.2 能力协商

- Core 声明需求：`needs: ["text", "tool_call", "vision"]`
- Adapter 声明能力：`supports: ["text", "tool_call"]`
- 不满足时：Core 决定「降级（去掉 vision 需求）」或「换后端」，**不由 adapter 自行决定**。

### 7.3 反面教材（必须避免）

2026-10-30 事故：LangBot 把配置里的 `provider_default` 原样作为 `reasoning_effort`
发给 DeepSeek，后者返回 422，导致该会话所有消息失败。
**根因**：厂商值穿透到了控制层。新架构中该映射只能存在于 `adapters/model/deepseek.py`。

---

## 8. 任务统一生命周期

```
created → queued → running → succeeded | failed | cancelled
```

- 所有入口产生的任务（QQ 生图、WebUI 生图、TTS、异步任务）**统一登记到 Core**。
- Media Gateway 只回报执行进度，不持有权威状态。
- Console 任务中心展示的是 Core 的任务，而非某个网关的内存数据。

---

## 9. 记忆迁移方案（决策 C）

### 9.1 冻结前

1. **导出快照**：LongTermMemory 全量导出（含来源、时间、profile traits）。
2. **记录冻结时间戳**，写入审计。

### 9.2 冻结期间

- LongTermMemory **只读**，不再作为新记忆的权威写入端。
- 迁移期间产生的必要记忆变更，进入 **暂存区（staging）**：
  `memory_pending` 表，标记来源与时间，迁移完成后按序合并。

### 9.3 迁移

1. 导出 → 去重 → 保留 provenance → 导入 Core `memory` 表。
2. 抽样比对：随机 30 条，Core 检索结果与原系统一致。
3. 冻结期结束后，LongTermMemory 降级为只读历史源。

---

## 10. LangBot Adapter 契约

### 10.1 最终形态

LangBot 只做：接收 QQ 事件 → 转成 Core 事件 → 上报 Core → 收到 Core 回复 → 发送到 QQ。
不保留人格 prompt、不保留业务记忆、不决定任务规划。

### 10.2 迁移中间态：影子模式（决策 B）

**影子期必须无副作用**，否则会重复发送、重复扣费、重复建任务：

| 限制 | 说明 |
|---|---|
| ❌ 不发送消息 | 影子路径禁止调用 QQ 发送接口 |
| ❌ 不写正式记忆 | 只能写影子隔离区（`shadow_*`），验证后丢弃 |
| ❌ 不执行有副作用的工具 | 只允许只读工具（列表、状态查询）；生成类工具仅记录"会调用什么" |
| ❌ 不创建真实任务 | 任务只写影子表，不进入 Core 正式 task 表 |
| ✅ 允许 | 身份解析、上下文组装、模型推理、结果记录、延迟测量 |

### 10.3 切换判据（不用时间，用指标）

两周只是**观察窗口**，不是切换依据。建议指标：

| 指标 | 目标 |
|---|---|
| 事件链完整率 | ≥ 99%（每次有 message.received 就有 message.sent 或明确失败事件） |
| 身份解析正确率 | 100%（抽样比对） |
| 记忆检索一致性 | 与原系统抽样一致率 ≥ 95% |
| 工具决策一致率 | ≥ 95%（记录差异并人工复核） |
| 异常恢复 | 注入故障（断网/超时/工具失败）后自愈成功率 ≥ 90% |
| P95 延迟 | 不高于当前路径 + 20% |

未达标不切换，继续修。

---

## 11. 数据库迁移与回滚

| 阶段 | 变更 | 回滚 |
|---|---|---|
| E1 | Core 新增 `events` 表 | 删表（不影响既有功能） |
| E2 | Core 接管身份/会话解析 | 开关 `CORE_SESSION=off`，切回 LangBot |
| E3 | Core 接管记忆（LongTermMemory 只读） | 恢复 LongTermMemory 写入权限 |
| E4 | Core 接管模型编排 | 开关 `CORE_COGNITION=off`，切回 LocalAgent |
| E5 | LangBot 退化为适配器 | 保留旧 pipeline，配置切回 |

**原则**：每个阶段都必须能**只改一个开关**回到上一阶段；不允许"改一半"的状态上线。

---

## 12. 安全前置条件（阻塞项）

以下问题在合并架构前必须完成（详见安全评估）：

| 项 | 现状 | 要求 |
|---|---|---|
| NapCat OneBot HTTP :13000 | 真实局域网可达，**token 为空** | 随机 token + 宿主端口只绑 127.0.0.1 |
| NapCat WS :13001 | token 为空 | 同上 |
| NapCat WebUI :6099 | 局域网可达 | 强随机 token + 只绑本机 |
| Media Gateway :8899 | 无鉴权 | 独立服务凭证（不复用 Core 主 key） |
| LangBot :5300 | 局域网可达 | 只绑本机 |
| JWT secret | 明文在 config.yaml | 环境变量/Secret；轮换会导致重新登录 |
| 凭证分级 | 单一 key | Core 管理 / LangBot 服务 / Console 用户 三级 |

---

## 13. 未决问题

1. 事件保留期与归档阈值（建议 30 天热数据 + 归档）。
2. 影子期长度与样本量下限。
3. 记忆合并时冲突策略（以 Core 为准 or 以时间为准）。
4. 多模型协作（同一任务多后端）是否进入 v1 范围——建议**不进**。
5. Console 是否直接读写 Core 数据库——**不允许**，只走 API。

---

## 14. 本文档不做的事

- 不重写已有的生图/语音/任务能力。
- 不引入消息队列、微服务拆分、K8s。
- 不改变 QQ 用户侧的交互方式。
