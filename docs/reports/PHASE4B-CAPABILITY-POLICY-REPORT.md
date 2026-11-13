# Phase 4B 实施报告：权限表、Provider 策略、执行记录保留

- 日期：2026-11-13
- 状态：已完成并热更新
- 关联：[ADR-014](../adr/ADR-014-capability-policy-retention.md)
- 上一阶段：[Phase 4A 报告](PHASE4A-CAPABILITY-INVOCATION-REPORT.md)
- 代码提交：`4ee0e7d`

## 1. 权限表

新增 `capability_permissions`：

```text
subject_type: source | actor | user | *
subject: qq / console / user-id / *
capability: image.generate / voice.synthesize / *
effect: allow | deny
priority / enabled / scope_json
```

决策：

- 具体度 `user > actor > source > *`
- 精确 capability 优于 `*`
- 同分 deny 优先
- 无匹配时 `capabilities.default_allow`，默认 `true`

管理接口：

```text
GET    /api/capabilities/permissions
POST   /api/capabilities/permissions
DELETE /api/capabilities/permissions/{id}
```

## 2. Provider 策略

新增 `capability_providers`，默认：

```text
image.generate / comfyui      priority 10
voice.synthesize / kokoro     priority 20
voice.synthesize / gpt-sovits priority 30
```

规则：

- 请求指定 provider/engine 时精确匹配
- 否则使用启用且 priority 最小者
- 当前仍只路由到现有 Gateway endpoint
- 管理接口：

```text
GET  /api/capabilities/providers
POST /api/capabilities/providers
```

## 3. 执行记录保留

配置：

```text
capabilities.retention_days  默认 30
capabilities.max_records     默认 10000
```

行为：

- Core 启动时执行一次 prune
- 手动接口：`POST /api/capabilities/maintenance/prune`
- 硬删除超期/超量记录，不归档

查询：

```text
GET /api/capabilities/retention
```

## 4. Live 验证

| 项目 | 结果 |
|---|---|
| permissions 默认 | 空表，default_allow=true |
| 临时 deny source=`phase4b-test` | `capability_permission_denied` |
| 测试权限已删除 | 200 |
| provider 默认 | comfyui / kokoro / gpt-sovits |
| retention | 30 days / 10000 rows |
| image capability | `mobius_registry_00008_.png`，provider=comfyui |
| voice capability | audio URL，provider=kokoro |
| execution records | character_revision=1，workflow=comic-portrait r1 |

## 5. 测试

| 项目 | 结果 |
|---|---|
| `test_capabilities.py` | 8/8 |
| `test_registry_migration.py` | 6/6 |
| `test_registry_runtime.py` | 5/5 |
| `test_registry_manage.py` | 7/7 |
| `test_voice_assets.py` | 4/4 |
| `test_registry_lifecycle.py` | 16/16 |
| `test_registry_api.py` | 8/8 |
| `test_qq_webhook.py` | 5/5 |
| `pnpm typecheck` | OK |

## 6. 边界

- Provider 可用性不是动态健康检查；只按 enabled/priority
- `scope_json` 尚未参与权限决策
- 权限默认 allow，默认 deny 需要显式配置
- 记录硬删除，无归档/恢复
- 权限/provider 管理暂无 Studio 页面
- Gateway 直连仍是本机运维接口

## 7. 回滚

- 代码：`core/capabilities.py`、`core/capabilities_api.py`、`core/server.py`
  有 `.bak-*` 备份
- 策略回滚：
  - 删除 `capability_permissions` 行 → 回到 default_allow
  - `capability_providers` 恢复默认 priority/enabled
  - 调整 `capabilities.retention_days/max_records` 并重启或手动 prune

## 8. 停止点

Phase 4B 完成，不继续 Phase 5。
