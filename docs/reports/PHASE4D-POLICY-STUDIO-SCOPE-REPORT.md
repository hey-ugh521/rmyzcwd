# Phase 4D 实施报告：Studio 策略管理页与 Permission Scope

- 日期：2026-11-13
- 状态：已完成并热更新
- 关联：[ADR-016](../adr/ADR-016-permission-scope.md)
- 上一阶段：[Phase 4C 报告](PHASE4C-PROVIDER-HEALTH-REPORT.md)
- 代码提交：`4d6bf6b`

## 1. Permission Scope 生效

`scope_json` 现在支持：

```json
{
  "characters": ["bronya"],
  "sources": ["qq"],
  "users": ["user-id"]
}
```

语义：

- 出现的约束必须同时满足（AND）
- `"*"` 表示该维度不限制
- 未出现的键不约束
- scope 不匹配 → 该权限行不参与决策（不是 auto-deny）
- 仍按 subject specificity / capability exact / priority / deny-wins 决策

`CapabilityService` 调整为**先解析角色，再做权限判断**，因此 image/voice 都能使用
`characters` scope。

## 2. Studio 策略管理页

新增 `/policy`「策略」页面：

- 权限 tab
  - 权限规则列表
  - 新建/编辑规则
  - subject type / subject / capability / effect / priority / enabled
  - scope characters/sources/users
  - 删除规则
- Provider tab
  - 优先级编辑
  - 启用/禁用
  - 动态健康状态
- 执行记录 tab
  - 最近能力执行
  - 保留策略展示

导航入口：侧栏「策略」。

## 3. Live 验证

Scope 测试：

```text
set: source=scope-live, capability=image.generate, effect=deny, scope.characters=[bronya]
bronya -> allow=false, source=table
vita   -> allow=true,  source=default
```

测试权限已删除。

Studio：

- `/policy` 页面加载成功
- 权限表显示 `default_allow=true`
- Provider 健康状态显示
- 页面可写

## 4. 测试

| 项目 | 结果 |
|---|---|
| `test_capabilities.py` | 11/11 |
| `pnpm typecheck` | OK |
| `pnpm build` | OK |
| `test_registry_migration.py` | 6/6 |
| `test_registry_runtime.py` | 5/5 |
| `test_registry_manage.py` | 7/7 |
| `test_voice_assets.py` | 4/4 |
| `test_registry_lifecycle.py` | 16/16 |
| `test_registry_api.py` | 8/8 |
| `test_qq_webhook.py` | 5/5 |

## 5. 边界

- scope 只支持 characters/sources/users
- 没有时间窗口、配额、IP、session scope
- scope 不参与 Provider 选择
- Studio 策略页没有手动 prune 按钮（只显示维护说明）
- 权限默认 allow
- 生产 Core 仍是直接启动进程，不是 launcher 受控进程

## 6. 回滚

- 代码：`core/capabilities.py`、Studio `PolicyPage.tsx` 等
- 策略回滚：删除 `capability_permissions` 行 → default_allow
- dist 备份：`D:\AI\backups\studio-policy-*`

## 7. 停止点

Phase 4D 完成，不继续 Phase 5。
