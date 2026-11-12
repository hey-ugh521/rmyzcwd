# Phase 0.1 验收报告：Registry Hardening

- 日期：2026-11-13
- 状态：完成，等待人工审查；未进入 Phase 1
- 基准：[Capability Registry 架构契约 v2](../aegis/plans/2026-11-13-capability-registry-contract.md)
- ADR：[ADR-010 Registry Resource Lifecycle](../adr/ADR-010-registry-lifecycle.md)
- 代码提交：`ec03a24`
- 上一阶段：Phase 0 提交 `005a545` / `c513e9c`

## 1. 范围遵守

仅做 Phase 0 边界加固：

- 强制状态变更 CAS
- PostgreSQL 双连接并发契约
- 真实 Core 认证集成测试
- 不变量复核与 RequestContext 深度冻结
- 文档/ADR 更新

未做：

- 未开发 UI
- 未接入 Agent Runtime、Gateway、TTS、ComfyUI
- 未做 Legacy 资源迁移
- 未进入 Phase 1
- 未提前实现 Workflow 四层安全验证

## 2. P0.1-01 状态变更 CAS

`disable` / `enable` / `archive` / `delete` 现在都必须提供 `expected_revision`：

- 缺失：`422 expected_revision_required`
- 版本不匹配：`409 revision_conflict`
- 并发状态变更：一个成功，另一个 `409 invalid_status_transition`

状态迁移收紧：

- `disable`: `active -> disabled`
- `enable`: `disabled -> active`
- `archive`: `active|disabled -> archived`
- `delete`: `active|disabled|archived -> deleted`
- 已删除资源再次删除返回冲突

测试：

- `test_state_change_requires_expected_revision`
- `test_concurrent_disable_has_one_winner`
- 既有 disable/enable/archive 测试已改为显式传 expected

## 3. P0.1-02 PostgreSQL 并发契约

新增 8 个 PostgreSQL 契约测试，使用独立临时数据库，Service 每次调用创建独立连接：

- 双连接并发 Update 单赢家
- 双连接并发 Publish 单赢家
- Reference 创建 vs 资源删除竞争
- 重复 Reference 拒绝
- Publish 失败时发布指针与 Revision 状态原子回滚
- Reference 失败时引用插入原子回滚

事务失败通过注入 `_audit` 异常触发；事务回滚后：

- `published_revision` 不变
- 目标 Revision 仍为 `draft`
- 只有一个 published Revision
- 失败的 Reference 不存在，目标资源可正常删除

结果：**8/8 通过**。

## 4. P0.1-03 Core 认证集成

新增 `tests/test_registry_core_auth.py`，使用真实 `core.server.app` 和现有 `auth_mw`：

- 未认证 mutation -> 401
- Bearer -> 200，审计 actor = `api-key`
- Cookie -> 200，审计 actor = `local-session`
- 回环只读 -> 200
- 不存在 `/registry/*` 绕过路径
- 错误响应不包含 `Traceback`、`sqlite`、`D:\`、`postgresql://` 或测试 key

actor 现在由 Core 认证中间件写入 `request.state.actor`；`registry_api` 忽略请求 body 的 `actor`，审计只使用认证上下文。

结果：**6/6 通过**。

## 5. P0.1-04 不变量复核

已补强：

| 不变量 | 保障 |
|---|---|
| 每个资源最多一个 published Revision | partial unique index `ux_registry_published_one` |
| published_revision 指向有效 Revision | SQLite/PG trigger |
| Revision snapshot 不可变 | SQLite/PG update/delete trigger |
| Rollback 不修改历史 snapshot | 生命周期测试 |
| RequestContext 不可被调用方修改 | 深冻结 `MappingProxyType` + tuple |
| checksum 确定性 | `sort_keys=True` + 紧凑分隔符 + UTF-8；顺序无关测试 |
| Reference 语义 | 复合外键 + 事务行锁 + 引用/删除竞争测试 |

SQLite 生命周期测试：**16/16 通过**。

## 6. 回归

- `tests/test_qq_adapter.py`: **52/52**
- `tests/test_qq_webhook.py`: **5/5**
- `tests/test_registry_api.py`: **8/8**
- `tests/test_registry_lifecycle.py`: **16/16**
- `tests/test_registry_core_auth.py`: **6/6**
- PostgreSQL contract: **8/8**

## 7. 文件变更

- `core/registry.py`
- `core/registry_api.py`
- `core/server.py`
- `docs/adr/ADR-010-registry-lifecycle.md`
- `docs/current/REGISTRY-LIFECYCLE.md`
- `docs/current/REGISTRY-API.md`
- `tests/test_registry_lifecycle.py`
- `tests/test_registry_api.py`
- `tests/test_registry_core_auth.py`
- `tests/test_registry_pg_contract.py`

## 8. 残留风险 / 未覆盖

1. PostgreSQL 测试需要显式 `MOBIUS_TEST_PG_DSN`，不是 CI 自动运行。
2. Core 认证测试通过 monkeypatch `API_KEY` 覆盖生产配置，未使用真实密钥。
3. `init_schema` 的 `_after_ddl` 是内部测试 seam；生产不传。
4. 当前只有 schema 初始化，没有旧角色/音色/工作流对象迁移状态机。
5. Registry API 已挂载，但 Agent/Gateway/TTS 仍未读取。
6. Workflow 四层安全验证、trust level、Injector 约束仍在后续阶段。

## 9. 停止点

Phase 0.1 到此停止。

不进入 Phase 1；等待人工审查。
