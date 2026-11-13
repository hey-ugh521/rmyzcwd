# Phase 4F-2C 设计稿：`/api/identity/me`

- 日期：2026-11-14
- 状态：**设计已定稿；本地实现完成，未部署生产**
- 输入：
  - [4F 设计计划 §8](2026-11-13-phase4f-identity-authorization.md)
  - [Identity Data Model](../../current/IDENTITY-DATA-MODEL.md)
  - [4F-2C 前置检查清单](2026-11-14-phase4f2c-preflight.md)
  - [2B 后续问题清单](2026-11-14-phase2b-followups.md)

## 1. 目标

只读身份查询：

```text
GET /api/identity/me
Authorization: Bearer <legacy_api_key>
  -> 只读解析既有 Credential
  -> 返回既有 Principal 的公开身份
```

不授权、不登录、不创建身份、不改变 Legacy 放行。Endpoint 自身不写 Shadow Event；
Shadow Middleware 仍按现有全局规则独立观察。

## 2. D1：身份来源采用 A

`/api/identity/me` 自行解析 Bearer，不依赖 `request.state.legacy_auth`。

| 组件 | 职责 |
|---|---|
| Legacy Middleware | 决定原有请求是否放行；不改变 |
| `/api/identity/me` | 读取当前 Bearer 对应的既有身份；只读 |
| Shadow Middleware | 独立观察；不参与 endpoint 返回 |

## 3. D1 补充：`inspect_legacy_api_key()` 契约

实现依据：[core/identity.py:616](../../../core/identity.py#L616)

| 检查 | 实际行为 | 返回状态 |
|---|---|---|
| 配置 key 存在 | `configured_legacy_key()` 为空 → 函数返回 unmapped | `unmapped` |
| Bearer 是否等于 configured key | `hmac.compare_digest` 常量时间比较 | 不匹配 → `unmapped` |
| Credential 是否存在 | 按 `sha256(configured)[:16]` 查 `lookup_prefix` | 无 → `mapping_missing` |
| 完整 key 是否匹配 | `_verify_secret` 校验 salted hash | 不匹配 → `conflict` |
| Credential 是否撤销 | `revoked_at` 非空 | `revoked` |
| Credential 是否过期 | `expires_at <= now` | `expired` |
| Principal 是否存在 | `service-legacy` 查询 | 缺 → `mapping_missing` |
| Principal 是否启用 | `enabled` | `principal_disabled` |
| Mapping 是否合法 | kind=machine 且 role=service | 否则 `conflict` |
| 多条 mapping | `lookup_prefix` 有 UNIQUE；固定 principal | 无多映射路径 |

重要区分：

- 该函数**不验证 Bearer header 语法**；header 解析由 endpoint 负责。
- 该函数**不更新 `last_used_at`**，不写 credential/session/cache。
- 该函数**不调用** `resolve_legacy_api_key()`，不会创建/复活身份。
- 数据库异常会向上传播；endpoint 必须映射为 500，而不是 401。
- 原始 key 不写入日志；错误日志只记录异常类型和请求 ID。

### 3.1 服务端配置缺失与凭据无效

`inspect_legacy_api_key()` 当前把“配置缺失”返回为 `unmapped`。为避免把
部署故障伪装成客户端认证失败，endpoint 适配层必须先做一次**只读配置门禁**，
并保证一次请求使用一致的配置视图：

```text
parse Bearer -> 401 if missing/malformed

configured = identity.configured_legacy_key()       # snapshot
if not configured -> 500 configuration_unavailable
if not constant_time_equal(request_key, configured) -> 401

state = identity.inspect_legacy_api_key(request_key)

configured_after = identity.configured_legacy_key() # guard
if configured_after != configured -> 500 configuration_changed
```

- 该检查只读 `store.CFG`，不修改 4F-2A mapping 或 frozen 代码
- 配置缺失时**不调用** inspect
- request_key 与 snapshot 不匹配时直接 401，不调用 inspect
- inspect 返回后再比对配置；配置变化则 500，避免“检查一个 Key、验证另一个 Key”
- 配置在进程内一次性加载，无 reload endpoint；生产不允许运行时替换
- `configured_legacy_key()` 与 `inspect_legacy_api_key()` 都读取同一个
  模块级 `store.CFG`，没有第二份配置副本
- 该模块没有依赖注入的配置对象；测试替换必须在隔离进程/临时库中完成
- 内部状态 `configuration_unavailable` / `configuration_changed`；
  对外统一 500 `identity_unavailable`
- 不引入配置 reload；不要求修改 4F-2A/2B

严格边界：

- `inspect_legacy_api_key()` 内部仍会读取配置；本设计不声称它是参数化快照函数
- 一致性由 endpoint 的 snapshot + pre/post guard 保证，而不是靠“配置通常不变”
- 如果未来需要严格单快照，必须单独批准给冻结函数增加只读参数，不能在本阶段顺手改
- post-guard 的实际保护范围：只能发现进程内 `store.CFG` 在请求期间被改动；
  配置文件本身变化不会被已运行进程感知，也不会被该 guard 捕获
- 本 endpoint 不提供配置热加载；配置文件变更仍需重启

响应判定：

| 情况 | 内部状态 | 对外 |
|---|---|---|
| 请求无/错 Bearer | `unauthenticated` | 401 |
| 服务端未配置 Legacy Key | `configuration_unavailable` | 500 |
| 配置在请求内发生变化 | `configuration_changed` | 500 |
| 数据库不可用 | `database_unavailable` | 500 |
| 凭据不匹配/撤销/过期/禁用/mapping 缺失 | 对应 inspect 状态 | 401 |

## 4. Endpoint 契约

```text
GET /api/identity/me
Authorization: Bearer <legacy_api_key>
```

### 4.1 Bearer Header 解析

遵循标准 HTTP 认证语法，不从 `request.headers.get()` 的合并结果猜测重复字段：

- 直接检查原始 ASGI header 列表
- Authorization 缺失 → 401
- Scheme 大小写不敏感，只接受 `Bearer`
- Scheme 与凭据之间允许一个或多个空格（标准认证语法），但不允许多个
  相互冲突的 Authorization header
- 空凭据、格式错误、重复/多值 Authorization → 401
- 不接受 Query、Cookie、Body 回退读取
- 不 trim/大小写转换/截断凭据内容
- 不对原始 key 做日志输出

请求 ID：

- `X-Request-Id` 只用于日志，不是身份凭据
- 可接受字符集和长度受限（建议 `[A-Za-z0-9._:-]`，最多 128）
- 非法/过长/non-ASCII header 不用作日志关联，由服务端生成替代 ID
- 不将未净化的 header 写入日志或响应
- 异常日志只记录异常类型和服务端/校验后的请求 ID，不记录原始异常文本

### 4.2 成功

成功：

```json
200 {
  "ok": true,
  "identity_mode": "legacy",
  "principal": {
    "id": "service-legacy",
    "kind": "machine",
    "role": "service"
  }
}
```

失败：

```json
401 {
  "ok": false,
  "error": "unauthenticated"
}
```

HTTP 细节：

- 401 带 `WWW-Authenticate: Bearer`
- 不附加 `error="invalid_token"` 或任何状态原因
- 500 不伪装成认证失败：

```json
500 {
  "ok": false,
  "error": "identity_unavailable"
}
```

- 500 不在响应中返回异常详情或 key
- 500 不带 `WWW-Authenticate`
- 成功/失败/500 均 `Cache-Control: no-store`
- 401 才带 `WWW-Authenticate: Bearer`
- 不设置 Cookie
- 不创建 Session
- 不修改 `request.state.legacy_auth`

## 5. D2：凭据状态响应

对外统一 401：

| 输入 | 响应 |
|---|---|
| 无 Bearer / 格式错误 | 401 `unauthenticated` |
| 未知/错误 key | 401 |
| revoked | 401 |
| expired | 401 |
| principal disabled | 401 |
| mapping 缺失 | 401 |
| conflict | 401 |
| 服务端未配置 Legacy Key | 500 `identity_unavailable` |
| 配置请求内发生变化 | 500 `identity_unavailable` |
| 数据库/内部异常 | 500 `identity_unavailable` |
| resolved | 200 |

内部可以区分状态用于日志/测试，但不进入响应，避免凭据枚举。

### 5.1 401 契约适用范围

上述 401 是 **endpoint 自身返回**的契约。Core 绑定 `127.0.0.1`，非回环或
被 `auth_mw` 提前拒绝的请求可能得到 Legacy 的 `{"error":"unauthorized"}`，
不带本契约的 `WWW-Authenticate`/`Cache-Control`。不得把 Legacy 拦截响应
误读为 `/api/identity/me` 的响应契约；这也不要求修改 `auth_mw`。

数据库/内部异常：

- 返回 500 `identity_unavailable`
- 不返回 401，不带 `WWW-Authenticate`
- `Cache-Control: no-store`
- 记录异常类型、请求 ID；不记录 key/token

## 6. D3：字段最小化

首版只返回：

```json
{
  "ok": true,
  "identity_mode": "legacy",
  "principal": {
    "id": "service-legacy",
    "kind": "machine",
    "role": "service"
  }
}
```

- `principal.id`：身份主体
- `principal.kind`：主体类型
- `principal.role`：**描述性属性，不是授权结果、permission 或 scope**
- **不返回 `credential.id`**：首版没有客户端使用场景；避免暴露内部稳定标识
- 不返回 display_name、metadata、hash、prefix、原始 key
- 不返回 `last_used_at`、created_at 等内部时间

如果未来需要 `credential.id`，必须单独证明客户端用途并重新评审。

## 7. D3 只读保证

- 只调用 `inspect_legacy_api_key()` 的 SELECT 路径
- 不调用 `authenticate_credential()`，避免 `last_used_at` 更新
- 不调用 `_map_legacy_key()` / `resolve_legacy_api_key()`
- 不写 principals / credentials / admin_sessions / bootstrap_tokens
- endpoint 自身不写 audit / Shadow Event
- Shadow Middleware 开启时仍可能按全局规则写 `not_evaluated`
- 不写缓存
- 请求前后数据库行数不变，且关键字段（尤其 `last_used_at`）不变
- 只读性除运行快照外，必须做完整调用链审计：
  `endpoint -> inspect_legacy_api_key -> store.conn SELECT`
  不存在 INSERT/UPDATE/DELETE 或隐式写入
- 数据库只读事务可作为辅助证据，但不引入本阶段数据库架构变更

### 7.1 事件循环隔离

- endpoint 的 Bearer 解析和配置快速检查在事件循环内完成
- 同步 DB 调用通过 `run_in_threadpool` 执行，不能直接在 `async def` 中阻塞事件循环
- 默认 5s 超时；超时返回 500 `identity_unavailable`
- 超时后后台线程可能继续运行，但不得写身份表；该风险与 2B worker 生命周期
  分开记录
- 线程池容量、超时后 worker 回收、PG timeout 与监控见
  [4F-2C 后续问题](2026-11-14-phase4f2c-followups.md)；部署前必须确认
- 回归测试必须验证慢 DB 不拖慢同一事件循环上的其他请求

## 8. D4：与 Legacy 隔离

强约束：

- 查询成功不得让其他请求获得额外权限
- 查询失败不得让其他请求被拒绝
- 不修改 `request.state.legacy_auth`
- 不修改 Legacy Mapping
- 不把查询结果缓存为全局认证状态

测试必须覆盖：

- endpoint 成功/失败后，原有 Legacy 路由响应与认证行为不变
- 不是只检查 `auth_mw` 文件未修改，而是跑真实回归

## 9. D5：Shadow Event 描述

精确区分：

| 行为 | 是否发生 |
|---|---|
| endpoint 主动写 Shadow Event | 否 |
| Shadow 开启时全局 Middleware 观察该请求 | 可能发生 |
| 观察结果影响 endpoint 响应 | 否 |
| 观察结果影响 Legacy 放行 | 否 |

因此：

- endpoint 本身不调用 `record_shadow_event`
- 不承诺“该 endpoint 永远不产生 Shadow Event”
- Shadow 开启时按现有全局规则允许 `not_evaluated`
- 不为 4F-2C 特殊排除该路由；修改全局观察属于 2B 后续变更
- endpoint 无 `X-Request-Id` 时自生成的 request ID 仅用于自身日志，
  与 Shadow Event 的 request_id 不是同一来源，不能互相校验

## 10. D6/D7/D8：独立 2B 后续问题

不进入 4F-2C 实现：

- Shadow 求值/写库异常静默
- worker 超时/积压/退出
- `latency_ms` 语义与实现

详见：

[2026-11-14-phase2b-followups.md](2026-11-14-phase2b-followups.md)

这些问题不得在 4F-2C PR 中顺手修复；需要单独审批和验收。

## 11. D9：配置、清理与审计

- 下次 Shadow 窗口必须有开启态脱敏配置快照
- Shadow Event 清理必须批准、先导出证据、记录独立操作
- 不删除正式安全审计
- 误创建 permission 的审计痕迹保留
- 4F-2C 不清理生产 Shadow Event
- endpoint 不写 audit

## 12. D10：响应基线规则

未来窗口比较：

- 必须一致：安全/语义 Header、Cookie 名称/属性
- 允许变化：Date、请求追踪类动态 Header
- 明确忽略：非关键字段

保存原始采样、归一化规则和比较结果。当前 PG 报告只有 status/body，
headers/cookies 没有基线，不能回填为已验证。

## 13. D11：冻结边界

| 项 | 状态 |
|---|---|
| 4F-1 数据模型 | 冻结 |
| 4F-2A Legacy Mapping | 冻结 |
| 4F-2B Shadow/认证/放行 | 冻结 |
| 4F-2C `/api/identity/me` | 仅设计 |
| Admin Login / 路由授权 / Default Deny | 不在范围 |
| G-10 | 打开，但不是 4F-2C 准入条件 |

## 14. D12：证据分层

- 设计正确
- 代码正确
- 运行正确
- 生产行为正确

分别记录；单元测试不能替代生产证据，报告不能替代故障注入。

## 15. 测试计划

功能：

- 有效 key + resolved mapping → 200，字段精确
- 无 Bearer / 格式错误 → 401
- Scheme 大小写、空凭据、多 Authorization → 401
- 尾部换行/控制字符 → 401
- 不从 Query/Cookie/Body 回退取 Key
- 未知/错误 key → 401
- revoked / expired / disabled → 统一 401
- mapping 缺失 / conflict → 401
- 服务端 Legacy Key 未配置 → 500 `identity_unavailable`
- 数据库/内部异常 → 500，不伪装成 401
- 无效/过长 `X-Request-Id` 不进入日志，使用服务端替代 ID
- 慢 DB 不阻塞同一事件循环的其他请求
- DB 超时 → 500，且不阻塞其他请求

只读：

- 并发查询前后行数不变
- `last_used_at` 不变
- 相关表关键字段不变
- 不新增 Principal/Credential
- endpoint handler 不主动写 Shadow Event
- 调用链审计确认无 INSERT/UPDATE/DELETE
- 配置门禁与 inspect 使用同一 endpoint snapshot；配置变化返回 500
- 原始 ASGI header 列表中的重复 Authorization 被拒绝

隔离：

- endpoint 成功/失败后 Legacy 路由行为不变
- 不修改 `request.state.legacy_auth`
- 不修改 mapping
- 非回环被 `auth_mw` 拦截的 401 属于 Legacy，不得断言为 endpoint 契约

平台：

- 3.12 / 3.13 本地复跑；CI 是否覆盖 3.12 必须单列，不能把本地手跑当 CI 证据

HTTP 语义：

- 401 `WWW-Authenticate: Bearer`
- 401 无 `invalid_token`
- 成功/401/500 均 `Cache-Control: no-store`
- 500 无 `WWW-Authenticate`
- 500 无异常详情/无 key

脱敏：

- 响应、日志、异常均无原始 Key/Token/hash
- credential.id 不出现

Shadow：

- Shadow 关闭：endpoint 不产生 Shadow Event
- Shadow 开启：允许全局 Middleware 产生 `not_evaluated`，但 endpoint 响应
  不依赖 Shadow Event 写入成功，也不依赖 worker 状态
- 全局 Shadow 失败不影响 endpoint 200/401/500 结果

## 16. 不进入 4F-2C

- Admin Login
- 路由授权
- Default Deny
- 修改 4F-2A mapping
- 修改 4F-2B Shadow/认证/放行
- 2B 可观测性/worker/latency 修复
- 生产故障注入
- `credential.id` 暴露

## 17. 审查裁决

| 问题 | 裁决 |
|---|---|
| D1 方案 A 及只读契约 | 接受 |
| 统一 401 + `WWW-Authenticate: Bearer` | 接受 |
| 500 `identity_unavailable` | 接受 |
| 删除 `credential.id` | 接受 |
| 2B 问题独立立项 | 接受 |
| Shadow 开启全局 `not_evaluated` | 接受 |

实现状态：

- 本地实现完成
- 未部署生产
- 生产配置、Shadow、4F-1/2A/2B 未改变
- 部署需要单独授权
