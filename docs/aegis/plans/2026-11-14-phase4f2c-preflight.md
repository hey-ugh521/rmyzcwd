# 4F-2C 前置检查清单

- 日期：2026-11-14
- 状态：**审查清单；未开始 4F-2C 设计/实现**
- 输入：4F-2B PG 验证报告、ADR-017、审查发现
- 原则：4F-2C 不得改变 4F-2B 冻结的认证/放行行为

## A. 必须修正的口径

### 1. auth 语义对齐

- [ ] 明确记录：loopback GET + Bearer 是 `legacy_anonymous`，不是
      `authenticated`
- [ ] `evaluated` 只来自 Legacy auth 的 `accepted` 请求
- [ ] 4F-2C 设计不得假设所有 Bearer 请求都进入 Identity Mapping
- [ ] 计划/报告中的请求预期与 auth_mw 真实行为一致
- [ ] 计划 §5 勘误已回填

### 2. 事件可关联性

- [ ] `request_id` 写入事件，或明确不做的理由
- [ ] `latency_ms` 由 Core 测量并写入，或删除死字段
- [ ] `error_class` 在 Shadow 异常时写入
- [ ] 重复 path 的事件可区分
- [ ] 4F-2C 验收不能依赖事件表无法复现的客户端延迟

### 3. Shadow 工作线程生命周期

- [ ] 明确 daemon 线程 + 0.25s 超时的上限
- [ ] 超时后线程是否继续、如何回收/限流
- [ ] 生产开启前做线程堆积/长稳测试
- [ ] Shadow 关闭后无遗留线程
- [ ] 写入失败/超时仍不影响 Legacy

## B. 4F-2C 身份接口契约

- [ ] `/api/identity/me` 的身份来源明确：
  - Bearer legacy
  - Studio cookie/admin session
  - 其他
- [ ] 未认证/未知 key/revoked 的返回语义
- [ ] 不返回 credential hash/prefix 之外的敏感字段
- [ ] 不创建 Principal/Credential
- [ ] 4F-2C 不启用 Admin Login / 路由授权 / Default Deny

## C. 配置与审计

- [ ] 开启态配置有单独留档（脱敏），不只保留关闭态备份
- [ ] 记录维护窗口、请求清单、回滚命令
- [ ] 清理控制请求产生的 Shadow Event 需被授权/记录
- [ ] 误创建 permission 的审计痕迹保留，不删除
- [ ] 审计表与 Shadow Event 不混用

## D. 响应与故障

- [ ] 静态响应兼容按 status/body/headers/cookies 比较
- [ ] 动态 endpoints（`/api/services`、`/api/status`）单列，不纳入静态判据
- [ ] 生产不做故障注入
- [ ] 写入失败隔离用隔离环境证明，标注测试证据
- [ ] Shadow allow/deny 不改变 Legacy 放行

## E. 准入

- [ ] 4F-2C 设计审查通过
- [ ] 4F-2B 冻结边界未被修改
- [ ] 上述 A/B/C/D 项有明确结论
- [ ] 未满足项不进入实现

本清单不是 4F-2C 设计稿，也不授权修改生产。
