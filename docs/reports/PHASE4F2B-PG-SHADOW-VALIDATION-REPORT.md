# Phase 4F-2B PG Shadow Validation 报告（维护窗口）

- 日期：2026-11-13
- 状态：**验证完成；Shadow 已关闭；事件表保留 8 条证据**
- 决策：[ADR-017](../adr/ADR-017-pg-shadow-validation.md)
- 计划：[PG Shadow Validation 执行计划](../aegis/plans/2026-11-13-pg-shadow-validation-plan.md)
- 生产路径：`D:\AI\Mobius`
- 配置备份：`D:\AI\Mobius\config.json.bak-shadow-20261113-235131`

## 1. 隔离窗口

执行前：

- 关闭全部 Edge/Studio 客户端
- `Get-NetTCPConnection -LocalPort 8900` 无 established 连接
- 停止 Gateway、Kokoro、ComfyUI、Memory、PowerContext 等服务
- 保留 Core、NapCat、PostgreSQL

执行中发现并处理：

- 一次 readiness 检查和 stop-all 控制请求产生了事件；在受控请求前清空
  `identity_shadow_events`
- 受控请求集固定为 7 条；`/api/shutdown` 在关闭 Shadow 前额外产生 1 条
  `not_evaluated` 事件

## 2. 受控请求与事件

| # | 请求 | Legacy | Shadow 结果 |
|---|---|---|---|
| 1 | `GET /api/capabilities/permissions` + Bearer | 200 | `legacy_anonymous`（本机只读不认证） |
| 2 | `GET /api/registry/character` + Bearer | 200 | `legacy_anonymous` |
| 3 | `GET /api/services` + Bearer | 200 | `not_evaluated` |
| 4 | `GET /v1/models` + Bearer | 200 | `not_evaluated` |
| 5 | `POST /api/capabilities/permissions` + Bearer + 非法 body | 422 | `evaluated` / `route` / `deny` |
| 6 | `POST /api/capabilities/permissions` + 错误 Bearer | 401 | `legacy_denied` |
| 7 | `GET /api/registry/character` 无 Bearer | 200 | `legacy_anonymous` |

受控 POST 的 `identity`：

```text
principal_id: service-legacy
credential_id: cred_a466692a452b
legacy_authenticated: 1
legacy_actor: api-key
policy_domain: route
policy_result: deny
```

## 3. 独立 PG 查询

通过独立 psycopg 连接读取：

```text
identity_shadow_events = 8
principals = 1
credentials = 1
```

- 事件可见即已提交
- 没有新增 Principal/Credential
- 事件中未出现原始 API Key
- 表结构无 authorization/token/secret 列

## 4. policy_version

所有 `policy_state=ready` 的事件：

```text
b2a37cad7a90d810
```

关闭 Shadow 后没有新增事件。

## 5. 响应兼容

Shadow 开启 vs 关闭：

| 请求 | status | body |
|---|---|---|
| permissions Bearer | 相同 200 | 相同 |
| registry Bearer | 相同 200 | 相同 |
| v1/models Bearer | 相同 200 | 相同 |
| 非法 POST | 相同 422 | 相同 |
| 错误 Bearer | 相同 401 | 相同 |
| registry anonymous | 相同 200 | 相同 |

`/api/services` 状态相同（200），body 因服务启动状态变化而不同；
该请求不是管理路由覆盖项，不作为静态 body 兼容判据。

## 6. 延迟

受控请求示例：

- permissions：约 0.11s
- registry：约 0.11s
- 非法 POST：约 0.11s
- v1/models：约 0.03s
- `/api/services`：约 3.2s（服务探测本身）

Shadow 同步写入在本地受控请求中约几十毫秒级；未做高负载长稳。

事件表字段限制：

- `latency_ms`、`request_id`、`error_class` 当前均未写入（8/8 NULL）
- 延迟是客户端测量，不是事件表字段
- 事件无法按 request_id 与请求一一关联，重复路径只能靠 `ts`
- Shadow 采用 daemon 线程 + 0.25s wait_for；超时后线程可能继续运行，
  生产开启前需评估线程堆积风险

## 7. 故障隔离

生产环境**没有注入 PG 故障**。

写入失败/超时/异常隔离由隔离测试证明：

```text
test_shadow_write_failure_does_not_change_response
test_shadow_timeout_does_not_change_response
test_shadow_exception_does_not_change_response
```

这属于测试证据，不是生产故障注入证据。

## 8. 副作用与清理

- 验证设计期间误创建过 1 条 permission，已立即删除；最终
  `permissions=[]`，但 audit 表保留创建/删除审计
- 为受控验证清空过一次 Shadow Event（此前为 readiness/控制请求产生）
- 最终事件表保留 8 条验证证据，未删除
- 配置已恢复为无 `identity` 段
- 备份 `...bak-shadow-20261113-235131` 是**关闭态**快照；开启态配置没有
  单独留档，窗口参数只能由 ADR-017、计划和事件反推
- 服务已恢复：gateway/comfyui/kokoro ready，langbot down
- Studio/Edge 已关闭，验证后未自动重开

## 9. Exit Criteria

- [x] PG 事件成功写入并提交
- [x] 事件语义与**修正后**的 auth/计划 §5 一致
- [x] policy_version 正确且稳定
- [x] 无新增身份记录
- [x] 关键 Legacy 响应一致（6/7 静态；services 动态不计）
- [x] Shadow 关闭后不再新增事件
- [x] 无凭证秘密泄露
- [ ] Shadow 写入失败不影响认证 —— 隔离测试证据，非生产故障注入
- [x] 已恢复 Shadow 关闭状态
- [x] 报告和 Git 提交

## 10. 结论

生产 PG Shadow 语义验证完成，Shadow 已关闭。

按冻结边界：

- 不扩大到真实用户流量
- 不进入 4F-2C
- 不启用 Default Deny
- 请求级隔离仍是独立议题
