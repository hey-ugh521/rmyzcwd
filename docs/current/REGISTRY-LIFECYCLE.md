# Registry Resource Lifecycle (Phase 0)

Source of truth: [ADR-010](../adr/ADR-010-registry-lifecycle.md).

## Resource states

```text
active
  ├─ disable -> disabled
  ├─ archive -> archived
  └─ soft delete -> deleted

disabled
  ├─ enable -> active
  ├─ archive -> archived
  └─ soft delete -> deleted

archived
  └─ soft delete -> deleted

deleted (terminal in Phase 0)
```

- `active`：可解析 published revision，可编辑，可发布。
- `disabled`：保留数据；运行时 `resolve()` 拒绝；可编辑；可重新 enable。
- `archived`：退出正常管理；保留历史 revision/audit；不可编辑；不可发布。
- `deleted`：软删除；保留 revision/audit；不可解析；物理清理由后续受控流程处理。

状态变更必须提供 `expected_revision`：

- 缺失：`422 expected_revision_required`
- 不匹配：`409 revision_conflict`
- 并发状态变更：一个成功，另一个 `409 invalid_status_transition`

状态迁移：

```text
disable: active -> disabled
enable:  disabled -> active
archive: active|disabled -> archived
delete:  active|disabled|archived -> deleted
```

## Revision states

```text
draft
  └─ publish -> published

published
  └─ publish newer/older -> superseded

superseded
  └─ publish again -> published
```

`state` 是审计元数据；唯一决定运行时的是 `RegistryResource.published_revision`。

## Publish / rollback

- `publish` 使用 `expected_current_revision` + `expected_published_revision` 做 CAS。
- 并发发布只有一个请求可以成功；另一个返回 `409 publish_conflict`。
- `rollback` = 重新发布历史 revision；不修改该 revision 的 snapshot。
- 发布指针更新与 revision 状态更新在同一事务中完成。

## RequestContext

```text
RequestContext
  request_id
  resolved[resource] = ResolvedResource(
    resource_type, resource_id, revision, checksum, snapshot, resolved_at
  )
```

- 第一次 resolve 后固定绑定的 revision。
- 同一请求重复 resolve 返回同一对象。
- 新请求创建新 context，才允许看到新发布版本。

Phase 0 只提供对象；Agent/TTS/Gateway 尚未接入。

## Error codes

| code | HTTP | 含义 |
|---|---:|---|
| `resource_not_found` | 404 | 资源不存在 |
| `resource_exists` | 409 | 创建冲突 |
| `revision_conflict` | 409 | `expected_revision`/current 不匹配 |
| `publish_conflict` | 409 | published 指针已变化 |
| `revision_not_found` | 404 | revision 不存在 |
| `revision_corrupt` | 409 | snapshot 与 checksum 不一致 |
| `resource_unavailable` | 409 | disabled/archived/deleted |
| `resource_not_published` | 409 | 没有 published revision |
| `resource_not_editable` | 409 | archived/deleted 不可编辑 |
| `resource_not_active` | 409 | 非 active 不可发布 |
| `invalid_status_transition` | 409 | 状态机不允许 |
| `reference_exists` | 409 | 仍被引用，不能删除 |
| `resource_not_referenceable` | 409 | 引用源/目标状态不允许 |
| `idempotency_conflict` | 409 | 同一 idempotency key 不同请求 |
| `invalid_resource_type` | 422 | 未知资源类型 |
| `invalid_resource_id` | 422 | id 非法 |
| `invalid_payload` | 422 | payload 不是 object |
| `expected_revision_required` | 422 | 缺少并发前置版本 |
| `expected_published_revision_required` | 422 | 发布缺少发布指针前置版本 |

## Invariants

1. `registry_revisions` 的 snapshot/checksum/identity 不可更新；DB trigger 拒绝。
2. revision 不可删除；DB trigger 拒绝。
3. `current_revision` 单调递增。
4. `published_revision` 必须指向存在的 revision；DB trigger 拒绝无效指针。
5. 每个资源最多只有一个 `state='published'` revision；partial unique index 保证。
6. 编辑不改变 `published_revision`。
7. 删除前必须在同一事务中检查引用。
8. 同一资源同时只有一个并发写者能成功。
9. 软删除保留 revision 和 audit。
10. `RequestContext` 返回深冻结 snapshot；调用方不能修改已解析内容。
11. checksum 使用 `sort_keys=True`、紧凑分隔符、UTF-8 的确定性 JSON 规范化。
12. Reference 建立要求 source 为 active/disabled、target 为 active；删除 target 前必须在同一事务检查引用；解除引用与删除语义分别审计。
