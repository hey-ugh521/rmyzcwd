# ADR-010: Registry Resource Lifecycle

- 状态：Accepted（Phase 0.1 hardened）
- 日期：2026-11-13
- 关联：[Capability Registry 架构契约 v2](../aegis/plans/2026-11-13-capability-registry-contract.md)
- 实现：`core/registry.py`, `core/registry_api.py`, `tests/test_registry_lifecycle.py`, `tests/test_registry_api.py`, `tests/test_registry_pg_contract.py`

## Context

Character、VoiceProfile、Workflow 都需要草稿、发布、回滚和引用检查。原设计分别使用 `revision`、无版本、`version + workflow_revisions`，会产生三套生命周期语义，且无法保证“编辑中的资源不改变正在运行的 Agent”。

Workflow 的 checksum 只能证明内容完整性，不能证明可信；但 Phase 0 先解决资源治理，不把 Workflow 执行安全混入生命周期。

## Decision

采用统一的两表模型：

```text
RegistryResource
  type, id, status, current_revision, published_revision,
  created_at, updated_at, created_by, updated_by

RegistryRevision
  type, id, revision, snapshot, checksum, state,
  created_at, created_by, change_note, parent_revision
```

核心规则：

1. Revision snapshot 不可变，只能新增 revision。
2. `current_revision` 是最新持久化版本；`published_revision` 是运行时唯一可用版本。
3. 编辑已发布资源只产生 draft revision，不改变 `published_revision`。
4. Publish/Rollback 通过 CAS（expected current + expected published）和事务更新发布指针。
5. Disable = 暂停运行时；Archive = 退出正常管理和使用；Delete = 默认软删除，必须先做引用检查。
6. disable/enable/archive/delete 必须提供 `expected_revision`；缺失返回 422，不匹配返回 409。
7. 引用表使用复合外键和行锁，删除与新增引用不得留下悬空引用。
8. `RequestContext` 在请求开始时锁定 `resolved_revision`，并返回深冻结 snapshot；Phase 0 只定义契约，不接入 Runtime。
9. 数据库层用 SQLite/PG trigger 阻止 revision snapshot/checksum/identity 被原地更新或删除。
10. partial unique index 保证每个资源最多一个 published revision；trigger 保证 published 指针指向有效 revision。
11. Registry API 的审计 actor 来自 Core 认证上下文，不信任请求 body 的 actor。
12. SQLite 与 PostgreSQL 都必须通过并发/回滚/引用契约测试，不能只用单连接顺序模拟。

## Consequences

Positive:

- 三个资源共用一套发布/回滚/审计逻辑。
- Agent/TTS/Gateway 未来只需读取 `published_revision`。
- 编辑不会改变运行行为。
- 引用删除和并发发布有明确的 409 语义。
- SQLite 首发，PostgreSQL 通过同一组契约测试。

Negative / deferred:

- 资源 payload 仍是 opaque JSON；业务 schema 校验留给 Phase 2。
- Workflow 四层安全验证、Prompt Compiler、VoiceAsset 不在 Phase 0。
- Registry API 已挂载，但 Agent/Gateway 尚未读取它。
- 没有 UI；没有迁移旧角色文件。
- 只有一个 schema version；未来需要显式 migration registry。

## Alternatives Rejected

- 每种资源独立版本表：三套语义，回滚/发布逻辑重复。
- 直接覆盖 snapshot：无法审计，无法回滚，运行时可能突变。
- 只靠应用层不可变约定：测试和并发下不可靠；必须有 DB trigger。
- 在 Phase 0 同时做 Workflow 安全验证：范围过大，会把资源治理和执行安全混在一起。

## Verification

- SQLite lifecycle: 16/16
- API contract: 8/8
- Core auth integration: 6/6
- PostgreSQL contract/concurrency: 8/8
- 现有 QQ adapter/webhook 回归：52/52 + 5/5

PostgreSQL 并发测试使用两个独立连接（每个 Service 调用独立 `store.conn()`），覆盖 concurrent update、concurrent publish、reference/create-delete race、duplicate reference、publish failure rollback、reference failure rollback。

## Rollback

Phase 0 不改变 Agent/Gateway 路径。若 Registry 出现问题：

- 不调用 Registry API；
- 不切换 Runtime；
- 可直接回退代码提交；
- 数据表保留不影响现有 Core 启动（`init_schema` 只创建表）。

生产数据表物理删除需单独审批；本项目 Phase 0 只做软删除。
