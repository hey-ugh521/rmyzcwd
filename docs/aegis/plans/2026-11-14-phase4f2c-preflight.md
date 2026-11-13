# 4F-2C 前置检查清单

- 日期：2026-11-14
- 状态：**设计审查通过；本地实现完成；部署/生产未授权**
- 输入：
  - [4F 设计计划 §8](2026-11-13-phase4f-identity-authorization.md)
  - [Identity Data Model](../current/IDENTITY-DATA-MODEL.md)
  - [PG Shadow Validation 报告](../reports/PHASE4F2B-PG-SHADOW-VALIDATION-REPORT.md)
  - [ADR-017](../adr/ADR-017-pg-shadow-validation.md)

## 0. 状态与证据

| ID | 项 | 状态 | 结论 | 证据 | 判定人 |
|---|---|---|---|---|---|
| A1 | 计划 §5 auth 语义回填 | 已闭合 | loopback GET+Bearer 是 anonymous，不是 evaluated | `5d44ffa`；PG 报告 §2 | 审查者 |
| A2 | 报告 §9 口径 | 已闭合 | 改为“修正后 auth/计划 §5 一致” | `d0f91aa` | 审查者 |
| A3 | `request_id` 链路 | 已闭合（代码） | 代码已接线；本次 NULL 是请求未带 `X-Request-Id` | `server.py`、`identity_shadow.py` | 审查者 |
| A4 | `error_class` 归因 | 已闭合（代码） | 仅 policy_version 异常时写入；正常 NULL 预期 | `identity_shadow.py` | 审查者 |
| A5 | `latency_ms` | 未闭合 | 真死字段：定义但从未写入 | 代码与 PG 8/8 NULL | 审查者 |
| A6 | 求值/写库异常可观测性 | 未闭合 | worker `except Exception: pass` 可能静默失败 | `server.py` 189-193 | 审查者 |
| A7 | Shadow 关闭运行时状态 | 已闭合 | 核心重启于 23:56:34，晚于事件；运行时 false | PG 报告 §1、进程时间 | 审查者 |
| A8 | 4F-2A + 2B 边界 | 已确认 | 双冻结，不得改 mapping/认证/放行 | ADR-017、2B-3 清单 | 审查者 |
| A9 | G-10 | 已确认 | **不是 4F-2C 准入条件**，保持打开 | `ARCHITECTURE-GAP.md:37` | 审查者 |

## A. 已闭合，不再要求 4F-2C 处理

- [x] 计划 §5 已回填 7 条实际请求，并写明原假设错误
- [x] 报告 §9 已改为“修正后 auth/计划 §5 一致”
- [x] `policy_state` 已生效；策略读取失败为 `error/unavailable`
- [x] `action` 跟随 HTTP method
- [x] capabilities 路径使用段边界
- [x] PG 事件写入、独立只读复核、身份不新增、秘密不泄露
- [x] Shadow 已关闭，关闭后无新增事件

这些不再进入 4F-2C 的前置修复。

## B. 4F-2C 设计前必须确认

### B1. `/api/identity/me` 的身份来源

**问题：** `auth_mw` 对 loopback GET 不校验 Bearer，`legacy_auth.authenticated=False`。
如果 endpoint 只读 `request.state.legacy_auth`，永远是匿名，无法验证旧 key
映射 `service-legacy`。

必须在设计稿中裁决一种来源：

- [ ] A. endpoint 自行只读解析 `Authorization: Bearer`，不复用 Legacy 放行结果；
      无有效凭证返回明确 401/anonymous 契约
- [ ] B. 限定非回环请求，但当前 Core 绑定 127.0.0.1，需额外部署假设
- [ ] C. 只接受 4F-3 admin session；则 4F-2C 不能验证 legacy key
- [ ] D. 其他，必须写明

设计稿当前选择 **A**，待审查：
[`/api/identity/me` 设计](2026-11-14-phase4f2c-identity-me-design.md)。

2B 遗留问题：
[2B 后续问题清单](2026-11-14-phase2b-followups.md)。

同时必须写清：

- [ ] 未认证/未知 key/revoked/disabled 的返回语义
- [ ] 不返回 credential hash/prefix 之外的敏感字段
- [ ] 不创建 Principal/Credential
- [ ] endpoint 不是授权入口，不改变 Legacy 放行
- [ ] 复用 [4F 设计计划 §8](2026-11-13-phase4f-identity-authorization.md) 与
      [Identity Data Model](../current/IDENTITY-DATA-MODEL.md) 的接口契约

### B2. 事件关联与可观测性

- [x] `request_id` 代码链路已存在；不是死字段
- [ ] 下次受控窗口每条请求必须带 `X-Request-Id`，事件可按 request_id 关联
- [ ] `latency_ms`：4F-2C 决定“写入真实耗时”或“删除字段”，不能继续定义不用
- [ ] 求值期异常：是否写 `error_class`/事件/日志，必须在 4F-2C 明确
- [ ] 写库期异常：是否重试、丢弃、计数，必须可观测
- [ ] 不允许 `except Exception: pass` 继续作为唯一异常处理

### B3. Shadow 工作线程生命周期

- [ ] daemon 线程 + 0.25s wait_for 的上限与回收策略
- [ ] 超时后线程继续运行时的堆积风险与限流
- [ ] Shadow 关闭后无遗留线程
- [ ] 写入失败/超时仍不影响 Legacy

### B4. 配置与审计

- [ ] 下次窗口开启态配置必须有脱敏快照；2B 关闭态备份不能补齐
- [ ] 记录窗口、请求清单、回滚命令
- [ ] 清理控制请求产生的 Shadow Event 需授权/记录
- [ ] 误创建 permission 的审计痕迹保留
- [ ] 审计表与 Shadow Event 不混用

## C. 响应与故障判据

- [ ] 静态响应按 status/body/headers/cookies 比较
- [ ] 当前 PG 报告只采了 status/body hash；headers/cookies 基线**尚无证据**，
      下次窗口必须补采，不能回填为已通过
- [ ] `/api/services` 是受控清单内的动态项，不纳入静态判据
- [ ] `/api/status` **不在这 7 条受控清单内**，不得用它的动态 body 作为兼容证据
- [ ] 生产不做故障注入
- [ ] 写入失败隔离只用隔离测试证明，标注测试证据
- [ ] Shadow allow/deny 不改变 Legacy 放行

## D. 边界与停止条件

### 冻结边界

- [ ] 4F-2A Legacy Mapping 不改
- [ ] 4F-2B Shadow 行为/认证/放行不改
- [ ] 不启用 Admin Login、路由授权、Default Deny
- [ ] G-10 不是 4F-2C 准入条件

### 越界即停

- [ ] Shadow allow/deny 影响 Legacy 结果
- [ ] `/api/identity/me` 改变认证边界
- [ ] 引入新的身份写入路径
- [ ] 为 4F-2C 修改已冻结的 2B 代码而未单独批准
- [ ] 未裁决 B1 身份来源就开始实现
- [ ] 用“Report 绿灯”替代缺陷修复或证据补齐

## E. 4F-2C 准入

- [x] B1 身份来源已裁决（方案 A）
- [ ] B2 关联/可观测性方案已确认
- [ ] B3 线程生命周期已确认
- [ ] B4 配置/审计要求已确认
- [ ] C 响应基线补采或明确不在 4F-2C 范围
- [x] 4F-2C 设计审查通过
- [x] 冻结边界未被修改

未满足项不进入部署；本地实现完成不构成生产授权。本清单不是 4F-2C 设计稿，
也不授权修改生产。
