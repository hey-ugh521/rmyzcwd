# Phase 4F-2B 设计审查稿：Identity Middleware Shadow

- 状态：**Gate 0 有条件通过；契约已按审查意见修订，待确认后编码**
- 日期：2026-11-13
- 前置：[4F-1 数据模型](2026-11-13-phase4f-identity-authorization.md)、[4F-2A Legacy Mapping](../reports/PHASE4F2A-LEGACY-MAPPING-REPORT.md)
- 范围：只设计双轨认证的 Shadow 观察层；不切换认证，不改变放行结果

## 1. 目标

4F-2B 只回答：

> 如果新身份系统现在接管，它会把请求认成谁、按什么权限决策、与旧认证是否一致？

本阶段不回答：

- 是否真的放行
- 是否返回 401/403
- 是否设置新 Cookie/Session
- 是否启用 Default Deny

## 2. 双轨执行模型

```text
Request
  │
  ├─ Legacy Authentication（唯一放行依据）
  │    - 现有 API_KEY / Studio cookie
  │    - 保持现有 401/403、actor、响应
  │
  ├─ Identity Shadow Evaluation（只观察）
  │    - 解析 Principal / Credential / Role
  │    - 运行完整权限决策
  │    - 记录结果，不授权
  │
  └─ Handler
       - 实际结果只由 Legacy Authentication 决定
```

硬约束：

- Shadow allow ≠ 真实授权成功
- Shadow deny ≠ 阻断当前请求
- Legacy 结果与 Shadow 结果必须在数据结构中分开
- Shadow 异常不得影响 Handler

## 3. 建议的中间件位置

```text
Legacy auth middleware (outer, unchanged)
    -> Shadow observer middleware (inner, non-short-circuiting)
        -> route handler
```

原因：

- 只有 Legacy 认证完成后才能拿到 `legacy_authenticated` 与旧 actor
- Shadow 位于 Legacy 之后，不能提前拒绝请求
- Legacy 拒绝时是否进入 Shadow 由设计决定；建议记录 `legacy_denied` 但不做身份写入

实现时需要通过中间件顺序测试证明：

- Legacy 401 仍 401
- Legacy 200 仍 200
- Shadow 抛异常时响应 body / status / headers 不变

## 4. 身份解析

4F-2B 只观察：

- `Authorization: Bearer <legacy_api_key>` 的旧 API Key 路径
- 不解析 Studio cookie（Admin Session 属于 4F-3）

解析步骤：

```text
bearer token
  -> 是否等于 configured legacy key
  -> 查找 4F-2A 已存在的 service-legacy mapping
  -> 检查 Credential 状态（active/revoked/expired）
  -> 检查 Principal enabled/role
  -> 得到 principal_id / credential_id / role
```

关键规则：

- **未知 key**：记录 `unmapped`，不创建 Principal/Credential
- **没有预映射**：记录 `unmapped`，Shadow 不创建身份
- **已撤销 Credential**：记录 `revoked`，不重新映射、不复活
- **已过期 Credential**：记录 `expired`
- **Principal disabled**：记录 `principal_disabled`
- **service-legacy 缺失**：`unmapped`，不自动 bootstrap

4F-2B 不应在请求路径中做身份写入。4F-2A 的映射必须由显式 CLI/部署步骤完成。

## 5. 权限域边界

**Route Authorization 与 Capability Permission 不是同一个权限域。**

- 管理路由：`/api/capabilities/permissions`、`/api/capabilities/providers`、
  `/api/registry/*`、`/api/policy/*`
- 能力调用：`/v1/...`、`/api/capabilities/invocations`

主体、资源、动作和上下文不同。不能为了让管理路由复用
`capabilities.decide_permission()`，把管理路由伪装成能力调用。

```text
Identity Shadow Observer
  -> Route Context Resolver
       -> Authorization Context
            +-- Route Policy（管理路由）
            |     -> RoutePolicyEvaluator
            +-- Capability Policy（能力调用）
                  -> capabilities.decide_permission()
            -> Shadow Event
```

### 5.1 Route Context Resolver

只生成上下文，不做授权：

```text
route_id
route_action
resource_type
resource_id
principal_id / role
source
user_id / character_id（可解析时）
```

解析不出来时返回 `insufficient_context`，不猜测。

### 5.2 RoutePolicyEvaluator

管理路由第一版使用**显式的 route policy**：

- 角色矩阵：admin / operator / service
- route action：read / write / manage
- resource type：registry / policy / provider / capability
- 默认拒绝未定义 route action

它不复用 capability permission 表，也不把 route action 写成 capability 名。

### 5.3 Capability Policy

只有确认是能力调用路由时，才调用：

```text
decide_permission(
  capability=<capability id>,
  actor=<legacy actor or principal id>,
  source=<request source>,
  user_id=<if available>,
  character_id=<if available>,
)
```

保留其完整语义：specificity、exact capability、priority、deny-wins、scope。

### 5.4 语义适配条件

如果未来证明 `decide_permission()` 可以覆盖管理路由，必须先有：

1. 明确的 Route Action -> Capability 映射契约；
2. semantic conformance 测试，证明同一输入决策一致；
3. 设计稿更新，不得在实现里隐式替代 RoutePolicyEvaluator。

在此之前，管理路由只走 RoutePolicyEvaluator。

## 5.5 三态语义

必须区分：

| 状态 | 含义 |
|---|---|
| `evaluated` | 覆盖范围内，上下文足够，已完成策略计算 |
| `not_evaluated` | 路由不在 Shadow 覆盖范围 |
| `insufficient_context` | 路由在覆盖范围，但缺少必要决策信息 |
| `error` | 观察器/策略器内部异常 |

`not_evaluated` 与 `insufficient_context` 不得合并；前者是覆盖缺口，后者是上下文缺口。

## 5.6 Legacy 拒绝

```text
Legacy Authentication
  +-- accepted -> Identity Mapping -> Shadow Evaluation
  +-- denied   -> legacy_denied -> 不创建、不映射身份
```

Legacy 拒绝时：

- 不调用 4F-2A mapping
- 不创建 Principal/Credential
- 只记录 `legacy_denied`
- Shadow 不能把未知 Key 注册成身份

## 5.7 policy_version

`policy_version` 必须覆盖所有影响决策的策略输入：

```text
policy_version = SHA256(
  canonical_policy_snapshot
  + policy_schema_version
  + evaluator_version
)[:16]
```

`canonical_policy_snapshot` 至少包含：

- route policy 规则（角色矩阵、route action、默认拒绝）
- capability permissions 全量规则
- `default_allow`
- subject weights / deny-wins / specificity 规则版本
- scope 解释规则版本

`evaluator_version`：

- `route-policy-v1`
- `capability-policy-v1`

不包含：请求上下文、时间戳、随机值、用户输入。

历史 Shadow 事件因此可解释：同一 policy_version 必须对应同一决策语义。

## 5.8 保留与清理

- 保留 30 天或 10000 条，以事件创建时间为准。
- 清理按批次上限（建议 500 条/批），避免阻塞请求。
- 清理失败不影响认证/请求处理。
- 不删除其他审计表数据。
- Shadow Event 与安全审计表分离。
- 不保存 Authorization Header、原始 API Key、完整 Token 哈希。

## 6. Shadow 记录

建议新增：

```text
identity_shadow_events
  id
  ts
  request_id
  method
  path
  route_id
  route_action
  resource_type
  resource_id
  legacy_authenticated
  legacy_actor
  identity_state
  evaluation_state
  policy_domain          route | capability | none
  principal_id
  credential_id
  role
  action
  policy_result
  policy_version
  reason
  error_class
  latency_ms
```

`identity_state`（身份解析结果）：

```text
unmapped
mapping_missing
revoked
expired
principal_disabled
resolved
legacy_denied
```

`evaluation_state`（是否完成策略计算）：

```text
evaluated
not_evaluated
insufficient_context
error
```

`policy_result`：

```text
allow
deny
not_evaluated
insufficient_context
error

```

`legacy_authenticated` 与 `policy_result` 必须分开存储，禁止合并成一个 `allowed`。

## 7. 六个设计问题

### 7.1 新身份系统异常是否影响旧认证？

不影响。

- Shadow 整体 `try/except`
- 任何异常只写 `error_class` + `policy_result=error`
- 不修改 response.status / headers / body
- 不重抛到 Legacy middleware

### 7.2 未知 Key 是否记录 unmapped，绝不创建身份？

是。

- 只比较 configured key
- 不在 Shadow 路径调用任何 `create_principal/credential`
- 映射缺失也记录 `mapping_missing`，不自动创建

### 7.3 已撤销 Credential 是否记录 revoked，不重新映射？

是。

- 先查 mapping，再检查 `revoked_at` / `expires_at` / Principal.enabled
- 不重新生成 Credential
- 不修改旧状态

### 7.4 Shadow 是否复用完整权限决策算法？

分权限域，不强行复用不匹配的抽象：

- 管理路由：`RoutePolicyEvaluator`，显式 route action / role / resource 规则
- 能力调用：直接调用 `capabilities.decide_permission()`
- 只有通过语义适配契约后，管理路由才允许复用 capability 决策
- 若 scope/context 不足，记录 `insufficient_context`，不返回 allow
- 不写第二套“简化判断”

### 7.5 是否记录 policy_version？

是。

- 覆盖 route policy、capability permissions、default_allow、
  subject weights、scope 规则、schema/evaluator version
- 采用 `SHA256(canonical_policy_snapshot + policy_schema_version + evaluator_version)[:16]`
- 不包含请求上下文、时间戳、随机值
- 同一条 Shadow 记录必须可解释当时使用的是哪一版策略语义

### 7.6 Shadow 日志是否避免敏感凭证？

是。

禁止：

- Authorization header 原文
- API Key / token / secret
- 完整哈希或 hex 前缀

允许：

- principal_id
- credential_id
- role
- identity_state
- error_class

## 8. 失败边界

Shadow 失败分类：

| 失败 | 行为 |
|---|---|
| DB 不可用 | 记录 `error`，不阻断请求 |
| 策略表读取失败 | 记录 `error`，不阻断请求 |
| 身份映射缺失 | `mapping_missing`，不创建 |
| 路由不在覆盖范围 | `not_evaluated`，不创建 |
| 覆盖范围内上下文不足 | `insufficient_context`，不返回 allow |
| 敏感字段异常 | 记录 error_class，不记录值 |
| Shadow 记录写入失败 | 允许丢弃记录，不影响请求 |

Shadow 数据写入失败、超时、内部异常都不能反向影响 Legacy。

## 9. 验收标准

- Legacy 允许 + Shadow allow → 响应状态/body/headers/cookies 不变
- Legacy 允许 + Shadow deny → **不阻断**，响应不变
- Legacy 拒绝 + Shadow 记录 → 仍拒绝；不调用 Identity Mapping
- Shadow 关闭 → 不产生策略评估和 Shadow 数据库写入
- Shadow 写库失败 → Legacy 请求仍按原逻辑完成
- Shadow 超时 → Legacy 请求仍按原逻辑完成
- Shadow 内部异常 → 状态码、body、关键 Header、Cookie 不变
- 未知 key → `unmapped`，身份表无新增
- revoked/expired/disabled → 对应状态，不复活
- 管理路由 → `RoutePolicyEvaluator`，不伪装成 capability
- capability 路径 → `decide_permission()`
- `not_evaluated` 与 `insufficient_context` 不混用
- 每条记录有覆盖全部策略输入的 `policy_version`
- 日志/记录无明文 Key、Token、完整哈希
- Shadow deny 不影响 Legacy 已放行请求
- 外部副作用与 baseline 一致（除 Shadow 自身事件表写入）

## 10. 不进入 4F-2B

- `/api/identity/me`（4F-2C）
- Admin Login
- 路由授权
- Default Deny
- 缓存/多进程撤销传播
- Shadow 记录 UI

## 11. Gate 0 决策状态

已按审查意见确认：

1. Shadow 第一版只覆盖管理、策略、Registry 路由；其他记 `not_evaluated`。
2. Legacy 拒绝记 `legacy_denied`，不创建、不映射身份。
3. `policy_version` 覆盖全部策略输入 + schema + evaluator version。
4. 保留 30 天或 10000 条，先到先清；批处理，失败隔离，不混审计表。
5. 4F-2B 只观察 Bearer 旧 API Key，Studio cookie 留给 4F-3。
6. 上下文不足记 `insufficient_context`，不能推导为 allow。
7. 管理路由使用 RoutePolicyEvaluator；能力调用使用 `decide_permission()`；
   不把管理路由伪装成能力调用。
8. 区分 `evaluated` / `not_evaluated` / `insufficient_context` / `error`。

以上契约确认后进入 4F-2B 实现。
