# Phase 4F 下一阶段计划（Draft）

- 日期：2026-11-13
- 状态：**规划中；4F-2B 未批准实现**
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

## 需要先确认的 6 个决策

建议默认值如下；确认后写入设计稿，不再边实现边改。

1. **覆盖范围**
   - 建议：先覆盖管理/策略/Registry 路由和能力调用入口。
   - 其他 `/api/*` 可记录 `not_evaluated`，后续扩展。
2. **Legacy 拒绝请求**
   - 建议：记录 `legacy_denied`，但不做任何身份写入。
3. **policy_version**
   - 建议：permissions 表的 canonical hash + schema version，存 16 hex。
4. **保留期**
   - 建议：30 天或 10000 条，取先到；与 capability 记录策略一致。
5. **观察范围**
   - 建议：4F-2B 只观察 Bearer 旧 API Key；Studio cookie 留给 4F-3。
6. **上下文不足**
   - 建议：记录 `insufficient_context`，绝不记成 allow。

## 架构边界

新代码：

```text
core/identity_shadow.py      观察器、action map、policy_version
core/identity_shadow_api.py  可选：仅 4F-2C 使用，本阶段不做
core/server.py               中间件接线
identity_shadow_events       新表，独立迁移
```

硬约束：

- 不修改 Legacy Authentication 的 401/403、响应 body/header
- 不调用任何创建身份的函数
- 不复用第二套权限算法；直接调用 `capabilities.decide_permission()`
- Shadow allow/deny 只写入记录
- 记录中不出现 Key/Token/完整 hash
- Shadow 异常不得传播到 Handler

## Change Necessity

`Change Necessity: code-change`

理由：要观察真实请求中的新身份评估差异，仅靠 CLI/文档无法产生请求级证据。
最小边界：新增观察模块、事件表、非阻断中间件；不改认证函数。

## Ripple Signal Triage

- 共享核心：认证中间件、`capabilities.decide_permission()`、Registry 路由
- 消费者：管理页面/API 响应
- 风险：中间件顺序改变响应；Shadow 异常传播；事件表写放大；policy_version 漂移
- 对策：Legacy auth 保持外层；Shadow 内层；响应基线对比；异常注入测试

## TDD Route

```text
Mode: off
Decision: skipped
Reason: 用户未要求 strict TDD；但安全/权限切片仍必须有回归测试和失败注入
```

## 任务分解

### 2B-1 Shadow 记录与保留

- 新表 `identity_shadow_events` + 索引
- retention 清理函数
- 字段：request_id、method、path、legacy_authenticated、legacy_actor、
  identity_state、principal_id、credential_id、role、action、policy_result、
  policy_version、reason、error_class、latency_ms
- 测试：schema 幂等、保留期、无敏感字段

### 2B-2 观察器与权限复用

- `evaluate_shadow()`：Bearer → 4F-2A 已存在 mapping → 状态检查
- action map：管理/策略/Registry → policy action
- 复用 `decide_permission()`；上下文不足返回 `insufficient_context`
- policy_version：canonical permissions hash
- 测试：
  - unknown → `unmapped`，无身份写入
  - revoked/expired/disabled → 对应状态
  - specificity/priority/deny-wins/scope 与 `decide_permission` 一致
  - 记录无 secret

### 2B-3 中间件 Shadow 接线

- Legacy auth 外层；Shadow 内层；异常全捕获
- 增加 `identity.shadow.enabled` 开关，默认 **off**
- 基线响应对比测试：
  - Legacy allow + Shadow allow/deny → 响应完全一致
  - Legacy deny + Shadow 记录 → 仍拒绝
  - Shadow 注入异常 → 响应不变
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
- Legacy 认证响应基线完全不变
- 未知 key/revoked 不写身份表
- 每条 Shadow 记录可追溯到 policy_version
- 无 secret 出现在表或日志
- Windows + Ubuntu CI 实跑通过

## 停止条件

- 4F-2B 只观察，不改变放行结果
- 4F-2C/Admin Login/路由授权/Default Deny 不在本计划内
- 4F-2A 生产映射边界不变
- 生产 PG 路径不受 SQLite 测试修复影响

## 执行路由

- 当前：等待 6 个决策确认
- 确认后：inline 实现，按 2B-1 → 2B-2 → 2B-3 → 2B-4
- 每个切片单独验证，不合并认证切换
