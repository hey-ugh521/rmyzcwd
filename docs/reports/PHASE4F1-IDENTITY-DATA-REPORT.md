# Phase 4F-1 实施报告：身份数据模型与凭证生命周期

- 日期：2026-11-13
- 状态：4F-1 完成并已把身份数据层部署到生产；未改变 HTTP 认证/路由授权行为
- 设计审查：[Phase 4F 设计稿](../aegis/plans/2026-11-13-phase4f-identity-authorization.md)
- 数据契约：[Identity Data Model Contract](../current/IDENTITY-DATA-MODEL.md)
- 代码提交：`dbdd784`、`a94bc55`（条件验收加固）

## 1. 范围

按批准范围只做：

- Principal
- Credential
- Admin Session
- Bootstrap / Recovery token
- 哈希、撤销、过期、轮换
- CLI
- 同事务审计

**不做**：

- HTTP 认证中间件改造
- 路由权限校验
- Studio 登录页
- Default Deny 切换
- 审计 UI
- Core 进程托管

## 2. 数据模型

### principals

- `id` 稳定
- `kind`：human / machine / local
- `role`：admin / operator / service（v1 单一静态角色）
- `enabled`
- 禁用后：
  - Credential 无法认证
  - Admin Session 无法认证
  - 未消费 bootstrap/recovery token 失效

### credentials

- 只存 `secret_hash`（salted sha256；高熵 token）
- `lookup_prefix` 唯一
- 支持 `expires_at`、`revoked_at`
- `rotated_from` 只引用旧 Credential ID
- Secret 只在创建时返回一次

### admin_sessions

- `token_hash`、`csrf_hash` 只存哈希
- CSRF 使用服务端同步器
- 只有启用的 admin 可创建
- 过期、撤销、Principal 禁用均失效

### bootstrap_tokens

- purpose：bootstrap / recovery
- 一次性消费
- 并发消费单赢家
- 过期/消费状态持久化

## 3. CLI

```text
python -m core.identity_cli bootstrap-admin
python -m core.identity_cli recover-admin
python -m core.identity_cli list-principals
python -m core.identity_cli create-credential --principal svc-qq --kind service_token
python -m core.identity_cli rotate-credential --credential cred_xxx
python -m core.identity_cli revoke-credential --credential cred_xxx
python -m core.identity_cli disable-principal --principal admin-1
python -m core.identity_cli purge-sessions
```

Secret / token 只显示一次；CLI 不写日志。

## 4. 审计事务一致性

身份写操作与 `audit` 写入在同一个 DB 事务中：

- principal create/disable
- credential create/revoke/rotate
- bootstrap create/consume
- session create/revoke/expire

测试覆盖：

```text
mock _audit 抛错
-> create_credential 失败
-> credentials 表没有残留行
```

另外增加真实数据库回滚测试：SQLite trigger 阻断 audit 插入，
`create_credential` 失败后 credentials 表无残留。

同时把 4E 的 policy mutation 审计改为同事务：

- `capability.permission.set/delete`
- `capability.provider.set`
- audit 写入失败会回滚权限修改

## 4.1 Recovery 语义

- `bootstrap_admin` 在任何 admin 存在时拒绝
- `recover_admin` 默认选择现有 admin（含禁用）
- 消费 recovery token 时重新启用目标 admin
- 旧 Credential / Admin Session 保持失效，不恢复
- 可选 `--create-new` 显式创建新 admin
- Recovery token 与 bootstrap token 共用原子消费逻辑

## 4.2 外键与迁移

新建表使用：

```text
Credential/Session/Token -> Principal: RESTRICT
Credential.rotated_from -> Credential: SET NULL
Audit -> Principal: 无级联
```

测试：

- 删除有 Credential 的 Principal 被 FK 拒绝
- 轮换后删除旧 Credential，新凭证 rotated_from 变为 NULL
- `init_schema()` 可重复执行，不自动创建 admin

## 4.3 Bootstrap 初始化原子性

- Bootstrap 的 admin 检查、Principal 创建、Token 创建、审计写入合并为单一事务。
- SQLite 使用 `BEGIN IMMEDIATE`。
- PostgreSQL 使用 `pg_advisory_xact_lock`，避免空查询无法锁行的问题。
- 审计在 token 创建阶段失败时，Principal 和 Token 一起回滚。
- `recover_admin(create_new=True)` 使用同样的原子创建原则。
- 测试：
  - 两个线程同时 `bootstrap_admin()` → 1 admin / 1 token
  - SQLite trigger 阻断 `identity.bootstrap.create` → 0 admin / 0 token

## 4.4 全局 SQLite 外键与 kind-role

- `store.conn()` 现在统一执行 `PRAGMA foreign_keys=ON`，不再依赖测试或 Identity 模块手动开启。
- Credential kind 收紧为 `api_key | service_token`；Bootstrap/Recovery 只属于 `bootstrap_tokens.purpose`。
- kind-role 合法组合：
  - human: admin/operator
  - machine: service
  - local: admin/operator
- Admin Session 认证和 CSRF 校验都会重新检查 `role=admin`。

## 5. 验收结果

| 验收 | 结果 |
|---|---|
| Principal/Credential/Admin Session 表 | 已建立 |
| FK / 唯一约束 | 已建立 |
| Principal 禁用使 Credential/Session/Token 失效 | 通过 |
| Credential 不能跨 Principal | FK 约束 + 无更新入口 |
| Secret 只在创建时返回一次 | 通过 |
| 数据库不存明文 Secret | 通过 |
| 撤销/过期/轮换 | 通过 |
| 旧 Credential 轮换后失效 | 通过 |
| 哈希算法/版本可识别 | `sha256-v1` |
| Bootstrap 一次性 token | 通过 |
| Token 只显示一次 | CLI 输出一次 |
| Token 哈希落库 | 通过 |
| 过期 token 无法消费 | 通过 |
| 并发消费单赢家 | Bootstrap 与 Recovery 均通过（两线程） |
| Bootstrap 初始化并发 | 两个线程只有一个成功，最终 1 admin / 1 token |
| Bootstrap 审计失败回滚 | trigger 阻断后 0 admin / 0 token |
| 全局 SQLite FK | `store.conn()` 自动开启，无需手动 PRAGMA |
| Credential kind / token purpose 分离 | `api_key/service_token` vs `bootstrap/recovery` |
| kind-role 组合 | machine admin 被拒绝 |
| Admin Session role 复查 | 角色改为 operator 后 session/CSRF 均失败 |
| 禁用 admin 恢复 | Recovery token 消费后重新启用原 Principal，旧凭证/会话仍失效 |
| 真实 DB 审计回滚 | SQLite trigger 阻断 audit 后凭证写入回滚 |
| FK 删除策略 | Principal RESTRICT，rotated_from SET NULL 通过 |
| 重启后消费状态不丢 | DB 状态；重新连接可验证 |
| 现有 API 路由行为不变 | 认证/路由测试通过 |
| 旧 API_KEY 未接入新表 | 未映射；不会成为 admin |
| 数据库迁移可回滚/恢复 | 纯新增表；不依赖降级删数据 |

## 6. 测试

| 项目 | 结果 |
|---|---|
| `test_identity.py` | 21/21 |
| `test_capabilities.py` | 16/16 |
| `test_registry_core_auth.py` | 8/8 |
| `test_registry_migration.py` | 6/6 |
| `test_registry_runtime.py` | 5/5 |
| `test_voice_assets.py` | 4/4 |

## 7. 生产部署

4F-1 已热更新：

- `core/identity.py`
- `core/identity_cli.py`
- `core/server.py`（启动时创建身份表）
- `core/capabilities.py`（策略变更与审计同事务）

生产验证：

```text
python -m core.identity_cli list-principals
{
  "principals": []
}
```

- 身份表已创建
- 早期空表已重建为显式 FK schema：
  - Credential/Session/Token → Principal `ON DELETE RESTRICT`
  - `rotated_from` → Credential `ON DELETE SET NULL`
- 没有创建 admin principal / bootstrap token
- 现有 API、Studio、能力调用未切换认证路径

## 8. 当前限制

- `python -m core.identity_cli` 是当前 CLI 入口；尚未编译进 `MobiusCLI.exe`
- 尚无 HTTP 登录/消费 token 接口（4F-2/4F-3）
- 尚无路由授权
- 角色矩阵仍未冻结
- 默认拒绝迁移未开始
- `bootstrap_admin` 在存在任何 admin（含禁用）时拒绝；`recover_admin` 默认恢复原 admin 并重新启用主体，不恢复旧凭证
- 生产身份表已用空表重建，显式 FK 已验证
- 审计记录仍无 UI；4G 提供筛选页

## 9. 停止点

4F-1 到此停止。

等待 4F-2（认证映射 + shadow）测试结果，再进入 4F-3 路由授权。
