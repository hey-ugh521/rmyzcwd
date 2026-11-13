# ADR-014: Capability Permissions, Provider Policy and Record Retention

- 状态：Accepted
- 日期：2026-11-13
- 关联：[ADR-013](ADR-013-capability-invocation-boundary.md)

## 背景

Phase 4A 的统一能力层只有硬编码的 source allowlist、固定 provider 和无界执行记录。
需要在不改变 Agent 调用的前提下，加入可管理策略。

## 决策

### 权限表

新增 `capability_permissions`：

```text
subject_type: source | actor | user | *
subject: qq / console / user-id / *
capability: image.generate / voice.synthesize / *
effect: allow | deny
priority: integer
enabled: 0/1
scope_json: 预留
```

决策规则：

1. 只匹配启用的行。
2. 具体度：`user > actor > source > *`；精确 capability 优于 `*`。
3. 同分时 deny 优先。
4. 无匹配时使用 `capabilities.default_allow`，默认 `true`，保持兼容。

### Provider 策略

新增 `capability_providers`：

```text
image.generate / comfyui      priority 10
voice.synthesize / kokoro     priority 20
voice.synthesize / gpt-sovits priority 30
```

- 请求指定 provider/engine 时优先精确匹配。
- 否则选已启用、priority 最小的 provider。
- 当前只路由到现有 Gateway endpoint，不新增 provider 实现。
- `constraints_json` 是预留字段，暂不参与决策。

### 执行记录保留

- 配置：`capabilities.retention_days`（默认 30）、`max_records`（默认 10000）
- Core 启动时执行一次 `prune_invocations()`
- 管理接口可手动触发
- 删除是按 `created_at` 的硬删除，不归档

## API

```text
GET    /api/capabilities/permissions
POST   /api/capabilities/permissions
DELETE /api/capabilities/permissions/{id}
GET    /api/capabilities/providers
POST   /api/capabilities/providers
GET    /api/capabilities/retention
POST   /api/capabilities/maintenance/prune
```

## 后果

正面：

- 权限可配置，不再只依赖角色 media_policy
- Provider 顺序可调，可临时禁用
- 执行记录有明确保留策略

代价：

- `scope_json` 尚未参与决策
- Provider 可用性不是动态健康检查，只按 enabled/priority
- 权限默认 allow，未来如果要默认 deny 需要显式配置
- 记录硬删除，不可恢复
