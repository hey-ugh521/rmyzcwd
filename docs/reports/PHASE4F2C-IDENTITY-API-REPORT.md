# Phase 4F-2C 实施报告：`/api/identity/me`

- 日期：2026-11-14
- 状态：**已部署生产；Shadow 关闭；未开启真实流量切换**
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

**适用范围：** 以上是 endpoint 自身返回的契约。Core 绑定 `127.0.0.1`；
非回环或被 `auth_mw` 提前拒绝的请求可能得到 Legacy 的
`{"error":"unauthorized"}`，不带本契约 headers。该 Legacy 响应不作为
endpoint 契约，也不要求修改 `auth_mw`。

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
- 同步 DB 解析通过 `run_in_threadpool`，默认 5s 超时；慢 DB 不阻塞事件循环
- Bearer 解析使用 `fullmatch`，尾部换行/控制字符拒绝

## 5. 只读与隔离

- 不调用 `authenticate_credential()`，不更新 `last_used_at`
- 不调用 `resolve_legacy_api_key()`，不创建/复活身份
- 不写审计、不写 Shadow Event
- 不修改 `request.state.legacy_auth`
- endpoint 200/401/500 后 Legacy 路由响应不变
- Shadow 关闭时不产生 Shadow Event

## 6. 测试

`tests/test_identity_api.py`：26/26

覆盖：

- 成功响应字段
- 缺少/错误/格式错误 Bearer
- scheme 大小写、多空格
- 尾部换行/控制字符拒绝
- 重复 Authorization
- Query/Cookie 回退拒绝
- 配置缺失 → 500
- 数据库异常 → 500
- 配置请求内变化 → 500
- revoked / expired / disabled / mapping missing → 401
- 只读快照：行数与关键字段不变
- 慢 DB 不阻塞同一事件循环的其他请求
- 并发 DB 解析峰值不超过 CapacityLimiter 上限
- DB 超时 → 500
- endpoint 200/401/500 后 Legacy 回归
- Shadow 关闭无事件
- Shadow 开启时允许全局 not_evaluated，endpoint 响应不变
- Shadow 写库失败不影响 endpoint 200
- Shadow 求值失败不影响 endpoint 200
- Shadow 失败不影响 endpoint 401/500

完整入口：

```text
Ran 148 tests ... OK
webhook 5/5
```

Python 3.12 / 3.13 本地通过。

CI 当前矩阵是 Windows/Ubuntu × 3.13，**3.12 没有 CI 证据**；报告不把本地
3.12 手跑当作 CI 通过。

## 7. 生产部署

授权范围：**仅 4F-2C endpoint 部署**；不开启 Shadow，不修改 4F-1/2A/2B 行为。
授权与门禁裁决记录：
[PHASE4F2C-DEPLOY-AUTHORIZATION.md](PHASE4F2C-DEPLOY-AUTHORIZATION.md)。

注意：部署前 §E 门禁未全部闭合（B2/B3/B4/C、followups §3 未勾）；本次是
用户明确授权下的例外部署，不构成门禁通过或后续先例。

执行时间：

- 备份：2026-11-14 14:13:06
- portable PostgreSQL 启动：2026-11-14 14:14:43
- Core 重启：2026-11-14 14:16:52（PID 25988）
- 生产 Python：3.12.14；CI 仍为 3.13，无 3.12 CI 证据

部署文件：

| 文件 | 生产 SHA256 前缀 |
|---|---|
| `identity_api.py` | `E3FD63AD51398F9F` |
| `server.py` | `7F77BB2B77BEBA4F` |

注意：上述生产 `identity_api.py` 是部署时版本，**不包含后续本地
CapacityLimiter(4)**；limiter 版本尚未部署。生产 endpoint 仍是旧实现。

备份：

```text
D:\AI\Mobius\core\server.py.bak-20261114-141306
```

生产配置：

- `config.json` mtime 2026-11-13 12:37:15
- SHA256 前缀 `80F934C4933E0557`
- 顶层无 `identity` 段

生产 `store.py` 与仓库 HEAD 不一致：

- 生产是 2026-11-13 19:38 旧版本
- 缺少 SQLite WAL `_conn_lock` / anchor / `_ClosingConnection` / 跨进程锁修复
- 生产 `db_backend=pg`，PG 路径等价，因此不影响 4F-2C endpoint
- 这属于开发仓库与生产安装分离的 drift，登记到 M3 干净安装 / G-10 前置项；
  本次没有同步 `store.py`

生产验证：

```text
GET /api/identity/me + valid key
200 {"ok":true,"identity_mode":"legacy","principal":{"id":"service-legacy","kind":"machine","role":"service"}}

GET /api/identity/me + wrong/missing key
401 {"ok":false,"error":"unauthenticated"}
WWW-Authenticate: Bearer
Cache-Control: no-store
```

- `/api/capabilities/permissions` 仍为 200，body hash 与部署前一致
  `acf335ffe0e6e796`
- Shadow：配置无 `identity` 段，`shadow_enabled()` 隐式返回 False
- `identity_shadow_events` 仍为 8，没有新增
- `principals` 仍只有 `service-legacy`
- `credentials` 1 条，`last_used_at=NULL`
- 服务恢复：gateway/comfyui/kokoro/napcat/postgres ready，langbot down

部署过程中发现 portable PostgreSQL 未运行，Core 启动阻塞；已用配置中的
`pg_ctl` 启动 `pgsql-portable`，恢复后 Core 启动成功。该操作属于服务恢复，
不改变 DSN 或数据库内容。

## 8. 未做

- 未开启 Shadow
- 未做生产 Shadow 重新验证
- 未修复 2B 后续问题（独立立项）
- 4F-2C 本地已加 CapacityLimiter(4)，但该版本未部署；PG timeout/监控仍未关闭
  （见 [4F-2C 后续问题](../aegis/plans/2026-11-14-phase4f2c-followups.md)）
- 未启用 Admin Login / 路由授权 / Default Deny
- 未修改 4F-1/2A/2B 冻结内容

## 9. 停止点

4F-2C endpoint 已部署并通过生产只读验证；Shadow 仍关闭。

部署后的生产行为变更仅限新增 `GET /api/identity/me`，没有改变 Legacy 放行。
