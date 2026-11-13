# Phase 4F-2B-2 报告：Shadow 观察器与 RoutePolicyEvaluator

- 日期：2026-11-13
- 状态：2B-2 完成；未接中间件、未改变认证/放行
- 设计：[4F-2B Shadow 设计稿](../aegis/plans/2026-11-13-phase4f2b-shadow-design.md)
- 计划：[4F-2B 实施计划](../aegis/plans/2026-11-13-phase4f2b-implementation-plan.md)

## 1. 范围

- `resolve_route()`：管理/策略/Registry 路由上下文
- `route_decision()`：v1 角色矩阵
- `inspect_legacy_api_key()`：只读身份解析，不创建、不复活
- `evaluate_shadow()`：纯观察计算，不写库、不改请求
- `policy_version()`：canonical policy snapshot hash

## 2. 覆盖范围

实际覆盖：

- `/api/registry/*`
- `/api/capabilities/permissions*`
- `/api/capabilities/providers*`
- `/api/capabilities/provider-health*`
- `/api/capabilities/maintenance*`
- `/api/policy*`

明确不覆盖：

- `/api/capabilities/invocations*`
- `/v1/*`
- 其他 `/api/*`

后两类返回 `not_evaluated`。

## 3. 路由策略 v1

| role | read | write |
|---|---|---|
| admin | allow | allow |
| operator | allow | deny |
| service | deny | deny |

默认拒绝。该矩阵仅用于 Shadow 观察，不接真实授权。

## 4. 身份解析

`inspect_legacy_api_key()`：

- 只读取 configured key 对应的 4F-2A mapping
- 状态：`resolved` / `unmapped` / `mapping_missing` / `revoked` /
  `expired` / `principal_disabled` / `conflict`
- 不创建 Principal/Credential
- 不调用 `resolve_legacy_api_key()` 的写入路径

## 5. 三态与 legacy 观测

`evaluate_shadow()`：

- 路由不覆盖 → `not_evaluated`
- 覆盖但缺少 legacy 观测/身份 → `insufficient_context`
- 身份 resolved → `evaluated`，`policy_result=allow|deny`
- Legacy denied → `identity_state=legacy_denied`，不映射身份
- Legacy anonymous → `legacy_anonymous`，不返回 allow
- 能力调用 → `not_evaluated`，不调用 `decide_permission()`

## 6. policy_version

```text
sha256(canonical_json({
  route_policy,
  capability_policy: {permissions, default_allow, subject_weights, scope_rule_version},
  policy_schema_version,
  evaluator_versions: {route, capability}
}))[:16]
```

- stable key ordering
- permissions 按 id 排序
- 不包含请求上下文/时间/随机值
- 修改 permission 行会改变版本

## 7. 测试

`tests/test_identity_shadow_eval.py`：9/9

覆盖：

- route 覆盖/不覆盖
- 角色矩阵
- resolved service 管理路由 deny
- unknown bearer 不写身份
- legacy denied 不映射
- legacy anonymous 不 allow
- capability 路径 not_evaluated 且不调用 `decide_permission()`
- policy_version 稳定且随权限输入变化
- 缺少 legacy 观测 → insufficient_context

完整入口：

```text
Ran 110 tests ... OK
webhook 5/5
```

## 8. 当前未做

- Legacy auth 只读 `legacy_auth` 状态
- Shadow 中间件
- Shadow Event 写入接线
- 生产部署

## 9. 停止点

2B-2 到此停止。等待验收后进入 2B-3。
