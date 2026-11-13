# PG Shadow Validation 执行计划（维护窗口）

- 日期：2026-11-13
- 状态：**待执行前审查；未开启生产 Shadow**
- 决策：[ADR-017](../adr/ADR-017-pg-shadow-validation.md)
- 约束：方案 A 维护窗口；不做请求级白名单

## 1. 前置条件

任何一项不满足就停止：

- [ ] 生产 Shadow `enabled=false`
- [ ] `identity_shadow_events` 当前行数已记录
- [ ] Studio 页面/浏览器轮询已关闭
- [ ] 其他 `/api`、`/v1` 客户端已确认无请求
- [ ] Gateway 无活跃任务，或已暂停
- [ ] LangBot 保持 down
- [ ] Core 可立即重启
- [ ] 配置和文件备份已完成
- [ ] 验证请求清单已固定
- [ ] 回滚步骤已演练

## 2. 隔离证据

执行前记录：

```powershell
Get-NetTCPConnection -LocalPort 8900 |
  Select-Object State,LocalAddress,RemoteAddress,RemotePort,OwningProcess
```

通过条件：

- 只有验证脚本的本地连接
- 没有 Edge/Studio 轮询连接
- 没有外部 IP 连接
- `/api/tasks` 没有活跃写入

## 3. 基线

在 Shadow 关闭状态记录：

- 每个请求的 status/body hash
- 事件表行数（应为 0）
- Principal/Credential 行数
- policy_version（通过事件或离线计算）
- Core 响应延迟

## 4. 开启 Shadow

仅在维护窗口内：

1. 备份生产 `config.json`
2. 加入：

```json
"identity": {
  "shadow": {
    "enabled": true
  }
}
```

3. 重启 Core
4. 确认：
   - `shadow_enabled() == True`
   - 事件表仍为 0
   - 没有客户端连接

## 5. 预定义请求（执行后修正）

原计划假设“GET + Bearer 会认证并 evaluated”。实际 `auth_mw` 对 loopback
只读请求不认证，因此 GET + Bearer 得到 `legacy_anonymous`，不会进入
Identity Mapping。执行时按以下修正后的清单进行：

| # | 请求 | 实际 legacy | 实际 Shadow |
|---|---|---|---|
| 1 | `GET /api/capabilities/permissions` + Bearer | 200 | `legacy_anonymous` |
| 2 | `GET /api/registry/character` + Bearer | 200 | `legacy_anonymous` |
| 3 | `GET /api/services` + Bearer | 200 | `not_evaluated` |
| 4 | `GET /v1/models` + Bearer | 200 | `not_evaluated` |
| 5 | `POST /api/capabilities/permissions` + Bearer + 非法 body | 422 | `evaluated` / route / deny |
| 6 | `POST /api/capabilities/permissions` + 错误 Bearer | 401 | `legacy_denied` |
| 7 | `GET /api/registry/character` 无 Bearer | 200 | `legacy_anonymous` |

只有第 5 条（合法 Bearer 的变更请求）产生 resolved/evaluated 证据；其余
loopback GET 是匿名只读。

原 §5 的 6 条预期不适用于实际 Legacy 语义；2C 设计必须按本修正表理解。

## 6. PG 证据

使用独立 PG 连接，不使用 Core 连接：

- 事件已提交可见
- 事件数量等于受控请求数（执行时另有 1 条 `/api/shutdown` 控制事件，需单列）
- 每条事件：
  - route_id / action
  - identity_state
  - evaluation_state
  - policy_result
  - policy_state
  - policy_version
  - reason
- 已知字段缺口：`request_id` / `latency_ms` / `error_class` 当前未写入；
  事件与请求无法按 request_id 关联，延迟只能由客户端测量
- 无新增 Principal/Credential
- 事件表无 Authorization/API Key/Token hash 字段或值

## 7. 响应与故障隔离

- 对比 Shadow 关闭/开启的 status/body（同一请求）
- 记录同步写入延迟
- 生产不制造 PG 故障
- 写入失败隔离用隔离环境证明，报告标注为测试证据，不是生产证据

## 8. 关闭与恢复

1. 移除 `identity.shadow`
2. 重启 Core
3. 确认 `shadow_enabled() == False`
4. 再发同一组请求
5. 确认事件表没有新增
6. 恢复 Studio/Gateway/入口
7. 保留事件记录作为验证证据

## 9. 中止条件

- 出现未识别客户端请求
- 事件数超过预定义请求数
- 出现新增 Principal/Credential
- Shadow 改变 Legacy status/body
- Core 重启失败
- 配置无法恢复
- 事件中出现凭证秘密

## 10. 执行后输出

- `PHASE4F2B-PG-SHADOW-VALIDATION-REPORT.md`
- 事件摘要 JSON
- 独立 PG 查询结果
- 回滚/关闭证据
- 生产文件与配置备份路径

## 11. 执行前审查

本计划不开启 Shadow。

需要确认：

- 维护窗口时间
- 是否允许关闭 Studio 浏览器
- 是否允许暂停 Gateway
- 验证请求清单是否接受
- 生产故障注入明确不执行
