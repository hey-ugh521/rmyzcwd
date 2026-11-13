# Identity Data Model Contract (Phase 4F-1)

- 状态：**Frozen / 4F-1 accepted**
- 日期：2026-11-13
- 设计审查：[Phase 4F design](../aegis/plans/2026-11-13-phase4f-identity-authorization.md)
- 范围：只定义数据事实与生命周期；**不改变现有 HTTP 认证/路由行为**

## 1. Principal

```text
principals
  id             稳定 ID，创建后不变
  kind           human | machine | local
  display_name
  role           admin | operator | service
  enabled        0/1
  created_at
  metadata_json
```

约束：

- v1 一个 Principal 只有一个静态 Role。
- kind-role 组合受约束：

| kind | 允许 role |
|---|---|
| human | admin / operator |
| machine | service |
| local | admin / operator |

机器身份默认不能成为 admin；如未来需要，必须作为显式设计新增 kind 或策略，而不是两个字段独立校验的副作用。

- Principal 禁用后：
  - 所有 Credential 立即不可认证；
  - 所有 Admin Session 立即不可认证；
  - 未消费的 bootstrap/recovery token 立即失效。
- Principal ID 不作为 secret；只用于归因。

## 2. Credential

```text
credentials
  id
  principal_id
  kind           api_key | service_token
  lookup_prefix  16 hex（64 bit），用于查找
  secret_hash    salted sha256（高熵随机 token；人类密码另行 scrypt/argon2）
  hash_algo      sha256-v1
  expires_at
  revoked_at
  last_used_at
  created_at
  rotated_from   旧 Credential ID
```

约束：

- Secret 只在创建时返回一次；数据库不存明文。
- `rotated_from` 只引用旧 ID，不保存旧 Secret。
- 不可把 Credential 改挂到其他 Principal。
- 撤销、过期、Principal 禁用均使认证失败。
- `last_used_at` 是运行时状态，认证不依赖它；更新节流 60s，避免写竞争。

## 3. Admin Session

```text
admin_sessions
  id
  principal_id
  token_hash
  csrf_hash
  created_at
  expires_at
  last_seen_at
  revoked_at
```

约束：

- Session token 只存哈希。
- CSRF 采用服务端同步器：DB 存 `csrf_hash`，登录时 CSRF 明文只下发一次，客户端用于提交。
- 只有启用的 admin Principal 可以创建管理会话。
- 认证管理会话时必须重新检查 Principal 的 `enabled` 和 `role=admin`；CSRF 校验同样要求 session 仍对应有效 admin。
- 过期/撤销/Principal 禁用后失效。

## 4. Bootstrap / Recovery Token

```text
bootstrap_tokens
  id
  lookup_prefix
  token_hash
  hash_algo
  purpose        bootstrap | recovery
  principal_id
  expires_at
  consumed_at
  created_at
```

约束：

- 一次性消费；并发消费只能一个成功。
- Token 只显示一次，只存哈希。
- bootstrap：首次创建 admin principal 并生成 token；只要存在任何 admin（含禁用）就拒绝。
- bootstrap 的“检查 admin、创建 Principal、创建 Token、写审计”必须在**同一个事务**中：
  - SQLite 使用 `BEGIN IMMEDIATE`
  - PostgreSQL 使用事务级 advisory lock
  - 并发初始化只能有一个成功
  - token 创建阶段审计失败必须回滚 Principal 和 Token
- recovery：为现有 admin 生成 token；默认恢复原 Principal，CLI 可显式 `--create-new`。
- recovery token 消费时重新启用目标 admin，但**不恢复旧 Credential/Session**。
- Token 消费状态持久化；重启 Core 不会重置。
- 恢复不要求无条件 Windows elevated；是否提权取决于安装目录/数据库权限。
- 恢复操作必须审计；token 消费后失效。
- 只能通过本机 CLI 生成，不允许网络 API 重置。

## 5. 外键与删除策略

| 关系 | 策略 |
|---|---|
| Credential → Principal | RESTRICT |
| Admin Session → Principal | RESTRICT |
| Bootstrap Token → Principal | RESTRICT |
| Credential.rotated_from → Credential | SET NULL |
| Audit → Principal | 逻辑归因；4F-1 不建立物理外键，不级联删除 |

理由：

- Principal 禁用是“主体不可用”，不删除数据；认证时检查 `enabled`。
- 删除 Principal 不应级联删除凭证、会话、Token 或审计。
- 轮换历史只保留 ID 引用，不保留旧 Secret。

## 6. 迁移约束

- `init_schema()` 必须可重复执行。
- 身份表是纯新增表，不自动生成 admin。
- 不把旧 `api_key`、Studio cookie 或旧权限表映射成 Principal。
- DDL 在同一事务中执行；失败不应留下半初始化状态。
- 已有 4F-1 生产表如果由早期版本创建，4F-2 部署前需要用空表重建或显式迁移 FK；不允许静默保留错误级联策略。
- 身份数据回滚不能依赖删除表；安全数据恢复应使用备份。

## 7. 事务与审计契约

- 身份数据变更与 audit 写入**同一个 DB 事务**。
- audit 失败 → 整体回滚；不允许“修改成功但审计缺失”。
- 审计 detail 禁止写入 token、secret、hash、CSRF。
- 动作：

```text
identity.principal.create/disable
identity.credential.create/revoke/rotate
identity.bootstrap.create/consume
identity.session.create/revoke/expire
```

## 8. 角色矩阵状态

4F-1 只落数据，不启用路由授权。角色矩阵暂不冻结；待 4F-2/4F-3 实际测试后冻结。

已确认的方向：

- 拆分 `runtime.read` / `runtime.task.cancel` / `runtime.service.control` / `runtime.provider.control`
- `audit.read` 初版只给 admin
- operator 不默认获得其他主体任务取消权
- `capability.invoke` 还要经过 Capability Policy Engine 二次判断
- `studio-local` 不是 Principal，只是 Admin Session 的来源

## 9. 4F-1 不包含

- HTTP 认证中间件改造
- `/api/identity/me`
- Studio 登录页
- 路由权限校验
- Default Deny 切换
- 审计 UI
- Core 进程托管

## 10. CLI

```text
python -m core.identity_cli bootstrap-admin
python -m core.identity_cli recover-admin
python -m core.identity_cli list-principals
python -m core.identity_cli map-legacy-key
python -m core.identity_cli create-credential --principal svc-qq --kind service_token
python -m core.identity_cli rotate-credential --credential cred_xxx
python -m core.identity_cli revoke-credential --credential cred_xxx
python -m core.identity_cli disable-principal --principal admin-1
python -m core.identity_cli purge-sessions
```

Secret/token 只在命令输出中出现一次；CLI 不写日志文件。

## 11. Legacy Identity Mapping (4F-2A)

旧 API_KEY 只允许映射到固定机器主体：

```text
configured core.api_key
  -> principal: service-legacy
  -> kind: machine
  -> role: service
  -> credential lookup_prefix = sha256(api_key)[:16]
  -> credential kind: service_token
```

约束：

- 只映射当前 `core.api_key`；未知 key 返回 None，不创建 Principal/Credential。
- 同一 key 每次映射到同一 Principal/Credential。
- 不同 key 使用不同 lookup_prefix / Credential。
- 绝不映射为 admin。
- `service-legacy` 已存在但 kind/role 不是 machine/service → 拒绝，不覆盖。
- 映射创建与审计同一事务；失败回滚。
- 4F-2A 只建立数据映射，不接入 HTTP 认证、不改变请求放行结果。
- 映射不是认证：未来认证管线仍需独立验证 Credential 和授权。

## 12. 验收

- Secret 不落库；哈希算法/版本可识别
- 撤销、过期、轮换生效
- Principal 禁用使 Credential/Session/未消费 Token 失效
- Bootstrap 并发消费单赢家
- Bootstrap/Recovery 审计
- 现有 API 路由行为不变
- 旧 API_KEY 未接入新表，不会被误认为 admin
