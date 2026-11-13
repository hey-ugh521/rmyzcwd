# ADR-012: Registry Runtime Migration State and Legacy Read Shutdown

- 状态：Accepted
- 日期：2026-11-13
- 关联：[Registry 架构契约 v2](../aegis/plans/2026-11-13-capability-registry-contract.md)

## 背景

Phase 3A 已经让 Agent/Gateway 在 `enforce` 下读取 published Registry，但仍保留
legacy fallback。直接把 fallback 关掉会有两个风险：

1. 覆盖不完整时，部分角色或 workflow 会突然不可用。
2. 没有可审计的“何时可以关闭旧读路径”的判断依据。

## 决策

引入一个持久化迁移状态机：

```text
LEGACY_DISCOVERED
  -> LEGACY_INDEXED
  -> REGISTRY_IMPORTED
  -> REGISTRY_VERIFIED
  -> RUNTIME_SHADOW
  -> RUNTIME_SWITCHED
  -> LEGACY_READ_DISABLED
```

状态只允许前进一步，存储在 `data/registry_migration.json`，每次推进写审计。
推进由 API 控制：

```text
GET  /api/registry/manage/migration/status
POST /api/registry/manage/migration/compare
POST /api/registry/manage/migration/advance
```

`advance` 的硬门槛：

- `REGISTRY_VERIFIED` 要求 `compare.ok == true`
- `LEGACY_READ_DISABLED` 要求 `compare.ok == true` 且覆盖完整

Runtime resolver 读取同一个状态：

- `LEGACY_READ_DISABLED` 时不再读取 legacy character 文件
- 未注册/未发布角色 fail closed
- 未发布 workflow 不再允许 legacy workflow 回退（Agent 路径）

## 对照测试

`compare()` 对三类资源做规范字段对照：

- character：name/workflow/style/negative/checkpoint/size/loras/media_policy/voice
- voice_profile：engine/voice/speed/used_by
- workflow：节点 class_type 映射

生产验收：

```text
state=LEGACY_READ_DISABLED
published=13 characters / 4 voices / 9 workflows
compare=true, checked=26, mismatches=0
```

## 后果

正面：

- 旧读路径的关闭有证据、可回滚、可审计
- 覆盖不完整时无法误关闭
- 运行时缺资源时 fail closed，不会静默读旧文件

代价与边界：

- 迁移状态是本地文件，不是数据库事务；文件丢失会回到
  `LEGACY_DISCOVERED`（fallback 恢复，运行时仍受 `runtime_mode` 控制）。
- `compare` 是规范字段对照，不是字节级文件对照。
- Registry 只读索引/Studio 能力库仍会扫描 legacy 文件用于展示；那些是只读投影，
  不是 Agent 执行路径。
- Gateway 的 `/api/generate` 仍保留显式 workflow 文件回退，供非 Agent 调用；
  Agent 路径使用 `workflow_graph`，不依赖该回退。

## 回滚

1. 将 `registry.runtime_mode` 改为 `off` 并重启 Core；或
2. 删除/恢复 `data/registry_migration.json` 到较早状态；
3. 恢复 config 备份。

这次生产备份：`D:\AI\backups\phase3b-20261113-182100`。
