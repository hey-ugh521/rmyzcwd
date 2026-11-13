# ADR-016: Permission Scope Semantics

- 状态：Accepted
- 日期：2026-11-13
- 关联：[ADR-014](ADR-014-capability-policy-retention.md)

## 背景

Phase 4B 的 `capability_permissions.scope_json` 是预留字段。只按
subject/capability 匹配时，无法表达“仅限制某角色”或“仅限制某来源”。

## 决策

`scope_json` 支持三个可选约束：

```json
{
  "characters": ["bronya"],
  "sources": ["qq"],
  "users": ["user-id"]
}
```

语义：

- 所有出现的约束必须同时满足（AND）
- 数组包含 `"*"` 表示该维度不限制
- 未出现的键不参与约束
- 空 scope `{}` 等同于无 scope
- scope 不匹配时，该权限行不参与决策；不会因为“不匹配”产生 auto-deny
- 多条匹配仍按 ADR-014 的 specificity/priority/deny-wins 规则

`CapabilityService` 在解析角色之后调用权限判断，因此 image/voice 都能使用
`characters` scope；source/user 来自调用上下文。

## API/UI

- `POST /api/capabilities/permissions` 接受 `scope` object
- Studio `/policy` 页面提供 `characters / sources / users` 三个 scope 输入
- 权限表显示 scope 通过 JSON 存储，UI 以逗号分隔编辑

## 后果

正面：

- 可以按角色/来源/用户做细粒度限制
- 不匹配的规则不误伤其他调用
- 保持 default_allow 与 deny-wins

限制：

- 没有时间窗口、配额、IP、session scope
- scope 只用于权限行匹配，不用于 Provider 选择
- scope JSON 格式错误时按空 scope 处理（宽松），不阻塞调用
