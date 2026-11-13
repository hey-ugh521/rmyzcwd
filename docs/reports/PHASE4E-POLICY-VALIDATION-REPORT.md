# Phase 4E 验证报告：策略 P0/P1 验收

- 日期：2026-11-13
- 状态：P0/P1 已验证并修复
- 关联：[ADR-016](../adr/ADR-016-permission-scope.md)、[Phase 4D 报告](PHASE4D-POLICY-STUDIO-SCOPE-REPORT.md)
- 代码提交：`ef259dc`

## 1. P0 权限冲突

验证重点：specificity、priority、deny-wins。

实现调整：

- 决策顺序改为：
  1. subject specificity：`user > actor > source > *`
  2. capability exact vs `*`
  3. priority
  4. 同分 deny-wins
- priority 不再覆盖更高 specificity

测试：

- `source:*` allow vs `source:qq` deny → deny
- `source` priority 999 allow vs `user` deny → user deny 生效
- 多规则匹配仍取最高 rank

## 2. P0 默认授权

验证：

| 场景 | 结果 |
|---|---|
| 空表 | default_allow=true → allow |
| 无匹配规则 | default |
| 禁用规则 | 被忽略，回到 default |
| `default_allow=false` | 无匹配 → deny |
| scope 不匹配 | 该行不参与决策，不是 auto-deny |

测试覆盖：`test_default_and_disabled_permission`、
`test_permission_scope_characters`。

## 3. P0 管理接口安全

验证：

| 请求 | 结果 |
|---|---|
| 未认证 `POST /api/capabilities/permissions` | 401 |
| Bearer API key | 200 |
| Studio 本机 cookie | 允许（local-session） |
| 审计 actor | `api-key` / `local-session`，不接受 body 伪造 |

结论：Core 目前没有普通用户账号模型；`API_KEY` / 本机 Studio cookie 就是管理边界。
未认证用户不能修改策略。持有管理 key 的调用视为管理员。
如果未来需要普通用户与管理员分离，需要独立账号/角色体系。

测试：`test_policy_unauthenticated_mutation_rejected`、
`test_policy_bearer_allowed_and_audited`。

## 4. P1 并发修改

实现：

- `capability_permissions.version`
- `capability_providers.version`
- `POST /permissions`、`POST /providers` 接受 `expected_version`
- `DELETE /permissions/{id}?expected_version=...`
- SQLite 使用 `BEGIN IMMEDIATE`，PG 使用行级锁
- 冲突返回 `409 permission_conflict` / `provider_conflict`

测试：

- 两个线程同时基于 version=1 修改同一规则
- 恰好一个成功（version=2），一个 `permission_conflict`
- 陈旧 version 串行更新 → 409

Live：

```text
create version=1
update expected=1 -> version=2
update expected=1 -> 409 permission_conflict
```

## 5. P1 审计完整性

每次权限/Provider 修改写 `audit`：

```text
actor
action: capability.permission.set/delete, capability.provider.set
detail.before
detail.after
detail.requested
result: ok/fail
error
ts
```

Live 审计样例：

```text
api-key capability.permission.set ok before=None after=allow v1
api-key capability.permission.set ok before=allow v1 after=deny v2
api-key capability.permission.set fail before=deny v2
api-key capability.permission.delete ok before=deny v2 after=None
```

测试：`test_permission_version_conflict_and_audit` 校验 actor、result、before/after/version。

## 6. Studio 配合

- 权限行返回 `version`
- 编辑提交带 `expected_version`
- 删除带 `expected_version`
- Provider 编辑带 `expected_version`
- 冲突时 UI 收到 409，提示刷新后重试

## 7. 测试

| 项目 | 结果 |
|---|---|
| `test_capabilities.py` | 15/15 |
| `test_registry_core_auth.py` | 8/8 |
| `pnpm typecheck` | OK |
| `pnpm build` | OK |
| 生产 stale version | 409 |
| 生产未认证 mutation | 401 |

## 8. 当前限制

- 没有独立的普通用户/管理员账号模型
- 审计列表没有 Studio 页面
- Provider 并发冲突有 version，但本次重点测试权限规则
- 策略默认 allow
- Core 仍是直接启动进程

## 9. 结论

P0/P1 验收项通过：

- specificity / priority / deny-wins：符合
- 默认授权：符合
- 管理接口：未认证拒绝，管理员可写
- 并发修改：单赢家 + 409
- 审计：前后状态/操作者/结果完整
