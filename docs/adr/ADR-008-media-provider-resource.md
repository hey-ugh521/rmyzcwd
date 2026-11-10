# ADR-008：媒体 Provider Adapter 与资源调度（M2）

- 状态：**Proposed（M2a 已实现并验收；M2b/M2c 待实现）**
- 日期：2026-11-10
- 决策人：产品经理（M2 方向已定：Provider Adapter + 媒体资源调度，ComfyUI/TTS 解耦）
- 关联：`ARCHITECTURE-v1.1.md`、`ADR-002`（状态词）、`ADR-007`（任务 owner）、`PRODUCT-BOUNDARY.md`、`ARCHITECTURE-GAP.md`（G-02/G-03/G-13）
- 影响：Media Gateway、Core、TTS/ComfyUI 调用路径、任务恢复、Console/诊断

## 1. 背景

当前媒体执行是"直接调用"而非"Provider 抽象"：

- `media_gateway/server.py` 的 `_run_image()` 内联 ComfyUI 提交/轮询/取消；`synth_tts()` 内联 Kokoro/GSV/F5 选择与回退（`engine` 硬编码）；
- Core Supervisor（`core/services.py`）只管进程生命周期与媒体模式；资源冲突依赖 `idle_loop`/`comfy_busy`/网关 activity 的启发式判断，**没有显式 GPU 租约**；
- TTS 与生图可能同时占用 GPU（GSV 为 GPU 路径），目前没有资源仲裁；
- Provider 特有字段、能力、健康状况没有统一描述，Console/诊断无法回答"当前能做什么、为什么不可用"；
- M1 已让 Core 成为任务状态唯一 owner，但执行端的 Provider 差异仍散落在 Gateway。

M2 的目标：**Core 决定做什么，Runtime 管理怎么执行，Gateway 的 Provider Adapter 负责具体调用谁**；Provider 可替换，资源可调度，外部 API 不变。

## 2. 决策

### 2.1 适配器放在 Media Gateway（执行面）

- 媒体 Provider Adapter 位于 `media_gateway/providers/`，不放在 Core；Core 只消费能力/健康摘要与任务状态。
- 模型（LLM）Provider Adapter 继续留在 `core/adapters/model/`（ARCHITECTURE-v1.1 §4），两套 adapter 领域不同，不得混用。
- Core 不出现 ComfyUI 节点名、Kokoro/GSV 端口、工作流细节。

### 2.2 统一 Provider 接口

```python
class MediaProvider(Protocol):
    id: str
    kind: str                      # "image" | "speech"
    capabilities: ProviderCapabilities

    async def health(self) -> ProviderHealth: ...
    async def submit(self, req: ProviderRequest) -> ProviderSubmission: ...
    async def query(self, execution_id: str) -> ProviderExecution: ...
    async def cancel(self, execution_id: str) -> CancelResult: ...
    # warmup/shutdown 可选：由资源调度器决定是否调用
```

- `ProviderCapabilities`：`modes`（text_to_image/image_to_image/lora/tts/voice_clone）、`resource`（gpu_heavy/gpu_light/cpu）、`max_concurrency`、`supports_cancel`、`supports_recovery`。
- `ProviderExecution` 返回**规范状态**（pending/running/completed/failed/cancelled/submission_unknown，ADR-002）+ result/error_code/provider_meta；`provider_meta` 仅诊断，不得向上层泄漏。
- 能力不满足、Provider 不可用、超时：由 Runtime 统一映射为结构化错误（`provider_unavailable`/`capability_unsupported`/`provider_timeout`），不暴露厂商字段。

### 2.3 Provider Registry 与配置

```jsonc
"media": {
  "providers": {
    "comfyui": {"id": "comfyui", "kind": "image", "resource": "gpu_heavy",
                "max_concurrency": 1, "supports_cancel": true},
    "kokoro":  {"id": "kokoro",  "kind": "speech", "resource": "cpu",
                "max_concurrency": 2, "supports_cancel": false},
    "gsv":     {"id": "gsv",     "kind": "speech", "resource": "gpu_heavy",
                "max_concurrency": 1, "supports_cancel": true}
  },
  "routing": {"image": ["comfyui"], "speech": ["kokoro", "gsv"]}
}
```

- 引擎名（kokoro/mobius/f5）在 Adapter 内映射，不进入 Core。
- 路由按能力与健康选择；显式 engine 请求优先，失败时按 fallback 链回退（仅健康/能力失败，不掩盖业务错误）。
- 新增内部只读 `/api/providers`（鉴权同 `/api/*`）：能力、健康、资源占用；供 Console/诊断使用。

### 2.4 资源调度器（Gateway Media Runtime）

- 资源：`gpu_heavy`（ComfyUI、GSV）**独占**；`cpu`（Kokoro）可共享（`max_concurrency`）。
- 任务提交前 `acquire(resource, task_id)`；终态或失败后 `release`；租约含 owner/expiry，防泄漏。
- 队列：FIFO + 公平性（同用户连续任务不能饿死他人）；GPU 同一时刻只允许一个 heavy lease。
- 资源状态机：`idle → acquiring → busy → releasing → idle`；异常进入 `error` 并告警。
- 与 Core Supervisor 的分工：
  - Core 仍负责进程/服务生命周期（start/stop/probe/idle）。
  - Runtime 负责"谁在用 GPU"；Core idle 判断必须读取 Runtime 的 lease/queue 状态（不再仅靠 activity + Comfy queue 启发式）。
  - idle 休眠前必须确认无 heavy lease；prewarm/on_demand 语义不变。
- 取消/崩溃：lease 过期回收；Provider 不支持取消时如实上报，由 Core 终态策略处理。

### 2.5 TTS 解耦

- `KokoroProvider`（CPU，可并发）、`GSVProvider`（GPU heavy，独占）、`F5Provider`（legacy，按配置可选）。
- `engine` 请求 → Provider 路由；回退链只在健康/能力失败时使用，业务错误（文本过长/角色禁用）不回退。
- 外部 `/api/tts`、MCP `tts_speak`、Core 语音任务行为保持不变；返回结构兼容。

### 2.6 与 M1 任务 owner 的集成

- 任务仍由 Core 创建/持有；Gateway Runtime 在拿到 Core task_id 后：
  1. 选 Provider → `acquire` 资源 → `provider.submit` 得到 execution_id；
  2. 将 execution_id 写入执行账本（`task_snapshot` 私有账本，不承载业务状态）；
  3. Provider 状态事件 → Core `/v1/tasks/{id}/events`；
  4. 恢复：按 execution_id `query` Provider，迟到产物回收。
- 取消：Core `cancel_requested` → Runtime 调 `provider.cancel`；不支持取消则保留请求态，终态由 Core 策略决定。
- Provider 不可用：任务失败（明确 error_code），不落本地业务状态（保持 M1 fail-closed）。

### 2.7 兼容与回滚

- 引入特性开关 `MOBIUS_MEDIA_PROVIDER=legacy|adapters`：
  - `legacy`：现有直调路径（默认，直到 M2 验收通过）；
  - `adapters`：Provider/Runtime 路径。
- 验收通过后默认切 `adapters`；回滚开关保留一个 release。
- 外部 REST/MCP 响应、任务 ID、媒体模式语义不变。
- 部署顺序：先 Gateway（低风险，可回滚）→ 观察 → 再切换默认；Core 无破坏性 schema 变更。

## 3. 关键状态映射

| Provider 状态 | Core/对外状态 |
|---|---|
| pending | pending/queued |
| running | running |
| completed | completed |
| failed | failed（error_code 保留） |
| cancelled | cancelled |
| unknown/丢失 | submission_unknown（人工/超时策略） |

## 4. 被否决方案

| 方案 | 否决理由 |
|---|---|
| Provider 放在 Core | Core 会重新耦合 ComfyUI/TTS 细节，违反 control/execution 分离 |
| 多进程微服务化 Provider | 单机 Windows、过重；先用同进程 Adapter + 资源锁 |
| 仅靠 Supervisor 进程状态调度 | 无法知道 GPU 是否被 TTS/Comfy 同时占用，仍会冲突 |
| 无特性开关直接替换 | 风险不可控；必须可回滚 |

## 5. 验收标准

- 契约测试：每个 Provider 的 health/capabilities/submit/query/cancel；能力不足/不可用/超时的统一错误。
- 一致性：`legacy` 与 `adapters` 对同一请求返回兼容结果（图片/音频路径、task_id、最终状态）。
- 调度：GPU heavy 永不并发；CPU TTS 按并发上限；队列公平；取消/异常释放 lease；崩溃租约回收。
- 集成：M1 Core 任务事件/恢复/取消在 adapters 模式下通过；外部 API 结构不变。
- 隔离：Core 代码 grep 不到 ComfyUI/Kokoro/GSV 特有字段；Provider 特有字段不越过 Gateway 边界。
- 故障注入：Provider 进程挂、HTTP 超时、ComfyUI 恢复、TTS 回退、lease 泄漏。

## 7. 实现状态（M2a，2026-11-10）

- `media_gateway/providers/`：base 契约（能力/请求/状态/错误/Protocol）、Registry、ComfyUI Adapter。
- `media_gateway/server.py`：`MOBIUS_MEDIA_PROVIDER=legacy|adapters`（默认 legacy，行为不变）；内部 `GET /api/providers` 只读能力清单。
- `config.example.json`：新增 `media.providers/routing` 示例。
- 测试：`media_gateway/test_m2_provider_contract.py` **23/23**；既有网关回归 t5 40、t3 9、t4 9、async 37 全过。
- 未接执行路径：ComfyUI Adapter 尚未替换 legacy `_run_image`；TTS Adapter/资源调度在 M2b/M2c。

## 6. 待确认决策

1. Provider Adapter 放 Gateway（本 ADR 建议）还是部分放 Core？
2. 资源调度器放 Gateway Runtime（建议）还是 Core Supervisor？
3. Kokoro CPU 并发上限（建议 2）、GSV/ComfyUI GPU heavy 独占（建议是）。
4. 切换默认 `adapters` 的时机：M2 验收后立即，还是再观察一个 release？
5. 是否在本机首版就强制 Provider 路由（engine 显式指定时优先，失败回退策略）。
