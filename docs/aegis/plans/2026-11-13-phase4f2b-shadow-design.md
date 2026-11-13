# Phase 4F-2B 设计审查稿：Identity Middleware Shadow

- 状态：Draft for review；未动代码
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

## 5. 权限决策

Shadow 复用现有权限决策，而不是重新实现：

```text
decide_permission(
  capability=<route/action mapping>,
  actor=<legacy actor or principal id>,
  source=<request source>,
  user_id=<if available>,
  character_id=<if available>,
)
```

需要评估：

- specificity：user > actor > source > *
- exact capability vs *
- priority
- deny-wins
- scope：characters / sources / users

### 上下文不足

如果当前请求没有足够上下文（例如 body 中的 character 尚未解析）：

- 记录 `insufficient_context`
- **不把结果记为 allow**
- 不猜测、不默认放行

### Action 映射

4F-2B 先覆盖现有管理路由：

```text
GET  /api/capabilities/permissions     -> policy.read
POST /api/capabilities/permissions     -> policy.write
GET  /api/capabilities/providers       -> provider.read
POST /api/capabilities/providers       -> provider.write
GET  /api/capabilities/provider-health -> provider.read
POST /api/capabilities/maintenance/*   -> policy.write
/api/registry/*                         -> policy.read/write
```

能力调用路由需要 body 上下文，4F-2B 可记录 `insufficient_context`，在 4F-2C 再完整评估。

## 6. Shadow 记录

建议新增：

```text
identity_shadow_events
  id
  ts
  request_id
  method
  path
  legacy_authenticated
  legacy_actor
  identity_state
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

`identity_state`：

```text
unmapped
mapping_missing
revoked
expired
principal_disabled
resolved
legacy_denied
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

是。

- 直接调用 `capabilities.decide_permission()`
- 不写第二套“简化判断”
- 若 scope/context 不足，记录 `insufficient_context`，不返回 allow

### 7.5 是否记录 policy_version？

是。

- 建议 `policy_version = sha256(canonical(permissions rows))[:16]` 或
  `max(permission.version)` + schema version
- 同一条 Shadow 记录必须可解释当时使用的是哪一版权限表

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
| 权限表读取失败 | 记录 `error`，不阻断请求 |
| 身份映射缺失 | `mapping_missing`，不创建 |
| 敏感字段异常 | 记录 error_class，不记录值 |
| 日志写入失败 | 允许丢弃 Shadow 记录，不影响请求 |

Shadow 数据写入失败不能反向影响 Legacy。

## 9. 验收标准

- Legacy 允许 + Shadow allow → 响应不变
- Legacy 允许 + Shadow deny → 响应不变
- Legacy 拒绝 + Shadow 记录 → 响应仍拒绝
- 未知 key → `unmapped`，身份表无新增
- revoked credential → `revoked`，不复活
- mapping 缺失 → `mapping_missing`，不创建
- Shadow 注入异常 → 响应完全不变
- specificity/priority/deny-wins/scope 结果与 `decide_permission` 一致
- 每条记录有 `policy_version`
- 日志/记录无明文 Key、Token、完整哈希

## 10. 不进入 4F-2B

- `/api/identity/me`（4F-2C）
- Admin Login
- 路由授权
- Default Deny
- 缓存/多进程撤销传播
- Shadow 记录 UI

## 11. 需要确认的决策

1. Shadow 是否覆盖全部 `/api/*`，还是只覆盖管理路由？
2. Legacy 拒绝的请求是否也记录 `legacy_denied`？
3. `policy_version` 采用权限表 hash 还是最大 version？
4. Shadow 事件保留期（建议 30 天 / 与 capability 记录一致）。
5. 4F-2B 是否只观察 Bearer 旧 API Key，不观察 Studio cookie？
6. 是否允许 Shadow 因 body 上下文不足而只记录 `insufficient_context`？

以上确认后进入 4F-2B 实现。
