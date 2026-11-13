# Phase 4F-2B-4 报告：运行证据与冻结

- 日期：2026-11-13
- 状态：2B-4 完成；4F-2B 冻结；**未开启生产 Shadow**
- 环境：隔离 SQLite + TestClient；不是生产流量
- 脚本：[tests/shadow_evidence.py](../../tests/shadow_evidence.py)

## 1. 运行方式

```powershell
python tests/shadow_evidence.py
```

脚本：

- 创建临时 SQLite DB
- 初始化 identity / capabilities / shadow schema
- 创建 `service-legacy` mapping
- Shadow 关闭时采集 baseline
- Shadow 开启后发送同一组请求
- 比较响应，读取 Shadow Event，最后恢复配置并关闭 anchor

## 2. 请求

| method | path | legacy 预期 |
|---|---|---|
| GET | `/api/capabilities/permissions` | 200，Bearer 通过 |
| GET | `/api/shadow-not-covered` | 404，未覆盖路由 |
| POST | `/api/capabilities/permissions` | 401，错误 key |

## 3. 实测结果

```text
requests: 3
shadow_events: 3
principals_before: 1
principals_after: 1
no_identity_write: true
```

事件摘要：

```json
[
  {
    "path": "/api/capabilities/permissions",
    "identity_state": "resolved",
    "evaluation_state": "evaluated",
    "policy_domain": "route",
    "policy_result": "deny",
    "policy_state": "ready"
  },
  {
    "path": "/api/shadow-not-covered",
    "identity_state": null,
    "evaluation_state": "not_evaluated",
    "policy_domain": "none",
    "policy_result": "not_evaluated",
    "policy_state": "not_required"
  },
  {
    "path": "/api/capabilities/permissions",
    "identity_state": "legacy_denied",
    "evaluation_state": "insufficient_context",
    "policy_domain": "none",
    "policy_result": "insufficient_context",
    "policy_state": "ready"
  }
]
```

响应对比：

```text
3/3 status_same = true
3/3 body_same   = true
```

说明：

- Shadow 开启前后，Legacy 响应状态与 body 一致
- Legacy 拒绝路径 Shadow 仍执行，并记录 `legacy_denied`
- 未覆盖路由记录 `not_evaluated`
- 管理路由记录 `evaluated`，service 角色被 Route Policy deny
- Shadow 没有新增 Principal

## 4. 冻结状态

4F-2B 冻结：

- Shadow 默认关闭
- 生产未部署、未开启
- 无真实 HTTP 授权切换
- 无 Admin Login
- 无 Default Deny
- 4F-2A mapping 边界不变

## 5. 已知限制

- 运行证据来自隔离 TestClient，不是生产流量
- 生产 PG 下的 Shadow Event / 中间件尚未实跑
- 未做 24h/长稳运行
- Shadow 事件仅保留策略/身份观察，不是安全审计
- `2B-4` 不扩展功能

## 6. 后续

4F-2B 到此冻结。若要继续：

1. 先做生产 PG + Shadow 关闭状态的部署验证
2. 再讨论是否开启 Shadow
3. 4F-2C `/api/identity/me` 必须另立阶段
