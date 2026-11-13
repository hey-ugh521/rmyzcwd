# Phase 4F-2C 实施报告：`/api/identity/me`

- 日期：2026-11-14
- 状态：**本地实现完成；未部署生产**
- 设计：[2026-11-14-phase4f2c-identity-me-design.md](../aegis/plans/2026-11-14-phase4f2c-identity-me-design.md)
- 前置：[4F-2C 前置检查清单](../aegis/plans/2026-11-14-phase4f2c-preflight.md)

## 1. 范围

只实现：

```text
GET /api/identity/me
Authorization: Bearer <legacy_api_key>
```

不改变 Legacy 认证/放行，不启用 Admin Login、路由授权、Default Deny，不修改
4F-1/2A/2B 的冻结数据模型或 Shadow 行为。

## 2. 文件

| 文件 | 变更 |
|---|---|
| `core/identity_api.py` | 新增只读 endpoint |
| `core/server.py` | 仅 include identity router |
| `tests/test_identity_api.py` | endpoint 契约/只读/隔离测试 |

`server.py` 没有修改 `auth_mw`、Shadow middleware 或任何认证逻辑。

## 3. 接口契约

成功：

```json
200 {
  "ok": true,
  "identity_mode": "legacy",
  "principal": {
    "id": "service-legacy",
    "kind": "machine",
    "role": "service"
  }
}
```

失败：

```json
401 {"ok": false, "error": "unauthenticated"}
500 {"ok": false, "error": "identity_unavailable"}
```

- 401：`WWW-Authenticate: Bearer`
- 500：无 `WWW-Authenticate`
- 200/401/500：`Cache-Control: no-store`
- 不返回 credential.id、hash、prefix、原始 key

## 4. 身份解析

- 直接解析原始 ASGI Authorization header
- 缺失/格式错误/重复 Authorization → 401
- scheme 大小写不敏感
- 允许 scheme 后一个或多个空格
- 不从 Query/Cookie/Body 回退
- 不 trim/大小写转换/截断 token
- 先读取 `configured_legacy_key()` 快照
- 配置缺失 → 500
- token 与配置快照不匹配 → 401
- 调用 `inspect_legacy_api_key()` 只读解析
- 调用后再次检查配置；变化 → 500
- 只读状态：resolved → 200；unmapped/mapping_missing/conflict/revoked/
  expired/principal_disabled → 401

## 5. 只读与隔离

- 不调用 `authenticate_credential()`，不更新 `last_used_at`
- 不调用 `resolve_legacy_api_key()`，不创建/复活身份
- 不写审计、不写 Shadow Event
- 不修改 `request.state.legacy_auth`
- endpoint 200/401/500 后 Legacy 路由响应不变
- Shadow 关闭时不产生 Shadow Event

## 6. 测试

`tests/test_identity_api.py`：14/14

覆盖：

- 成功响应字段
- 缺少/错误/格式错误 Bearer
- scheme 大小写、多空格
- 重复 Authorization
- Query/Cookie 回退拒绝
- 配置缺失 → 500
- 数据库异常 → 500
- 配置请求内变化 → 500
- revoked → 401
- 只读快照：行数与关键字段不变
- endpoint 200/401/500 后 Legacy 回归
- Shadow 关闭无事件

完整入口：

```text
Ran 136 tests ... OK
webhook 5/5
```

Python 3.12 / 3.13 均通过。

## 7. 未做

- 未部署生产
- 未在 PG 生产运行 endpoint
- 未开启 Shadow
- 未修复 2B 后续问题（独立立项）
- 未启用 Admin Login / 路由授权 / Default Deny
- 未修改 4F-1/2A/2B 冻结内容

## 8. 停止点

4F-2C 本地实现完成，等待代码审查。

部署前需要单独批准；生产验证仍按冻结边界执行。
