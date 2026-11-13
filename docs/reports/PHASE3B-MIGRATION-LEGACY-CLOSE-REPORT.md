# Phase 3B 实施报告：迁移状态机、旧读关闭、完整对照

- 日期：2026-11-13
- 状态：已完成并应用到生产 live；Agent 执行路径不再读 legacy 角色/音色文件
- 关联：[ADR-012](../adr/ADR-012-registry-runtime-migration.md)
- 上一阶段：[Phase 3A 报告](PHASE3A-RUNTIME-RESOLVER-REPORT.md)
- 代码提交：`fc0b347`

## 1. 迁移状态机

新增 `core/registry_migration.py`：

```text
LEGACY_DISCOVERED
  -> LEGACY_INDEXED
  -> REGISTRY_IMPORTED
  -> REGISTRY_VERIFIED
  -> RUNTIME_SHADOW
  -> RUNTIME_SWITCHED
  -> LEGACY_READ_DISABLED
```

特性：

- 状态持久化到 `data/registry_migration.json`
- 只允许前进一步
- 每次推进写审计 `registry.migration.advance`
- `REGISTRY_VERIFIED` 需要 compare 通过
- `LEGACY_READ_DISABLED` 需要 compare 通过 + 覆盖率完整

API：

```text
GET  /api/registry/manage/migration/status
POST /api/registry/manage/migration/compare
POST /api/registry/manage/migration/advance
```

## 2. 完整对照

`compare()` 覆盖：

- 13 个 character：
  - name / workflow / style / negative / checkpoint / size / loras / media_policy
  - voice.engine / voice.voice / voice.speed
- 4 个 voice_profile：
  - engine / voice / speed / used_by
- 9 个 workflow：
  - 节点 class_type 映射

生产结果：

```text
state=LEGACY_READ_DISABLED
published=13 characters / 4 voices / 9 workflows
compare=true
checked=26
mismatches=0
coverage.complete=true
legacy_fallback=false
```

## 3. 旧读路径关闭

`registry_runtime.character()`：

- `LEGACY_READ_DISABLED` 时不再调用 `store.get_character`
- 直接按 published Registry 解析 id/name/alias
- 未发布/未注册角色返回 `None`，Agent 返回 fail closed 错误

验证：

- 把 `bronya.json` 从 legacy 目录移走，resolver 仍返回 published `bronya`
- Agent 显式请求 `not_exist` → `character_not_found`
- 已发布角色生图/语音正常

边界：

- Studio「能力库」仍扫描 legacy 文件做只读展示，这是展示路径，不是 Agent 执行路径
- Gateway 直接 `/api/generate` 无 graph 时仍可回退 workflow 文件；Agent 使用
  `workflow_graph`，不依赖该回退
- 旧文件未删除，保留回滚能力

## 4. Live 验证

| 项目 | 结果 |
|---|---|
| migration state | `LEGACY_READ_DISABLED` |
| legacy_fallback | false |
| coverage | 13 / 4 / 9，complete |
| compare | true，checked 26，0 mismatch |
| Agent image | `t_93a8c28c`，`mobius_registry_00005_.png` |
| Agent voice | `t_fbc3bc1a`，audio URL OK |
| unknown character | `character_not_found`（fail closed） |
| runtime status | `enforce` |

## 5. 测试

| 项目 | 结果 |
|---|---|
| `test_registry_migration.py` | 6/6 |
| `test_registry_runtime.py` | 5/5 |
| `test_registry_manage.py` | 7/7 |
| `test_voice_assets.py` | 4/4 |
| `test_registry_lifecycle.py` | 16/16 |
| `test_registry_api.py` | 8/8 |
| `test_registry_index.py` | 3/3 |
| `test_qq_webhook.py` | 5/5 |
| `pnpm typecheck` | OK |

## 6. 备份与回滚

- 迁移状态备份：`D:\AI\backups\phase3b-20261113-182100\registry_migration.json`
- Runtime 配置备份：`D:\AI\backups\config-runtime-20261113-123715\config.json`
- Core 代码旧版本：`D:\AI\Mobius\core\*.bak-*`
- 迁移状态文件：`D:\AI\Mobius\data\registry_migration.json`

回滚：

1. `config.json` 的 `registry.runtime_mode` 改为 `off`
2. 或恢复较早的 `registry_migration.json` 状态
3. 重启 Core

## 7. 限制

- 生产 Core 当前由直接进程启动，不是 launcher 受控服务进程；下次正常启动恢复 managed 模式。
- 迁移状态是本地文件，不是数据库事务。
- `compare` 是规范字段/节点类型对照，不是字节级文件对照。
- 未做 `LEGACY_READ_DISABLED` 后的旧文件删除；回滚需要保留。
- VoiceProfile 与 VoiceAsset 的运行时绑定仍未完成。
- Gateway 非 Agent 直接调用仍有 workflow 文件回退。

## 8. 停止点

Phase 3B 完成，不继续 Phase 4（统一能力调用/权限/Provider 路由）。
