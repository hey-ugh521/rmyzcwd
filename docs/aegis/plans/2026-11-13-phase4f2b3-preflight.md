# Phase 4F-2B-3 前置检查清单

- 日期：2026-11-13
- 状态：检查清单；**未开始 2B-3 中间件**
- 依据：[2B-3 实施计划](2026-11-13-phase4f2b-implementation-plan.md)、[Shadow 设计稿](2026-11-13-phase4f2b-shadow-design.md)
- 目的：接线前确认所有会影响真实请求的边界已被固定

## A. 已闭合（2B-2 收口）

- [x] `policy_state=unavailable`：策略 DB 读取失败时 `evaluation_state=error`，
      不产出 `evaluated`
- [x] `route_action` 由 HTTP method 决定（GET/HEAD/OPTIONS=read，
      POST/PUT/PATCH/DELETE=write）
- [x] capabilities 路径使用段边界，拒绝 `permissions-evil` 等裸前缀
- [x] 能力调用路径首版 `not_evaluated`，不调用 `decide_permission()`
- [x] `identity_shadow` 不再依赖 `capabilities` 模块，离线策略对比不拉入
      `services/httpx`

## B. 2B-3 接线前必须确认

- [ ] Shadow 外层、Legacy auth 内层；Legacy 仅写只读
      `request.state.legacy_auth`
- [ ] Legacy 拒绝路径 Shadow 仍能执行，并记录 `legacy_denied`
- [ ] Legacy 拒绝路径不调用 Identity Mapping
- [ ] `identity.shadow.enabled` 默认 off；off 时不读策略、不写事件
- [ ] Shadow 写库失败、超时、内部异常不影响 Legacy 响应
- [ ] 响应基线比较包含 status/body/headers/cookies/外部副作用
- [ ] Shadow allow/deny 均不改变放行结果
- [ ] 事件写入字段白名单，无 Authorization/原始 Key/完整 Token hash
- [ ] `policy_state` 写入事件，error/unavailable 不被解释为策略变更
- [ ] `policy_version` 在事件写入时同值；不因请求上下文变化
- [ ] 中间件顺序测试证明 auth 仍先决定 401/403
- [ ] 关闭 Shadow 时现有 114 unit + 5 webhook 全绿
- [ ] Windows/Ubuntu CI 跑过新回归

## C. 2B-3 不进入

- [ ] 不做路由授权
- [ ] 不做 Admin Login
- [ ] 不启用 Default Deny
- [ ] 不做 4F-2C `/api/identity/me`
- [ ] 不改变 4F-2A mapping 边界

## D. 停止条件

2B-3 只允许观察。任何“Shadow deny 阻断请求”或“Shadow allow 跳过 Legacy”
的实现都视为越界，回到设计审查。
