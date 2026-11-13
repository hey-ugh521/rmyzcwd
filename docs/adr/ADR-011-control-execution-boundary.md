# ADR-011: Control Plane / Execution Plane Boundary

- 状态：Accepted
- 日期：2026-11-13
- 关联：[Capability Registry 架构契约 v2](../aegis/plans/2026-11-13-capability-registry-contract.md)、[ADR-010](ADR-010-registry-lifecycle.md)

## Context

“去 LangBot 依赖”曾被理解成“所有开源组件都自己重写”。这会导致项目把有限的工程能力投入到 GPU 推理、语音模型、QQ 协议等成熟领域，反而削弱控制面的质量。

Möbius 真正需要掌握的是：资源、状态、生命周期、任务归属、权限和策略；不是 ComfyUI 的节点实现、TTS 模型的推理代码或 NapCat 的协议栈。

## Decision

采用五条原则：

1. **Core 自主**  
   Core 拥有资源、状态、生命周期、任务归属、权限和策略。
2. **接口统一**  
   外部工具通过有版本、有超时、有幂等、有认证、有错误码的 Adapter/Gateway 接口接入。
3. **执行解耦**  
   ComfyUI、TTS、NapCat、远程 API 等作为 Tool Provider 执行；Core 不依赖其内部结构。
4. **工具复用**  
   继续使用成熟开源项目完成推理、协议和平台适配；不重复造轮子。
5. **部署渐进**  
   逻辑上 Core 是独立控制中心；物理部署（Windows Service、后台进程、桌面壳）以后可调整。

## Boundaries

| 层 | 拥有 | 不拥有 |
|---|---|---|
| Core Control Plane | 资源、Revision、发布、任务归属、权限、策略、审计 | GPU 推理、协议栈、具体工具实现 |
| Adapter / Gateway | 统一请求/响应、参数校验、调用分发、结果标准化、超时/幂等/错误映射 | 资源发布状态、用户/角色业务 |
| Tool Provider | ComfyUI、TTS/STT、NapCat、远程 API | Möbius 内部资源模型 |
| Launcher / Desktop Shell | 启动、进程监督、WebView 壳 | Core 领域逻辑 |

## Consequences

Positive:

- Registry/TaskStore/Agent 可以独立演进。
- ComfyUI workflow、TTS provider、QQ 协议适配器可以替换，不需要改 Core 资源模型。
- 不要为了“完全自研”重复实现已成熟的能力。
- 可以逐步决定 Windows 运行形态，不被 UI 复杂度绑架。

Trade-offs:

- 需要维护 Adapter 合约和错误标准化。
- 需要接受外部工具的版本/行为变化，通过 provider 边界隔离。
- 不能让外部 DTO 直接进入 Core 领域模型。

## API Rules

从现在开始，外部能力接口必须考虑：

- 版本化（如 `/v1`）
- 超时
- 幂等
- 认证
- 结构化错误码
- 结果标准化
- 调用审计

不要在接入第三方服务时才补这些。

## Verification

- Core 领域模型不出现 ComfyUI 节点结构、TTS 引擎字段或 OneBot 消息段。
- Media Gateway / Adapter 负责把 provider 返回转换为 Möbius 统一结果。
- Registry 的 `published_revision` 和 TaskStore 的执行状态分离。
- 更换一个 provider 不需要修改角色/资源 schema。

## Alternatives Rejected

- **全面自研**：成本高，质量差，偏离产品价值。
- **直接嵌入 LangBot/SillyTavern/Open WebUI**：会重新制造外部 owner，破坏控制权边界。
- **让 Core 直接依赖 provider 返回结构**：短期快，长期无法替换。

## Learning Rule

每个架构决策至少回答：

1. 解决什么问题？
2. 牺牲什么？
3. 如何验证？
4. 未来如何撤销？

ADR 是训练这种能力的最小工具，不是额外仪式。
