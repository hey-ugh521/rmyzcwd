# Phase 4F-2B-3 报告：Shadow Middleware 接线

- 日期：2026-11-13
- 状态：2B-3 完成；Shadow 默认关闭；未改变 Legacy 认证/放行
- 设计：[4F-2B Shadow 设计稿](../aegis/plans/2026-11-13-phase4f2b-shadow-design.md)
- 前置检查：[2B-3 preflight](../aegis/plans/2026-11-13-phase4f2b3-preflight.md)

## 1. 接线结构

```text
Shadow Observer (outer)
  -> Legacy Authentication (inner, unchanged authority)
       +-- accepted -> handler -> response
       +-- denied   -> 401/403 -> response
  <- Shadow reads legacy_auth after response
       +-- accepted -> evaluate + record
       +-- denied   -> legacy_denied, no mapping
```

- `shadow_mw` 后注册，作为外层 middleware
- `auth_mw` 仍是唯一放行依据
- `auth_mw` 只增加只读 `request.state.legacy_auth`
- Shadow 不修改 response.status/body/headers/cookies

## 2. 配置

默认关闭：

```json
{
  "identity": {
    "shadow": {
      "enabled": false
    }
  }
}
```

开启后：

- 仅处理 `/api` / `/v1` 请求
- 不覆盖路由记录 `not_evaluated`
- 不创建 Principal/Credential
- 不调用 `decide_permission()`

## 3. Legacy auth 观测

`auth_mw` 设置：

```text
authenticated: bool
actor: str | None
reason: accepted | denied | anonymous | not_applicable
```

- 本机只读：`anonymous`
- 本机变更认证通过：`accepted`
- 拒绝：`denied`
- 非 `/api`、`/v1`：`not_applicable`

Legacy 拒绝时 Shadow 仍能执行并写 `legacy_denied`，不调用 4F-2A mapping。

## 4. 失败隔离

- evaluate/record 在 daemon 线程执行
- `wait_for` 0.25s；超时立即返回响应
- evaluate 异常、record 异常、策略 DB 异常全部被捕获
- 不重抛到 Legacy auth / handler
- Shadow 关闭时不创建线程、不读策略、不写事件

## 5. 测试

`tests/test_identity_shadow_middleware.py`：8/8

覆盖：

- Shadow 关闭无写入
- Legacy denied 可观察且不映射身份
- 认证管理路由 evaluated
- Shadow off/on 响应 status/body/headers 完全一致
- evaluate 异常响应不变
- record 写库失败响应不变
- 超时 < 0.55s 返回，响应不变
- Shadow deny 不阻断 Legacy 200

完整入口：

```text
Ran 122 tests ... OK
webhook 5/5
```

Python 3.12 / 3.13 均通过。

## 6. 当前未做

- 4F-2C `/api/identity/me`
- Admin Login
- 路由授权
- Default Deny
- 生产部署
- 生产配置未开启 Shadow

## 7. 停止点

2B-3 到此停止。等待验收后再进入 2B-4 运行证据与冻结。
