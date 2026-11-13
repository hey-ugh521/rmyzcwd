# ADR-013: Unified Capability Invocation Boundary

- 状态：Accepted
- 日期：2026-11-13
- 关联：[Registry 架构契约 v2](../aegis/plans/2026-11-13-capability-registry-contract.md)

## 背景

Phase 3B 后 Agent 已经只读 published Registry，但 Agent 仍直接调用
`GatewayClient.generate/tts`。这留下三个问题：

1. 权限检查散落在 Agent 工具代码。
2. 不同调用方可能绕过 Registry 直接调 Gateway。
3. 缺少“这次能力调用用了哪个 revision/provider”的执行记录。

## 决策

Core 内新增统一能力入口 `core/capabilities.py`：

```text
CapabilityService
  image.generate   -> 解析角色/发布 workflow -> Gateway /api/generate
  voice.synthesize -> 解析角色/语音字段     -> Gateway /api/tts
```

职责：

- 解析角色：`registry_runtime.character()`
- 解析 workflow：`registry_runtime.workflow()`
- 权限：角色 published `media_policy.allow_image/allow_voice`
- 来源策略：可选 `capabilities.allowed_sources`
- 执行记录：`capability_invocations` 表
- 审计：`capability.invoke`
- 路由：image → comfyui；voice → kokoro / gpt-sovits

Agent 的工具调用改为 `self.capabilities.image/voice`。

## 边界

- 直接调用 Gateway `/api/*` 仍是运维/Console 的本地接口，不作为 Agent 执行入口。
- 能力执行记录只记录调用事实，不替代 TaskStore 的任务状态。
- 能力层不写 Registry；它只读 published revision。
- `LEGACY_READ_DISABLED` 下缺资源 fail closed。

## 后果

正面：

- 权限/路由/记录只有一个 owner
- Agent 调用可追溯 `character_revision`、`workflow_revision`、provider、task_id
- 后续 Provider 路由和权限策略可以扩展在能力层

代价：

- Gateway 直连接口仍在，不能宣称“物理上不可绕过”；Agent 路径已统一。
- 执行记录表需要清理策略（当前无自动归档）。

## 后续

Phase 4B 可继续：

- capability catalog/参数 schema
- provider 选择策略（可用性/成本/延迟）
- 权限表（actor/role/scope）
- 执行记录保留策略
