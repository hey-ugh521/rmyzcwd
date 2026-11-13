# Phase 4F 下一阶段计划（Draft）

- 日期：2026-11-13
- 状态：**Gate 0 条件通过；契约已修订，待确认后编码**
- 前置：
  - [4F-2B Shadow 设计稿](2026-11-13-phase4f2b-shadow-design.md)
  - [4F-2A 报告](../reports/PHASE4F2A-LEGACY-MAPPING-REPORT.md)
  - [测试可复现性报告](../reports/TEST-REPRODUCIBILITY-2026-11-13.md)

## Goal

在不改变真实认证/放行结果的前提下，完成 **4F-2B Identity Shadow**：

```text
Legacy Authentication（唯一放行依据）
  -> Identity Shadow Evaluation（只观察）
  -> Shadow 记录
  -> Handler
```

同时把 G-10 的 unit 层 CI 收口，但 **不宣称 G-10 关闭**。

## 当前基线

| 项 | 状态 |
|---|---|
| 4F-1 身份数据模型 | 验收通过，冻结 |
| 4F-2A Legacy Mapping | 有条件验收通过，冻结 |
| 4F-2B 设计稿 | 已提交，等待确认 |
| 4F-2B 代码 | 未开始 |
| 4F-2C 身份接口 | 未开始 |
| 生产认证 | 未切换 |
| SQLite 并发修复 | `_conn_lock` + anchor + 跨进程锁，本机/POSIX 回归通过 |
| 单元测试 | 95 unittest + 5 webhook |
| CI | Windows/Ubuntu 矩阵和负向 probe step 已加，待实跑确认 |
| G-10 | 保持打开 |

## Gate 0 决策（本轮修订）

1. **覆盖范围**：只覆盖管理、策略、Registry 路由；其他记 `not_evaluated`。
2. **Legacy 拒绝**：记 `legacy_denied`，不创建、不映射身份。
3. **policy_version**：覆盖全部策略输入 + schema + evaluator version。
4. **保留期**：30 天或 10000 条，先到先清；批处理，失败隔离。
5. **观察范围**：只观察 Bearer 旧 API Key；Studio cookie 留给 4F-3。
6. **上下文不足**：记 `insufficient_context`，绝不推导为 allow。
7. **权限域**：管理路由 `RoutePolicyEvaluator`；能力路径首版
   `not_evaluated`，不调用 `decide_permission()`；后续阶段再接线。
   不把管理路由伪装成能力调用。
8. **中间件顺序**：Shadow 外层、Legacy auth 内层；Legacy 仅写只读
   `request.state.legacy_auth`；Shadow 在 Legacy 返回后读取。
9. **三态**：区分 `evaluated` / `not_evaluated` / `insufficient_context` / `error`。

## 架构边界

新代码：

```text
core/identity_shadow.py      观察器、Route Context Resolver、Shadow 记录
core/route_authorization.py  RoutePolicyEvaluator、route action map
core/server.py               中间件接线
identity_shadow_events       新表，独立迁移
```

硬约束：

- 不修改 Legacy Authentication 的 401/403、响应 body/header
- 不调用任何创建身份的函数
- 管理路由使用 RoutePolicyEvaluator，不伪装成 capability
- 能力路径首版统一 `not_evaluated`，不调用 `decide_permission()`
- Shadow 外层；Legacy auth 内层；Legacy 只增加只读观测状态
- Shadow allow/deny 只写入记录
- 记录中不出现 Key/Token/完整 hash
- Shadow 异常不得传播到 Handler
- `not_evaluated` 与 `insufficient_context` 分开存储

## Change Necessity

`Change Necessity: code-change`

理由：要观察真实请求中的新身份评估差异，仅靠 CLI/文档无法产生请求级证据。
最小边界：新增观察模块、事件表、非阻断中间件；不改认证函数。

## Ripple Signal Triage

- 共享核心：认证中间件、Registry 路由、只读 `legacy_auth` 状态
- 消费者：管理页面/API 响应
- 风险：中间件顺序改变响应；Shadow 异常传播；事件表写放大；
  policy_version 漂移；管理路由与能力权限域混淆
- 对策：Shadow 外层、Legacy 内层；响应基线对比；异常/超时注入；
  route 与 capability 分别测试

## TDD Route

```text
Mode: off
Decision: skipped
Reason: 用户未要求 strict TDD；但安全/权限切片仍必须有回归测试和失败注入
```

## 任务分解

### 2B-1 Shadow 记录与保留（已完成）

- 新表 `identity_shadow_events` + 索引
- 字段：request_id、method、path、route_id、route_action、resource_type、
  resource_id、legacy_authenticated、legacy_actor、identity_state、
  evaluation_state、policy_domain、principal_id、credential_id、role、action、
  policy_result、policy_version、reason、error_class、latency_ms
- retention 清理函数：30 天/10000 条，批次上限 500，失败隔离
- 测试：schema 幂等、保留期、批处理、无敏感字段、事件表与审计表分离
- 报告：[PHASE4F2B1-SHADOW-EVENT-REPORT.md](../reports/PHASE4F2B1-SHADOW-EVENT-REPORT.md)

### 2B-2 观察器与权限评估（已完成）

- `RouteContextResolver`：route_id/action/resource/role
- `RoutePolicyEvaluator`：角色矩阵 + route action + resource，默认拒绝
- **首版不评估能力调用路由**；`/v1/...`、`/api/capabilities/invocations`
  统一 `not_evaluated`
- 能力调用 `decide_permission()` 只保留架构边界，不在本切片接线
- Bearer → 4F-2A 已存在 mapping → 状态检查
- policy_version：canonical policy snapshot + schema + evaluator version
- 三态：`evaluated` / `not_evaluated` / `insufficient_context` / `error`
- 测试：
  - unknown → `unmapped`，无身份写入
  - revoked/expired/disabled → 对应状态
  - 管理路由 route policy 与 capability 路径 `not_evaluated`
  - 不调用 `decide_permission()`（能力首版未接线）
  - 记录无 secret
- 报告：[PHASE4F2B2-SHADOW-EVALUATOR-REPORT.md](../reports/PHASE4F2B2-SHADOW-EVALUATOR-REPORT.md)

### 2B-3 中间件 Shadow 接线

- **Shadow 外层**；Legacy Authentication 内层
- Legacy auth 仅写只读 `request.state.legacy_auth`
- Shadow 在 Legacy 返回后读取结果；拒绝路径可记录 `legacy_denied`
- 增加 `identity.shadow.enabled` 开关，默认 **off**
- 基线响应对比测试：
  - Legacy allow + Shadow allow/deny → 状态码/body/headers/cookies 完全一致
  - Legacy deny + Shadow 记录 → 仍拒绝；不调用 Identity Mapping
  - Legacy deny 后 Shadow 确实执行并记录 `legacy_denied`
  - Shadow 写库失败 → Legacy 请求仍按原逻辑完成
  - Shadow 超时 → Legacy 请求仍按原逻辑完成
  - Shadow 内部异常 → 响应完全不变
  - Shadow 关闭 → 不产生策略评估和 Shadow 写入
  - Shadow deny 不阻断 Legacy 已放行请求
  - 响应的 body/headers/cookies/外部副作用与 baseline 一致
    （Shadow 自身事件表除外）
- 不修改任何 401/403/status/headers/body

### 2B-4 证据与冻结

- 记录一次 shadow 运行摘要
- 明确 allow/deny 只是观察结果
- 更新报告和设计稿状态
- 停止点：等待 4F-2B 验收后再讨论 4F-2C

## G-10 并行轨道

不阻塞 4F-2B，但必须单独推进：

1. 观察 Windows/Ubuntu CI 实跑结果
2. 增加 PG contract CI（`MOBIUS_TEST_PG_DSN`）
3. 安装/启动/故障注入测试分层
4. 不把“unit 层绿灯”当作 G-10 关闭

## 验证标准

- 既有 95+5 测试全绿
- 新增 shadow 测试全绿
- Legacy 认证响应状态/body/headers/cookies 基线完全不变
- 未知 key/revoked 不写身份表
- 管理路由走 RoutePolicyEvaluator；能力路径首版 `not_evaluated`，不调用 `decide_permission()`
- Shadow 外层能观察 Legacy 拒绝，且拒绝路径不映射身份
- `not_evaluated` 与 `insufficient_context` 分开
- 每条 Shadow 记录可追溯到完整 policy_version
- 无 secret 出现在表或日志
- Windows + Ubuntu CI 实跑通过

## 停止条件

- 4F-2B 只观察，不改变放行结果
- 4F-2C/Admin Login/路由授权/Default Deny 不在本计划内
- 4F-2A 生产映射边界不变
- 生产 PG 路径不受 SQLite 测试修复影响

## 执行路由

- Gate 0 契约已修订，等待用户确认
- 确认后：inline 实现，按 2B-1 → 2B-2 → 2B-3 → 2B-4
- 每个切片单独验证，不合并认证切换
