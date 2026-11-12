# Phase 0 实施报告：Registry 生命周期基础设施

- 日期：2026-11-13
- 状态：Phase 0 完成，等待审查；未进入 Phase 1
- 基础契约：[Capability Registry 架构契约 v2](../aegis/plans/2026-11-13-capability-registry-contract.md)
- ADR：[ADR-010 Registry Resource Lifecycle](../adr/ADR-010-registry-lifecycle.md)
- 代码提交：`005a545`

## 1. 范围遵守

本阶段只实现 Registry 基础设施：

- 新数据库表 / schema 初始化
- `RegistryService` 生命周期
- `/api/registry/*` 契约
- `RequestContext` resolved revision 契约
- 自动化不变量测试
- ADR、状态机文档、API 契约文档

**未做：**

- 未开发 Studio/Console UI
- 未修改 Agent Runtime、Media Gateway、TTS、ComfyUI 执行路径
- 未把 Registry 接入现有角色/音色/工作流读取
- 未做 Phase 1 的只读索引和页面
- 未做 Workflow 四层安全验证（属于后续阶段）

## 2. 交付物

| 交付物 | 路径 |
|---|---|
| Registry schema / repository / lifecycle | `core/registry.py` |
| API 契约 | `core/registry_api.py` |
| Core 挂载与启动初始化 | `core/server.py` |
| ADR | `docs/adr/ADR-010-registry-lifecycle.md` |
| 状态机 | `docs/current/REGISTRY-LIFECYCLE.md` |
| API 契约文档 | `docs/current/REGISTRY-API.md` |
| SQLite 不变量测试 | `tests/test_registry_lifecycle.py` |
| API 契约测试 | `tests/test_registry_api.py` |
| PostgreSQL 契约测试 | `tests/test_registry_pg_contract.py` |

## 3. 核心实现

### 3.1 统一生命周期

```text
RegistryResource(type,id,status,current_revision,published_revision)
RegistryRevision(type,id,revision,snapshot,checksum,state)
```

- revision snapshot 不可变。
- `current_revision` 是最新持久化版本，`published_revision` 是运行时指针。
- 编辑不改变 `published_revision`。
- publish/rollback 使用 `expected_current_revision` + `expected_published_revision` CAS。
- disable / enable / archive / soft delete 是独立状态迁移。

### 3.2 DB 级不可变边界

- SQLite trigger：阻止 revision identity/snapshot/checksum 更新，阻止删除 revision。
- PostgreSQL trigger function：同样阻止 UPDATE/DELETE。
- 直接 SQL 篡改测试会失败，且不会破坏已有 revision。

### 3.3 并发与引用

- 写操作使用 `BEGIN IMMEDIATE`（SQLite）或行锁（PG）。
- update/publish 的并发冲突返回 `409 revision_conflict` / `409 publish_conflict`。
- 引用表使用复合外键；删除与新增引用在同一事务锁内检查，避免悬空引用。

### 3.4 API 工程契约

- `expected_revision` / `If-Match` 乐观锁。
- `Idempotency-Key` 创建幂等。
- 结构化错误 `{error:{code,message,details}}`。
- Pagination。
- 软删除 + 引用检查。
- 状态变更支持可选 `expected_revision` 查询参数。
- `X-Request-ID` 回显。

## 4. 验证证据

### SQLite 生命周期

```text
python tests/test_registry_lifecycle.py
Ran 13 tests
OK
```

覆盖：

- revision 不可变与直接 SQL 篡改
- 编辑发布资源不改变 published
- rollback 不重写后续 revision
- 并发编辑单赢家
- 并发发布单赢家
- disable/enable/archive
- 引用删除阻塞与解除
- 引用与删除并发不产生悬空引用
- idempotency 冲突
- RequestContext revision 锁定
- 重启恢复
- schema 初始化失败事务回滚并可恢复
- 分页

### API 契约

```text
python tests/test_registry_api.py
Ran 7 tests
OK
```

覆盖 create/update/publish/rollback、409 冲突、结构化错误、幂等、引用删除、状态接口、分页。

### PostgreSQL 契约

```text
MOBIUS_TEST_PG_DSN=<临时测试库> python tests/test_registry_pg_contract.py
Ran 2 tests
OK
```

使用一次性数据库 `mobius_registry_test`，测试后删除；覆盖生命周期、rollback、DB 不可变、引用删除。

### 现有 Core 回归

```text
tests/test_qq_adapter.py    52/52
tests/test_qq_webhook.py     5/5
```

Registry API 挂载未影响 QQ/Core 现有路径。

## 5. 未完成 / 风险

1. **Legacy 资源迁移状态机尚未实现**  
   本阶段只有 schema 初始化；现有角色/音色/工作流仍由旧路径读取。对象级 migration 状态机属于 Phase 1/3。

2. **Workflow 安全验证未实现**  
   本阶段只做 Registry 生命周期；Workflow 四层验证、trust level、Injector 约束属于后续。

3. **Registry API 尚未接入认证测试**  
   API 挂载在 Core 的 `/api` 中间件下；本阶段用独立 FastAPI 测试路由，未覆盖生产 cookie/Bearer 边界。

4. **State API 的乐观锁是可选 query**  
   update/publish 强制 expected；disable/enable/archive/delete 的 expected 可选，未传时仍有事务锁。若要求所有状态变更强制 CAS，可在 Phase 1 收紧。

5. **PG 测试不是 CI 自动运行**  
   需要显式提供 `MOBIUS_TEST_PG_DSN`；本次已在临时 PostgreSQL 库手工验证。

6. **没有迁移版本列表**  
   当前 `REGISTRY_SCHEMA_VERSION=1`，schema DDL 在事务中执行；未来 schema 变更需要显式 migration 表/函数。

## 6. 停止点

Phase 0 到此停止。

未开始：

- 只读 Registry 页面
- 角色/音色/工作流导入
- Agent/Gateway Runtime 接入
- Workflow 安全验证
- Capability Resolver

等待审查通过后再进入 Phase 1。
