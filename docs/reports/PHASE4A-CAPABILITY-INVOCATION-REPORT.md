# Phase 4A 实施报告：统一能力调用、权限、Provider 路由、执行记录

- 日期：2026-11-13
- 状态：已完成并热更新；Agent 媒体工具已走统一能力入口
- 关联：[ADR-013](../adr/ADR-013-capability-invocation-boundary.md)
- 上一阶段：[Phase 3B 报告](PHASE3B-MIGRATION-LEGACY-CLOSE-REPORT.md)
- 代码提交：`0d607c3`

## 1. 统一能力层

新增 `core/capabilities.py`：

```text
CapabilityService
  image.generate   -> Registry character/workflow -> Gateway generate
  voice.synthesize -> Registry character/voice    -> Gateway tts
```

职责：

- 解析 published Registry 角色/workflow
- 检查 `media_policy.allow_image/allow_voice`
- 可选 `capabilities.allowed_sources`
- Provider 路由：
  - image → comfyui
  - voice → kokoro / gpt-sovits
- 记录 `capability_invocations`
- 写审计 `capability.invoke`

Agent 的 `_do_image` / `_do_voice` 已改为调用 `self.capabilities`，不再直接
调用 `self.gw.generate/tts`。

## 2. 执行记录

新增表：

```text
capability_invocations
  id, capability, actor, user_id, session_id, source
  character_id, character_revision
  workflow_id, workflow_revision
  provider, task_id, status, error
  created_at, updated_at
```

查询：

```text
GET /api/capabilities
GET /api/capabilities/invocations?limit=50
```

能力目录：

```json
{
  "image.generate": {"provider": "comfyui", "kind": "image"},
  "voice.synthesize": {"provider": "auto", "kind": "voice"}
}
```

## 3. Live 验证

Agent 通过能力层执行：

| 项目 | 结果 |
|---|---|
| image capability | `t_5286153c`，`mobius_registry_00006_.png` |
| voice capability | `t_571ff659`，audio URL |
| execution records | image + voice 各一条 |
| character revision | 1 |
| provider | comfyui / kokoro |
| workflow | 已发布 comic-portrait |

复测记录字段时发现 workflow 记录写成角色别名 `portrait`，已修正为
`registry_runtime.workflow()` 返回的 published id/revision，并将在最终部署中生效。

## 4. 测试

| 项目 | 结果 |
|---|---|
| `test_capabilities.py` | 5/5 |
| `test_registry_migration.py` | 6/6 |
| `test_registry_runtime.py` | 5/5 |
| `test_registry_manage.py` | 7/7 |
| `test_voice_assets.py` | 4/4 |
| `test_registry_lifecycle.py` | 16/16 |
| `test_registry_api.py` | 8/8 |
| `test_qq_webhook.py` | 5/5 |
| `pnpm typecheck` | OK |

## 5. 边界

- Gateway `/api/*` 仍是本地运维接口，物理上仍可被其他本机进程调用；Agent 路径已统一
- 能力层不写 Registry，只读 published revision
- 执行记录不替代 TaskStore 任务状态
- 还没有完整作用域/角色权限表；当前权限主要是 media_policy + source allowlist
- 执行记录没有自动归档/保留策略

## 6. 回滚

- 代码：`core/capabilities.py`、`core/capabilities_api.py`、`core/agent.py`、
  `core/server.py` 有 `.bak-*` 备份
- 行为回滚：把 Agent 调用改回 `self.gw.generate/tts`（不推荐）
- Runtime 仍保持 `LEGACY_READ_DISABLED` + `enforce`

## 7. 停止点

Phase 4A 完成，不继续 Phase 4B（权限表、Provider 策略、记录保留）。
