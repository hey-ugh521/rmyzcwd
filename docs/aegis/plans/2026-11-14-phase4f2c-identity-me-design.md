# Phase 4F-2C 设计稿：`/api/identity/me`

- 日期：2026-11-14
- 状态：**设计修订版；未实施，未授权编码**
- 输入：
  - [4F 设计计划 §8](2026-11-13-phase4f-identity-authorization.md)
  - [Identity Data Model](../current/IDENTITY-DATA-MODEL.md)
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

不授权、不登录、不创建身份、不改变 Legacy 放行、不写 Shadow Event。

## 2. D1：身份来源采用 A

`/api/identity/me` 自行解析 Bearer，不依赖 `request.state.legacy_auth`。

| 组件 | 职责 |
|---|---|
| Legacy Middleware | 决定原有请求是否放行；不改变 |
| `/api/identity/me` | 读取当前 Bearer 对应的既有身份；只读 |
| Shadow Middleware | 独立观察；不参与 endpoint 返回 |

## 3. D1 补充：`inspect_legacy_api_key()` 契约

实现依据：[core/identity.py:616](../../core/identity.py#L616)

| 检查 | 实际行为 | 返回状态 |
|---|---|---|
| 配置 key 存在 | `configured_legacy_key()` 为空 → unmapped | `unmapped` |
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

## 4. Endpoint 契约

```text
GET /api/identity/me
Authorization: Bearer <legacy_api_key>
```

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
- 成功/失败均 `Cache-Control: no-store`
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
| resolved | 200 |

内部可以区分状态用于日志/测试，但不进入响应，避免凭据枚举。

数据库/内部异常：

- 返回 500 `identity_unavailable`
- 不返回 401
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
- 不写 audit / Shadow Event
- 不写缓存
- 请求前后数据库行数不变，且关键字段（尤其 `last_used_at`）不变

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
- 未知/错误 key → 401
- revoked / expired / disabled → 统一 401
- mapping 缺失 / conflict → 401
- 500 内部异常不伪装成 401

只读：

- 并发查询前后行数不变
- `last_used_at` 不变
- 相关表关键字段不变
- 不新增 Principal/Credential
- 不写 Shadow Event

隔离：

- endpoint 成功/失败后 Legacy 路由行为不变
- 不修改 `request.state.legacy_auth`
- 不修改 mapping

HTTP 语义：

- 401 `WWW-Authenticate: Bearer`
- 401 无 `invalid_token`
- 成功/失败 `Cache-Control: no-store`
- 500 无异常详情/无 key

脱敏：

- 响应、日志、异常均无原始 Key/Token/hash
- credential.id 不出现

Shadow：

- Shadow 关闭：endpoint 不产生 Shadow Event
- Shadow 开启：允许全局 `not_evaluated`，不影响 endpoint 响应或 Legacy 放行

## 16. 不进入 4F-2C

- Admin Login
- 路由授权
- Default Deny
- 修改 4F-2A mapping
- 修改 4F-2B Shadow/认证/放行
- 2B 可观测性/worker/latency 修复
- 生产故障注入
- `credential.id` 暴露

## 17. 待审查问题

1. D1 方案 A 及 `inspect_legacy_api_key()` 契约是否接受？
2. 统一 401 + `WWW-Authenticate: Bearer` 是否接受？
3. 500 `identity_unavailable` 是否接受？
4. 首版删除 `credential.id` 是否接受？
5. 2B 问题独立立项是否接受？
6. Shadow 开启时允许全局 `not_evaluated` 是否接受？
