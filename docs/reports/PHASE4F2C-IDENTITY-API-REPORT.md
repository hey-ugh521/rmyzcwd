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

`tests/test_identity_api.py`：34/34

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
- 并发 DB 解析峰值不超过槽位上限
- 超时路径不创建超过槽位的 worker
- 超时后槽位恢复，后台异常计入 stats
- 周期 stats 结构化日志输出
- Bearer-protected stats 诊断面
- 超时路径调用 store.cancel_pg（本地）
- DB 超时 → 500
- PG connect_timeout 传到 psycopg
- PG statement_timeout/thread-local context
- 配置值只接受整数；0 保留；负数/小数/inf/非法值回落默认
- endpoint 200/401/500 后 Legacy 回归
- Shadow 关闭无事件
- Shadow 开启时允许全局 not_evaluated，endpoint 响应不变
- Shadow 写库失败不影响 endpoint 200
- Shadow 求值失败不影响 endpoint 200
- Shadow 失败不影响 endpoint 401/500

完整入口：

```text
Ran 162 tests ... OK
webhook 5/5
```

Python 3.12 / 3.13 本地通过。

CI 证据（公开仓库 run）：

- `ad166db` run `43573074888`：Windows/Ubuntu × 3.12/3.13 四个 job 全 success
- `a1f67e9` run `43573657319`：四个 job 全 success
- `cd4f385` run `43573232641`：Windows 3.12 job 失败，原因是
  `test_permission_version_conflict_and_audit` 同时间戳排序 flaky
  （`['admin-b','admin-a','admin-c']`），与 4F-2C 无关；同一代码后续 run 全绿
- 因此 3.12 已有 CI 通过证据，不再使用“无 CI 证据”表述；flaky 登记到 G-10

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
- 第二次部署（最小 store 补丁 + 新 identity_api）：2026-11-14 15:55:44
- 第二次 Core 重启：2026-11-14 15:56:07（PID 29784）
- 生产 Python：3.12.14；CI 3.12/3.13 四 job 已全绿（见 §6）

部署文件（当前生产）：

| 文件 | 生产 SHA256 前缀 |
|---|---|
| `identity_api.py` | `20620DA8E05A9EC7` |
| `store.py` | `3A20A70BFA52A486` |
| `server.py` | `7F77BB2B77BEBA4F` |

注意：`store.py` 是**最小补丁**，不是仓库完整版本；SQLite WAL 修复仍未进入
生产。`identity_api.py` 与补丁必须成对部署。

本地后续版本（**未部署**）：

| 文件 | 本地 SHA256 前缀 |
|---|---|
| `identity_api.py` | `B4E9622928BA23F2` |
| `store.py` | `E7B07A98F2CB429F` |

本地新增：Bearer-protected `/api/identity/stats`、PG cancel 原型
（`pg_timeouts(cancel_key=...)` / `cancel_pg` / 超时 cancel）。
生产仍是上表部署版本，不含这些改动。

**部署阻断：** 本地 `identity_api.py` 调用 `cancel_key=` 与
`store.cancel_pg()`；生产最小补丁版 `store.py`（`3A20A70B…`）没有这两个
符号。只替换 `identity_api.py` 会让 `/api/identity/me` 返回 500，而
`/api/identity/stats` 仍可能 200。必须先完成 G-01 路径 B（完整 store 收敛）
或重新裁决补丁，不能直接部署本地版本。

**stats 认证口径：** `/api/identity/stats` 只校验配置 Legacy Key，不查询
Credential 状态；credential 被 revoke 后 `/me` 会 401，但 stats 仍可能 200。
它是配置 Key 保护的诊断面，不是身份认证端点；本地未部署。

生产已部署版本已有 per-loop `Semaphore(4)`、超时不释放槽位、PG timeout；
但强制 cancel、stats 诊断面和外部指标仍未进入生产。

> 部署硬依赖：本地 `identity_api._resolve_with_slot` 调用
> `store.pg_timeouts(cancel_key=...)`，超时路径调用 `store.cancel_pg()`。
> 生产最小补丁版 `store.py` 两者都没有；只换 `identity_api.py` 会抛
> `TypeError` 并返回 500。必须先走 G-01 路径 B 或重新裁决补丁。
> 生产 patched `store.py` 与仓库 HEAD 当前差异约 `+171/-19`；历史 214 行是
> 部署前旧版对比值，不能与当前值混用。
>
> 超时叠加：`connect_timeout=5s` + `statement_timeout=5s` 串行，worker
> 最坏约 10s 退出，而 endpoint 5s 已返回 500；槽位占用上界按 10s 计算。
>
> `options` 约束：当前生产 DSN 无 `options`，本次安全；未来若 DSN 添加
> `options`，直接覆盖会丢失原有设置。

备份：

```text
D:\AI\Mobius\core\server.py.bak-20261114-141306
D:\AI\Mobius\core\identity_api.py.bak-4f2c-20261114-155544
D:\AI\Mobius\core\store.py.bak-4f2c-20261114-155544
```

生产配置：

- `config.json` mtime 2026-11-13 12:37:15
- SHA256 前缀 `80F934C4933E0557`
- 顶层无 `identity` 段

生产 `store.py` 与仓库 HEAD 仍不一致：

- 当前生产已打 PG timeout 最小补丁
- 仍缺少 SQLite WAL `_conn_lock` / anchor / `_ClosingConnection` / 跨进程锁修复
- 生产 `db_backend=pg`，PG 路径等价，因此不影响 4F-2C endpoint
- 这属于开发仓库与生产安装分离的 drift，登记到 M3 干净安装 / G-10 前置项；
  本次没有同步完整 `store.py`

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
- 生产 PG 只读验证：
  - `pg_timeouts(statement_timeout_ms=5000)` 内 `SHOW statement_timeout = 5s`
  - 上下文退出后 thread-local 恢复
  - 普通连接 `SHOW statement_timeout = 0`
- `store.pg_timeouts` 存在，`identity_api` 调用路径可用

部署过程中发现 portable PostgreSQL 未运行，Core 启动阻塞；已用配置中的
`pg_ctl` 启动 `pgsql-portable`，恢复后 Core 启动成功。该操作属于服务恢复，
不改变 DSN 或数据库内容。

## 8. 未做

- 未开启 Shadow
- 未做生产 Shadow 重新验证
- 未修复 2B 后续问题（独立立项）
- 4F-2C 已部署有界槽位 + statement timeout + 进程内指标；
  周期 stats 日志用 INFO，但 Core/uvicorn 的 root 仍为 WARNING，
  生产未出现 `identity.me stats`，`stats_snapshot()` 无生产取证路径；
  强制回收/外部指标/生产长稳仍未关闭
  （见 [4F-2C 后续问题](../aegis/plans/2026-11-14-phase4f2c-followups.md)）
- 未启用 Admin Login / 路由授权 / Default Deny
- 未修改 4F-1/2A/2B 冻结内容

## 9. 停止点

4F-2C endpoint 已部署并通过生产只读验证；Shadow 仍关闭。

部署后的生产行为变更仅限新增 `GET /api/identity/me`，没有改变 Legacy 放行。
