# ADR-007：任务状态唯一 owner = Core（Gateway 不自存业务状态）

- 状态：**Accepted & Implemented（设计评审通过 2026-11-10；M1a/b/c 已实现于分支 `m1-task-owner`，见 §10）**
- 日期：2026-11-10
- 决策人：产品经理（D1=B，2026-11-10）
- 关联：`ARCHITECTURE-v1.1.md`、`ADR-002`（状态词汇）、`ADR-003`（PG 迁移）、`ADR-006`（网关鉴权边界）
- 影响：Core、Media Gateway、Console、LangBot MCP 工具、任务/审计查询

## 1. 背景

当前任务状态是双写：

- Gateway：`mobius.task_snapshot` 负责执行状态/幂等/outbox，是实际执行权威。
- Core：`mobius_core.tasks` 通过 outbox 事件投影一份镜像；Console/审计读 Core。
- 两份状态的词汇由 `core/store.py:_GW_STATUS` 做兼容映射（T1），但仍是双事实源。

这导致：

1. 崩溃/重启恢复必须同时猜两个库谁对；
2. 取消、迟到产物、unknown 状态无法形成单一终态；
3. 文档口径冲突（v1.1 说 Core owner，ARCHITECTURE-QUALITY 说 Gateway 唯一持久源）；
4. 用户/Console 看到的状态与执行端可能不一致。

产品决策 D1 已拍板：**Core 是任务业务状态的唯一 owner；Gateway 不自存业务状态。** 本 ADR 定义落地方式。

## 2. 决策

**Core 拥有任务身份、业务状态、终态与查询；Gateway 只保留执行队列/outbox 作为传输与恢复中间态，不再作为业务状态源，不对外提供任务状态。**

具体规则：

1. **任务身份由 Core 分配**。调用方（LangBot/Console/桌面）提交时带 `idempotency_key`；Gateway 转发给 Core `POST /v1/tasks`，Core 创建/幂等命中并返回 `task_id`。Gateway 后续用该 `task_id` 作为 `execution_id` 关联 ComfyUI/执行引擎。
2. **状态只写 Core**。Gateway 通过事件上报执行进展（`POST /v1/tasks/{task_id}/events` 或既有 outbox 消费）；Core 更新唯一任务状态。Gateway 本地只允许保存执行私有的临时信息（如 Comfy prompt_id、重试计数、内部队列位置），不得对外提供状态查询。
3. **查询走 Core**。Gateway 的 `/api/tasks/*` 与 MCP `comfyui_task_status` 改为代理 Core（迁移期），最终可从 Gateway 移除；Console/审计只读 Core。
4. **取消语义**：Core 记录 `cancel_requested`，Gateway 尽力取消执行；只有执行端确认停止后 Core 才进入 `canceled` 终态。若完成先于取消到达，则 `succeeded` + `cancel_requested=true`（用户可见"取消未生效"），不引入第二终态。
5. **恢复语义**：Gateway 重启后向 Core 查询 `running` 任务及其 `execution_id`，探测 ComfyUI 实际状态并上报；Core 对超时租约按策略置 `failed`/`submission_unknown`，禁止永久 `running`。
6. **幂等**：`idempotency_key` 唯一性由 Core 保证（唯一索引 + 冲突读回）；Gateway 不得用本地表作为幂等命中依据。

## 3. 数据模型（Core，目标态）

在 `mobius_core.tasks` 增加/明确：

| 字段 | 用途 |
|---|---|
| `task_id` | Core 生成，全局唯一 |
| `idempotency_key` | 唯一索引；同 key 同 payload 返回原任务，异 payload 冲突 |
| `payload_hash` | 幂等冲突判定 |
| `kind` / `status` | 业务类型/状态（ADR-002） |
| `execution_id` | Gateway/执行引擎的 id（Comfy prompt_id 等） |
| `error_code` | 结构化失败原因 |
| `attempts` | 执行尝试次数 |
| `lease_owner` / `lease_expires_at` | 执行租约，防永久 running |
| `cancel_requested` | 取消请求标记 |
| `version` | 乐观并发/迁移用 |
| `created_at` / `started_at` / `finished_at` / `updated_at` | 时间线 |

Gateway 侧 `task_snapshot` 降级为**执行私有账本**（迁移期保留，最终删除），字段不得作为对外状态来源。需要数据库迁移 runner（ADR-003 的延伸）。

## 4. 迁移步骤（分阶段，可回滚）

### Phase 1：Core 增加权威 API（不改调用方）
- Core 新增 `POST /v1/tasks`（创建/幂等）、`POST /v1/tasks/{id}/events`（执行事件）、`GET /v1/tasks*`（查询）。
- Core 任务表加字段/唯一索引；迁移 runner 记录 `schema_version`。
- Gateway 双写**只写执行账本**，状态事件仍发 Core；旧 `task_snapshot` 只读回填。
- 验收：契约测试覆盖创建/幂等/事件/查询；Core 是唯一可写状态处（静态检查/DB 权限）。

### Phase 2：切换读路径
- Gateway REST/MCP 任务查询代理 Core（标 deprecated）；Console 已读 Core，无需改。
- LangBot 工具调用不变（仍调 Gateway MCP，但底层走 Core）。
- 验收：同一任务在 Gateway/Core/Console 三处查询一致。

### Phase 3：删除双写与执行账本
- Gateway 不再写任何业务状态；删除 `task_snapshot` 业务字段或整表（保留 outbox 传输）。
- 回填/清理历史镜像；更新 `ARCHITECTURE-v1.1.md`、`ARCHITECTURE-QUALITY.md`、`STATUS.md` 为同一口径。
- 回滚：保留一个 release 的双读开关；Core 不可用时 Gateway fail closed（明确错误，不落本地状态）。

## 5. 关键接口契约（草案）

```http
POST /v1/tasks
{
  "kind": "image|speech",
  "idempotency_key": "...",
  "payload": {...},
  "source": "qq|console|desktop",
  "user_id": "...", "session_id": "...", "character": "..."
}
→ 200 {"task_id": "...", "status": "pending", "idempotent_hit": bool}

POST /v1/tasks/{task_id}/events
{
  "event": "running|progress|succeeded|failed|canceled|submission_unknown",
  "execution_id": "...", "error_code": "...", "result": {...},
  "seq": 123
}
→ 200 {"ok": true}
```

约束：事件按 `seq` 幂等；Core 拒绝非法状态转移；Gateway 事件发送必须校验 `ok=true`（T3 已修）。

## 6. 验收标准

| 测试 | 通过条件 |
|---|---|
| 幂等并发 | 同 key 并发提交 → 只有一个任务；异 payload → 409 |
| 崩溃恢复 | 杀 Gateway/Worker → Core 无永久 running；重启后任务可恢复或明确失败 |
| 取消竞争 | 取消与完成竞态 → 单终态；结果不丢 |
| 迟到产物 | 取消后产物到达 → 有明确归属（succeeded+cancel_requested 或结果归档） |
| 双写消除 | grep/DB 权限证明 Gateway 不再写业务状态；查询三处一致 |
| Core 重启 | 任务/审计可恢复；Gateway 重连后继续上报 |

## 7. 被否决的方案

| 方案 | 否决理由 |
|---|---|
| A) Gateway 唯一 owner，Core 只读投影 | 与产品决策 D1 冲突；跨入口/审计仍分裂 |
| 维持双写 + 映射 | 冲突/恢复/取消永远无法单一终态；已产生 T1 兼容债 |
| Gateway 本地 SQLite 作为缓冲权威 | 双事实源换位置，问题不变 |

## 8. 后果与风险

- Core 成为媒体任务关键路径：Core 不可用时 Gateway 必须 fail closed 并给出明确错误，不能静默落本地状态。
- 需要 Core 承担更高写入频率与迁移；用 PG + 唯一索引 + 短事务控制。
- 迁移期需要兼容双读；必须设 kill switch/回滚 release。
- LangBot MCP 工具响应结构需保持兼容（task_id/status 字段），避免用户侧行为变化。

## 9. 已确认参数（PM 2026-11-10）

1. **task_id 由 Core 分配**；调用方只提供 `idempotency_key` 与业务 payload。
2. **租约**：默认 60s，执行器每 30s 续约；过期由 Core 置 `failed`（error_code=`lease_expired`），Gateway 重启后按 execution_id 探测并上报。
3. **submission_unknown**：保留人工确认；超过 24h 未确认自动转 `failed`（error_code=`submission_unknown_timeout`），Console/审计保留原因。
4. **deprecation 窗口**：Gateway 旧任务查询接口代理 Core，保留 1 个 release（约 2 周）后移除。

> 实现任务：`docs/reports/QODER-M1-20261110.md`。

## 10. 实现状态（M1a/b/c，分支 `m1-task-owner`，2026-11-10）

| 阶段 | 落地 | 证据 |
|---|---|---|
| M1a | Core 权威 schema v2（幂等键唯一索引/租约/执行/版本列，sqlite+PG 幂等 ALTER 迁移，schema_meta.schema_version=2）；`/v1/tasks`、`/v1/tasks/{id}/events`、`GET /v1/tasks[/{id}]`、`/v1/tasks/{id}/cancel`；ADR-002 规范词状态机（`normalize_task_status`/`can_transition`，submission_unknown 独立终态、仅 24h→failed） | `core/store.py`、`core/server.py`、`core/migrations/001_task_owner.sql`、`tests/test_m1_task_owner.py`（sqlite 46/46、scratch PG 42/42） |
| M1b | Gateway 提交经 Core 分配 task_id（fail closed）；`_send_core_event` 投权威事件 API；`/api/tasks*` 与 MCP `comfyui_task_status` 代理 Core 并映射回历史 `public()` 结构（标 deprecated）；回滚开关 `MOBIUS_TASK_OWNER=core\|gateway` | `media_gateway/server.py`（`CoreTaskClient`/`task_owner_mode`/`_core_task_to_public`）、`tests/test_m1_gateway_proxy.py`（28/28） |
| M1c | 执行租约续约（网关 30s→Core，TTL 60s）；Core 收敛循环（`_task_convergence_loop`：租约过期→failed(lease_expired)、submission_unknown 24h→failed）；恢复对账（`_reconcile_core_running`：孤儿 running 按 execution_id 探测 ComfyUI，回收迟到产物或转 submission_unknown）；取消竞态单终态 | `core/store.py`（`task_expire_stale_leases`/`task_expire_stale_unknown`）、`core/server.py`、`media_gateway/server.py`、`tests/test_m1_recovery.py`（17/17） |

**双写消除口径**：core 模式下 Core 是任务业务状态**唯一权威**——Gateway 查询一律代理 Core、
执行进展只经事件 API 写 Core；`task_snapshot` 降级为**执行私有账本**（worker 队列/Comfy prompt_id/
重试计数），不再对外提供状态。物理删除 `task_snapshot` 业务列/整表按 §4 Phase 3 在 deprecation
窗口（1 个 release）后进行，避免破坏执行引擎的队列语义；当前保留 outbox 作传输。

**部署顺序（硬性）**：先升级 Core（具备 M1a 的 `/v1/tasks*`）再升级 Gateway（M1b core 模式）；
否则 Gateway 提交 fail closed（503）。**回滚**：`MOBIUS_TASK_OWNER=gateway` 即恢复网关本地权威
（旧 `/v1/tasks/events` 投影路径保留），无需回退代码。

**未决/后续**：① `task_snapshot` 业务列物理清理（Phase 3，窗口后）；② Console/审计读取口径统一到
规范词（当前经 `normalize_task_status` 读时归一，历史行仍存旧词）；③ 跨进程端到端（真 Core+真网关+
ComfyUI）由 Lead 隔离验收执行（本仓测试受 gateway/Core 分属不同 venv 限制，采用两端对同一
`/v1/tasks` 契约的隔离验证 + 单进程 store↔client 迷你集成）。
