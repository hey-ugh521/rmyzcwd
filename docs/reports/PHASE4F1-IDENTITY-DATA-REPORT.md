# Phase 4F-1 实施报告：身份数据模型与凭证生命周期

- 日期：2026-11-13
- 状态：4F-1 完成；未改变 HTTP 认证/路由授权行为
- 设计审查：[Phase 4F 设计稿](../aegis/plans/2026-11-13-phase4f-identity-authorization.md)
- 数据契约：[Identity Data Model Contract](../current/IDENTITY-DATA-MODEL.md)
- 代码提交：`dbdd784`

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

- 只存 `secret_hash`（scrypt）
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

测试：

```text
mock _audit 抛错
-> create_credential 失败
-> credentials 表没有残留行
```

同时把 4E 的 policy mutation 审计改为同事务：

- `capability.permission.set/delete`
- `capability.provider.set`
- audit 写入失败会回滚权限修改

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
| 哈希算法/版本可识别 | `scrypt-v1` |
| Bootstrap 一次性 token | 通过 |
| Token 只显示一次 | CLI 输出一次 |
| Token 哈希落库 | 通过 |
| 过期 token 无法消费 | 通过 |
| 并发消费单赢家 | 通过（两线程） |
| 重启后消费状态不丢 | DB 状态；重新连接可验证 |
| 现有 API 路由行为不变 | 认证/路由测试通过 |
| 旧 API_KEY 未接入新表 | 未映射；不会成为 admin |
| 数据库迁移可回滚/恢复 | 纯新增表；不依赖降级删数据 |

## 6. 测试

| 项目 | 结果 |
|---|---|
| `test_identity.py` | 9/9 |
| `test_capabilities.py` | 16/16 |
| `test_registry_core_auth.py` | 8/8 |
| `test_registry_migration.py` | 6/6 |
| `test_registry_runtime.py` | 5/5 |
| `test_voice_assets.py` | 4/4 |

## 7. 当前限制

- `python -m core.identity_cli` 是当前 CLI 入口；尚未编译进 `MobiusCLI.exe`
- 尚无 HTTP 登录/消费 token 接口（4F-2/4F-3）
- 尚无路由授权
- 角色矩阵仍未冻结
- 默认拒绝迁移未开始
- 4F-1 尚未部署到生产；避免无必要地重启 live Core
- `bootstrap_admin` 在已存在任何 admin（含禁用）时拒绝，避免 bootstrap 成为重建管理员后门；禁用 admin 的恢复路径仍需 4F-2 明确
- 审计记录仍无 UI；4G 提供筛选页

## 8. 停止点

4F-1 到此停止。

等待 4F-2（认证映射 + shadow）测试结果，再进入 4F-3 路由授权。
