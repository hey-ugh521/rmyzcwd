# Phase 4F 设计审查稿：身份、授权与默认拒绝迁移

- 状态：Draft for review；**未动代码**
- 日期：2026-11-13
- 关联：ADR-013 / ADR-014 / ADR-016、Phase 4D/4E 报告
- 范围：只覆盖 Phase 4F（身份与授权边界 + 默认拒绝迁移）；审计页/Provider 并发属于 4G，Core 进程托管属于 4H

## 0. 审查目标

先把身份模型定对，再实现 RBAC。Phase 4F 的验收不是“策略页能写”，而是：

- 调用者身份可认证、可归因、可撤销；
- 角色与凭证分离；
- 管理写权限不再来自“本机/旧 API key”的隐式信任；
- 默认拒绝迁移可影子评估、人工审核、灰度切换、快速回滚。

## 1. 当前边界（需要替换的部分）

现状：

- Core 本机变更接口凭 `core.api_key` 或 `/studio` 下发的 HttpOnly cookie 放行；
- `request.state.actor` 只有 `api-key` / `local-session` / `local-read`；
- `/studio` 本机 cookie 直接保存 API_KEY；
- `capability_permissions` 的 `scope` 已生效，但“谁能改策略”没有角色层；
- 审计 actor 是字符串，没有 principal / credential / request 关联。

因此 Phase 4F 要解决的是：

```text
Credential -> Principal -> Role -> Permission -> Resource/Action
```

而不是：

```text
Token 类型 -> 直接当角色
```

## 2. 身份模型

### 2.1 Principal

`principals`：

```text
id            稳定 id（human-001 / machine-qq / local-studio）
kind          human | machine | local
display_name
role          admin | operator | service
enabled       0/1
created_at
metadata_json
```

Principal 是**被归因的主体**。审计、任务、调用记录都应写 principal id，而不是只写字符串 actor。

### 2.2 Credential

`credentials`：

```text
id
principal_id
kind          api_key | admin_bootstrap | service_token
lookup_prefix 用于快速查找，不暴露完整 secret
secret_hash   scrypt/pbkdf2 哈希
hash_algo
expires_at
revoked_at
last_used_at
created_at
rotated_from
```

约束：

- 不存明文 secret；
- secret 只生成一次，界面只显示前缀/末四位；
- 支持撤销、轮换、过期；
- 认证时 prefix 查找 + 常量时间哈希比较；
- 认证成功后 `request.state.principal`、`request.state.credential`、`request.state.actor = principal.id`。

### 2.3 Admin Session

Studio 管理会话单独建模，不复用 API_KEY cookie：

```text
admin_sessions
  id
  principal_id
  token_hash
  csrf_hash
  created_at
  expires_at
  last_seen_at
  revoked_at
```

Cookie：

- `HttpOnly`
- `SameSite=Strict`
- 本机 HTTP 下 `Secure=false`，有 TLS 时 `Secure=true`
- 绝对过期 + 空闲过期
- CSRF token 双提交；所有管理 mutation 校验
- 只允许 `127.0.0.1` 访问 admin login

**旧 `/studio` cookie 不再天然等于管理员。** 它最多作为“本机 Studio 已连接”的读会话；策略写必须重新建立 admin session。

## 3. 角色与权限矩阵（建议）

初始只做静态角色，不引入角色表。

Action：

```text
identity.read
policy.read
policy.write
provider.read
provider.write
runtime.read
runtime.control
audit.read
capability.read
capability.invoke
```

矩阵：

| Action | admin | operator | service |
|---|---:|---:|---:|
| identity.read | ✓ | ✓ |  |
| policy.read | ✓ | ✓ |  |
| policy.write | ✓ |  |  |
| provider.read | ✓ | ✓ |  |
| provider.write | ✓ |  |  |
| runtime.read | ✓ | ✓ | ✓ |
| runtime.control | ✓ | ✓ |  |
| audit.read | ✓ | ✓ |  |
| capability.read | ✓ | ✓ | ✓ |
| capability.invoke | ✓ |  | ✓ |

`service` 是机器身份，不等于低权限管理员：它只能调用能力和读运行时状态，不能读策略、不能改策略、不能读审计。

`studio-local` 不是 principal；Studio 登录成功后映射到具体 human principal（通常 admin）。

## 4. 认证与授权管线

```text
Request
  -> Credential lookup (Bearer / admin session cookie)
  -> Principal
  -> Route permission check
  -> Handler
  -> Audit(principal_id, credential_id, request_id, before/after)
```

分三种过渡模式，避免一次切换伤到现有调用：

```text
identity.mode = legacy | shadow | enforce
permissions.mode = shadow | enforce
```

- `legacy`：保留旧认证结果，但构建 principal/credential 映射并记录 shadow 决策
- `shadow`：认证/授权决策只记录“会允许/会拒绝”，不改变实际放行
- `enforce`：按 Principal/Role 放行；旧 API_KEY 只映射 service，不映射 admin

影子模式必须复用**完整策略决策算法**（specificity、exact capability、priority、deny-wins、scope），不能只看“有没有 allow 行”。

## 5. 迁移与恢复

### 5.1 旧 API_KEY

原则：

```text
旧 API_KEY ≠ 默认 admin
```

迁移：

1. 旧 API_KEY 映射为 `service-legacy` principal（role=service）；
2. 旧 Studio cookie 不授予策略写；
3. 新 admin 只能通过本地 bootstrap 创建；
4. `identity.mode=enforce` 后，旧 key 不能绕过新的身份授权流程；
5. 保留 `core.api_key` 用于 Gateway/service 内部调用，但 Core 管理路由不再把它当 admin。

### 5.2 Admin bootstrap / 恢复

建议 v1 采用**一次性 bootstrap token**：

```text
MobiusCLI identity bootstrap-admin
  -> 生成一次性 token（只显示一次，存哈希）
  -> 浏览器打开 /studio/admin
  -> 输入 token
  -> 创建 admin session + CSRF
```

丢失恢复：

- 本机 elevated CLI 重新生成一次性 recovery token；
- 不提供默认 admin 密码；
- 不通过网络重置；
- 每次恢复写审计。

可选：后续再加用户名/密码或 OIDC，不在 4F。

### 5.3 默认拒绝迁移

不直接改 `default_allow=false`。

流程：

1. **Shadow Evaluation**
   - 假设 default deny，复用完整决策算法；
   - 记录 would_allow / would_deny / 匹配规则。
2. **Candidate Generation**
   - 从历史调用生成候选规则，但**不自动生效**；
   - 候选带证据计数、来源、capability、character scope。
3. **Policy Review**
   - 管理员逐条确认；
   - 不能把“历史成功”自动等同于“应继续授权”。
4. **Staged Enforcement**
   - 按 source / capability / 角色灰度；
   - 观察 deny 审计和错误率。
5. **Production Enforcement**
   - 全量 default deny；
   - 保留配置级快速回滚。

## 6. 审计设计

现有字段保留，补：

```text
request_id
principal_id
credential_id
resource_type
resource_id
role
source
```

约束：

- `before` / `after` 必须存在；
- `result = ok|fail|deny`；
- 不记录 token/secret；
- 普通管理 API 不提供审计修改/删除；
- 第一版至少做到 API 层 append-only；
- 后续再做数据库权限/哈希链。

## 7. 不进入 Phase 4F 的内容

- 完整多租户账号、OIDC/SSO
- 时间窗口/配额/IP/session scope
- Rate Limiter / Quota Manager / Circuit Breaker 拆分
- 审计页 UI（4G）
- Provider 并发验证（4G）
- Core 生命周期与进程托管（4H）

## 8. 实施切片（待批准）

### 4F-1 数据与凭证（不改变路由行为）

- principals / credentials / admin_sessions 表
- scrypt 哈希、prefix、撤销/轮换
- bootstrap CLI
- 测试：哈希不可逆、撤销、过期

### 4F-2 认证映射 + shadow

- 中间件从 Credential 解析 Principal
- `/api/identity/me`
- 旧 API key → service
- `identity.mode=legacy/shadow`
- 测试：未认证 401；旧 key 映射 service；不产生 admin

### 4F-3 路由授权 + Studio 管理会话

- 路由级 permission dependency
- policy/provider 写只允许 admin
- Studio admin login + CSRF + 过期
- `identity.mode=enforce`
- 测试：operator 写策略 403；admin 200；CSRF 缺失 403；旧 cookie 不能写

### 4F-4 默认拒绝影子与候选

- `permissions.mode=shadow`
- shadow 使用完整决策算法
- 候选规则表/报告
- 人工审核接口（只生成候选，不自动 allow）
- 测试：shadow 结果与 enforce 决策一致

### 4F-5 灰度与切换

- staged enforcement
- 回滚开关
- 恢复演练
- 验收报告

## 9. 验收标准

Phase 4F 完成条件：

- Principal/Credential 分离，审计可归因
- 旧 API_KEY 不是 admin，且不能绕过 admin session
- admin / operator / service 权限矩阵生效
- 未认证、过期、撤销、错误 CSRF 均拒绝
- 策略写只有 admin
- shadow 与 enforce 决策一致
- default deny 灰度可回滚
- admin bootstrap/recovery 可用
- secrets 不出现在 API/日志/审计

## 10. 需要先确认的设计决策

1. Admin 登录 v1：一次性 bootstrap token，还是本机密码，还是两者？
2. 旧 API_KEY 是否确认只映射 `service`，不做任何 admin 兼容？
3. 角色矩阵是否按上表冻结？
4. `studio-local` 是否只作为 session 来源，不再作为 principal？
5. 默认拒绝候选规则是否必须人工逐条确认，还是允许按 source 批量确认？
6. 审计 append-only 在 4F 只做 API 层，还是同时做 DB 权限？
7. 管理员恢复是否只允许本机 elevated CLI，还是允许本机文件 token？

以上确认后再进入 4F-1 编码。
