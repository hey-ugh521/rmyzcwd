# ADR-015: Dynamic Provider Health and Degradation

- 状态：Accepted
- 日期：2026-11-13
- 关联：[ADR-014](ADR-014-capability-policy-retention.md)

## 背景

Phase 4B 的 Provider 策略只按 `enabled/priority` 静态选择。Kokoro 已停止时，
`voice.synthesize` 仍会先尝试 Kokoro，再走失败重试；没有“先选健康 Provider”的能力。

## 决策

`CapabilityService` 增加动态健康检查：

```text
provider -> Supervisor service
comfyui  -> comfyui
kokoro   -> kokoro
gpt-sovits -> gsv
```

- 使用 `Supervisor.probe(service)`，不主动启动服务
- 健康结果缓存，TTL 默认 15 秒（`capabilities.provider_health_ttl_sec`）
- 按 Provider 策略顺序选择，跳过不可用 Provider
- 指定 Provider 时仍优先精确匹配，但不可用则降级到下一项
- 所有 Provider 不可用 → `provider_unavailable`
- 记录实际使用的 Provider；不记录不可用 Provider 的失败为成功

Voice Provider 到 Gateway engine 的映射：

```text
kokoro    -> kokoro
gpt-sovits -> mobius   (Gateway 的 F5/GSV 引擎名)
```

## API

```text
GET /api/capabilities/provider-health
```

返回每个 Provider 的 service、state、available、checked_at、error。

## 后果

正面：

- Kokoro 下线时 voice 自动走 GPT-SoVITS（如果 gsv ready）
- 健康检查有 TTL，避免每次请求探测进程
- Agent 仍通过统一能力层，无需知道 provider 细节

代价与边界：

- 这是服务进程级健康，不是模型加载/推理级健康
- image 当前只有 comfyui，没有第二个 Provider 可降级
- 没有熔断/半开状态机；失败后仍按 TTL 重试
- 没有延迟/成本权重
- 健康探测来自 Core Supervisor，不跨主机
