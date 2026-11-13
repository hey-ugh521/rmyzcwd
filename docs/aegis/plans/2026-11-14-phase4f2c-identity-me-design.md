# Phase 4F-2C 设计稿：`/api/identity/me`

- 日期：2026-11-14
- 状态：**Draft for design review；未实施**
- 输入：
  - [4F 设计计划 §8](2026-11-13-phase4f-identity-authorization.md)
  - [Identity Data Model](../current/IDENTITY-DATA-MODEL.md)
  - [4F-2C 前置检查清单](2026-11-14-phase4f2c-preflight.md)
  - [4F-2B PG 验证报告](../reports/PHASE4F2B-PG-SHADOW-VALIDATION-REPORT.md)

## 1. 目标

提供一个**只读身份查询**：

```text
请求携带 Bearer
  -> 只读解析 Credential
  -> 返回既有 Principal 的公开身份信息
```

本阶段不授权、不登录、不创建身份、不改变 Legacy 认证。

## 2. D1：身份来源

采用 **方案 A**：

- `/api/identity/me` 自行只读解析 `Authorization: Bearer`
- 不依赖 `request.state.legacy_auth`
- 原因是 loopback GET 在 Legacy auth 中不认证，`legacy_auth` 永远是匿名
- 复用 4F-2A 的 `identity.inspect_legacy_api_key()` 只读路径
- 不调用会创建身份的 `resolve_legacy_api_key()`

边界：

| 组件 | 职责 |
|---|---|
| Legacy Middleware | 决定原有请求是否放行，不改变 |
| `/api/identity/me` | 读取当前 Bearer 对应的身份，只读 |
| Shadow Middleware | 独立观察，不参与本 endpoint 的返回 |

## 3. Endpoint 契约

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
  },
  "credential": {
    "id": "cred_..."
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

约束：

- 不返回 `secret_hash`、`lookup_prefix`、原始 Key、Token
- 不返回 display_name/metadata（除非未来单独批准）
- 不返回 unknown/revoked/disabled 的区别
- 不设置 Cookie
- 不创建 Session
- 不修改 `request.state.legacy_auth`
- 不改变任何 Legacy 放行结果

## 4. D2：凭据状态响应

对外统一：

| 输入 | 响应 |
|---|---|
| 无 Bearer | 401 `unauthenticated` |
| 未知 key | 401 `unauthenticated` |
| 错误 key | 401 `unauthenticated` |
| revoked | 401 `unauthenticated` |
| expired | 401 `unauthenticated` |
| principal disabled | 401 `unauthenticated` |
| service-legacy 缺失 | 401 `unauthenticated` |
| resolved | 200 |

内部诊断可区分原因，但不能进入响应；避免密钥枚举。

## 5. D3：只读保证

- 使用 `inspect_legacy_api_key()`：只 SELECT
- 不使用 `authenticate_credential()`：该校验会更新 `last_used_at`
- 不写 audit
- 不写 Shadow Event
- 不写身份表
- 不调用 `resolve_legacy_api_key()`

Verification：

- endpoint 前后 `principals` / `credentials` / `admin_sessions` /
  `bootstrap_tokens` 行数不变
- endpoint 前后 credential `last_used_at` 不变

## 6. D4：与 Legacy 隔离

- `auth_mw` 不改
- `request.state.legacy_auth` 不改
- `/api/identity/me` 返回 401 不改变其他请求认证
- endpoint 不是路由授权开关
- Shadow 开启时，该 endpoint 仍可能被全局 Shadow 观察为
  `not_evaluated`；本阶段不修改 Shadow 行为

## 7. D5：请求 ID / 事件关联

当前 4F-2C endpoint 不写 Shadow Event，因此不需要 request_id 关联。

但下次 Shadow 窗口需要：

- 受控请求带 `X-Request-Id`
- 格式和唯一性在测试中校验
- 事件与请求按 header 关联
- 不把未带 header 的 NULL 解释为代码断链

该项不修改 2B 冻结代码。

## 8. D6：Shadow 异常可观测性

**不在 4F-2C 实现。**

当前缺口属于 4F-2B 后续变更：

- 求值失败
- 写库失败
- worker 静默 `except Exception: pass`
- 无事件/日志/失败计数

4F-2C 设计只记录要求：

- 故障不能影响 Legacy
- 失败必须可观测
- 不能依赖再写 Shadow Event 作为唯一失败证据

如要修复，必须单独立项并单独审批，不作为 4F-2C 的默认组成。

## 9. D7：Worker 生命周期

**不在 4F-2C 实现。**

需要单独处理的 2B 后续问题：

- daemon 线程超时后是否继续
- 请求取消后资源释放
- worker 积压/队列上限
- Shadow 关闭后确认无遗留线程
- Core 重启时未完成任务

同样必须单独审批，不得借 4F-2C 修改冻结 2B 代码。

## 10. D8：`latency_ms`

**不在 4F-2C 实现。**

先决定语义：

- Shadow evaluation 耗时
- Shadow persistence 耗时
- 还是两者分别记录

不把历史 NULL 回填为 0，不把未采集描述为已验证。

## 11. D9：配置、清理与审计

- 下次窗口必须有开启态脱敏配置快照
- Shadow Event 清理必须批准、先导出证据、记录独立操作
- 不删除正式安全审计
- 误创建 permission 的审计痕迹保留
- 4F-2C 不自行清理生产事件

## 12. D10：响应基线规则

未来比较应区分：

- 必须一致：安全/语义 Header、Cookie 名称/属性
- 允许变化：Date、请求追踪类动态 Header
- 明确忽略：非关键字段

保存原始采样、归一化规则和比较结果；当前 PG 报告没有 headers/cookies
基线，不能回填为已验证。

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

分别记录；不得用单元测试代替生产证据，也不得用报告代替故障注入。

## 15. 测试计划（设计阶段）

- 有效 key + mapping -> 200，字段精确
- 未知/错误/revoked/expired/disabled -> 统一 401
- 无 Bearer -> 401
- 前后身份表行数不变
- `last_used_at` 不变
- 响应不含 secret/prefix/hash
- 不设置 Cookie
- 不改变 Legacy 响应
- Shadow 关闭时不产生事件

## 16. 不进入 4F-2C

- Admin Login
- 路由授权
- Default Deny
- 修改 4F-2A mapping
- 修改 4F-2B Shadow/认证/放行
- 生产故障注入
- Shadow worker/latency 重构

## 17. 设计审查问题

请审查：

1. D1 = A 是否接受？
2. 401 统一响应是否接受？
3. 是否接受 4F-2C 不修复 2B 可观测性/worker/latency，而另立变更？
4. 响应字段是否只保留 principal id/kind/role/credential id？
5. 是否要求 endpoint 在 Shadow 开启时显式不产生事件，还是接受现有全局观察？
