# Phase 4C 实施报告：Provider 动态健康检查与降级

- 日期：2026-11-13
- 状态：已完成并热更新
- 关联：[ADR-015](../adr/ADR-015-provider-health-degradation.md)
- 上一阶段：[Phase 4B 报告](PHASE4B-CAPABILITY-POLICY-REPORT.md)
- 代码提交：`1daf820`

## 1. 动态健康检查

`CapabilityService` 现在通过 Core Supervisor 探测 Provider 对应服务：

```text
comfyui   -> comfyui
kokoro    -> kokoro
gpt-sovits -> gsv
```

- 使用 `Supervisor.probe()`，不主动启动服务
- 缓存 TTL 默认 15s：`capabilities.provider_health_ttl_sec`
- 只把 `state=ready` 视为可用
- 探测异常视为不可用

## 2. 选择与降级

- 按 `capability_providers` 的 priority 排序
- 请求指定 provider/engine 时优先精确匹配
- 精确匹配不可用时继续尝试下一项
- 全部不可用 → `provider_unavailable`
- 实际使用的 provider 写入 `capability_invocations.provider`

Voice Provider → Gateway engine：

```text
kokoro     -> kokoro
gpt-sovits -> mobius
```

## 3. 健康接口

```text
GET /api/capabilities/provider-health
```

返回：

```json
{
  "provider": "kokoro",
  "service": "kokoro",
  "state": "ready",
  "available": true,
  "checked_at": 1794566984.2,
  "error": ""
}
```

## 4. Live 验证

| 步骤 | 结果 |
|---|---|
| 三个 Provider 全部 ready | health 返回 3/3 available |
| 停止 Kokoro | kokoro=down，gsv=ready |
| 发起 voice（requested=kokoro） | 自动选择 gpt-sovits，engine=mobius，调用成功 |
| 恢复 Kokoro | ready |
| 最终 health | comfyui/kokoro/gpt-sovits 全部 ready |

降级调用返回：

```text
OK True cap_92401df03e40
record provider=gpt-sovits status=completed
```

## 5. 测试

| 项目 | 结果 |
|---|---|
| `test_capabilities.py` | 10/10 |
| `test_registry_migration.py` | 6/6 |
| `test_registry_runtime.py` | 5/5 |
| `test_registry_manage.py` | 7/7 |
| `test_voice_assets.py` | 4/4 |
| `test_registry_lifecycle.py` | 16/16 |
| `test_registry_api.py` | 8/8 |
| `test_qq_webhook.py` | 5/5 |
| `pnpm typecheck` | OK |

## 6. 边界

- 服务进程级健康，不是模型加载/推理级健康
- image 只有 comfyui 一个 Provider，没有可降级目标
- 没有熔断/半开状态机；失败后按 TTL 重试
- 没有延迟/成本权重
- 健康探测来自本机 Core Supervisor，不跨主机
- 权限/provider 暂无 Studio 页面

## 7. 回滚

- 代码：`core/capabilities.py`、`core/capabilities_api.py`、`core/agent.py`
  有 `.bak-*` 备份
- 行为回滚：恢复旧版 `capabilities.py` 并重启 Core；静态 provider 选择仍可用
- Provider 恢复：`/api/services/{name}/start` 或 launcher start-all

## 8. 停止点

Phase 4C 完成，不继续 Phase 5。
