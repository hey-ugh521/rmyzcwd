# Capability Registry 架构契约 v2

> 状态：Phase 0 / 0.1 已完成 Registry 基础设施；Phase 1 只读索引与 Studio 能力库页已实现，运行时仍未切换。
> 取代：[v1 设计稿](../../archive/aegis/2026-11-13-capability-registry-design-v1.md) 中关于生命周期、工作流安全、Prompt 编译、音色素材、迁移和阶段验收的部分。
> 原稿的 UI 页面分组、导入向导、组件复用建议保留。

## 0. 结论

这份设计值得继续，但**不能直接进入三个页面的开发**。先修订架构契约：

**P0 必须修改：**

1. 三种 Registry 统一为 `RegistryResource + RegistryRevision` 生命周期。
2. Workflow 的 checksum 只证明完整性，不证明可信；补全结构/依赖/权限/执行四层验证。
3. 明确 Capability、Resource、Provider、Gateway、TaskStore 的职责边界。

**P1 必须补齐：**

4. Character Prompt Compiler，角色数据不直接耦合模型消息格式。
5. 迁移状态机与 API 并发/幂等/引用检查契约。

**本阶段不做：**

- 通用插件市场
- UI Workflow JSON 的完整转换
- 自动安装 ComfyUI 自定义节点
- 把所有能力抽象成一个大而全的插件系统

---

## 1. 统一资源生命周期

### 1.1 为什么统一

角色、音色、工作流都需要：

- 草稿编辑
- 版本历史
- 发布/回滚
- 引用检查
- 审计
- 运行时只读已发布版本

如果各写一套，会出现三套发布语义、三套回滚逻辑和三套并发控制。

### 1.2 统一模型

```text
RegistryResource
  id                 stable id (slug/uuid)
  type               character | voice_profile | workflow
  status             active | disabled | archived
  current_revision   latest persisted revision (draft or published)
  published_revision latest revision allowed for runtime; nullable
  created_at
  updated_at
  created_by
  updated_by

RegistryRevision
  resource_id
  revision           monotonic integer per resource
  snapshot           immutable serialized resource payload
  checksum           sha256(snapshot canonical form)
  state              draft | published | superseded
  created_at
  created_by
  change_note
  parent_revision    nullable
```

### 1.3 关键语义

**编辑不改变运行行为。**

```text
revision 1: published  -> Agent 使用 1
revision 2: draft      -> 用户编辑 2
Agent 仍使用 1
测试通过后 publish 2 -> Agent 切到 2
```

- 每次保存创建新 revision；revision 不可变。
- `current_revision` 只表示“最新持久化版本”，不表示“运行中版本”。
- Agent/TTS/Gateway 读取 `published_revision`。
- 没有 `published_revision` 的资源不进入运行时。
- 运行时在**一次请求开始时**解析 revision，并在该次执行记录中锁定 `resolved_revision`；不允许同一轮中途切换。
- 回滚 = 把旧 revision 重新发布，产生审计记录；不要物理删除新 revision。
- 已发布资源的删除默认改为 `disabled`/`archived`，物理删除只允许对从未发布且无引用的资源。

### 1.4 发布原子性

```text
BEGIN
  verify expected_revision == current_revision   -- optimistic lock
  verify revision exists and checksum matches
  verify reference/capability constraints
  update resource.published_revision = revision
  update revision state = published
  mark previous published revision superseded
  insert audit event
COMMIT
```

- 发布失败不得留下半发布状态。
- 并发发布返回 `409 revision_conflict`。
- 发布前验证失败返回结构化错误，不修改指针。

### 1.5 业务字段

三种资源保留各自 payload，但外层生命周期共用：

```text
CharacterPayload
VoiceProfilePayload
WorkflowPayload
```

`RegistryRevision.snapshot` 存完整 payload；业务表可以只做索引/投影，不能成为第二套 owner。

---

## 2. Workflow 安全模型

### 2.1 checksum 的边界

`graph_checksum` 只能证明：

- 图内容没有发生未授权变化。

不能证明：

- 图里的节点不会读文件；
- 不会访问网络；
- 不会执行命令；
- 不会加载未经授权的模型；
- 参数不会把显存打爆。

因此发布状态 ≠ 可信状态。

### 2.2 四层验证

```text
结构验证
  格式、节点结构、连接关系、输出节点、循环/悬空引用

依赖验证
  node class 是否存在
  node 版本/来源
  模型/VAE/CLIP/LoRA 是否存在
  是否属于可信依赖

权限验证
  文件读写
  网络请求
  子进程/命令执行
  模型访问范围
  按 Workflow 记录 permissions，默认拒绝

执行验证
  参数范围
  最大 batch/size/steps/cfg
  显存预算
  执行超时
  输出目录
  资源限制
```

### 2.3 信任等级

```text
trust_level:
  builtin      # 随 Möbius 发布，默认可信
  verified     # 校验通过、节点依赖可信，可发布
  community    # 用户导入，默认不可发布，只能 draft/手工确认
  untrusted    # 缺失节点、权限不明、静态检查告警
```

- 只有 `builtin` / `verified` 可直接被 Agent 使用。
- `community` 需要用户在 Workflow 详情页显式确认“我信任此工作流及其依赖”。
- `untrusted` 不进入运行时。
- 不自动安装自定义节点；缺失节点只给出可操作告警。

### 2.4 首个版本只支持 API Prompt JSON

- 只接受 `{node_id: {class_type, inputs}}` 形式的 API Prompt。
- UI Workflow JSON（`nodes`/`links`）暂不支持完整转换。
- 后续再做转换器，且转换后必须重新走四层验证。

### 2.5 Injector 安全约束

```text
WorkflowInjector
  parameter
  nodeId
  input
  expectedType
  constraints {
    min, max, step, enum, maxLength
  }
  required
  allowUserOverride
  transform
```

示例：

```text
width  : number, 64..2048, step 8
height : number, 64..2048, step 8
batch  : number, 1..4
steps  : number, 1..64
cfg    : number, 0..20
seed   : integer, 0..2^32-1
```

Agent 传 `width=100000` 必须在注入前被拒绝；错误要返回清晰字段和边界，而不是交给 ComfyUI 失败。

### 2.6 执行隔离

- ComfyUI 自定义节点在 ComfyUI 进程内执行，不能假设它安全。
- 未信任工作流不执行。
- 可信工作流也要有显存、batch、超时、输出目录限制。
- 高权限节点（文件/网络/命令）应由节点能力清单标记；没有能力清单的节点视为高权限。
- Workflow 的发布状态和 TaskStore 的执行状态完全分离。

---

## 3. Prompt Compiler

### 3.1 为什么角色卡不能直接进模型

`PromptBlock[]` 是产品语义，不是 OpenAI/DeepSeek/Ollama 的消息格式。

不同 provider 对 system、developer、user、assistant、深度注入、历史截断的处理不同，因此需要编译层。

```text
CharacterProfile
      │
      ▼
Prompt Compiler
      │
      ├── System Instructions
      ├── Character Identity
      ├── Personality
      ├── Scenario
      ├── Lorebook
      ├── Dialogue Examples
      ├── Post History Instructions
      │
      ▼
Context Manager
      │
      ▼
Provider Adapter
      │
      ▼
LLM Request
```

### 3.2 权威字段

- `depthPrompt` 删除，统一由 `PromptBlock` 表达。
- 每个 block 有：
  - `placement`: `before_history | after_history | depth`
  - `depth`: 仅在 `placement=depth` 时有效
  - `order`
  - `enabled`
- Provider 适配器只负责把编译结果映射成消息数组，不重新解释角色字段。

### 3.3 Lorebook Resolver 独立

世界书不是简单的字段导入。需要独立 resolver 处理：

- 关键词/正则匹配
- 递归扫描
- token 预算
- 条目冲突/优先级
- 扫描深度
- 是否允许递归触发
- 群聊/用户作用域

Lorebook 的原始数据保留在 `character_book`，resolver 只输出本次上下文的命中条目，不修改原始卡。

### 3.4 Prompt Inspector

Context Manager 应能展示：

- 本轮使用了哪个角色 revision
- 哪些 PromptBlock 被编译
- 哪些 Lorebook 条目被触发
- 记忆/摘要注入
- 最终 provider messages

这是排查“角色行为突然变了”的关键工具。

---

## 4. Voice Registry 与素材生命周期

### 4.1 三类对象

```text
VoiceProvider   服务实现：kokoro / gpt-sovits / fish-speech / f5
VoiceProfile    音色配置：provider + model + speaker + ref asset + speed + weight
VoiceAsset      素材：参考音频、生成音频、说话人模型、模型权重、预览缓存
```

### 4.2 VoiceAsset

```text
VoiceAsset
  id
  kind: reference_audio | generated_audio | speaker_model | model_weight | preview_cache
  path
  checksum
  source
  license
  consent_record_id     nullable
  owner
  reuse_policy          private | this_profile | this_character | workspace
  created_at
  expires_at            nullable
```

### 4.3 Profile 不再直接承载 previewUrl

- `VoiceProfile.preview_asset_id` 指向一个 `preview_cache` asset。
- 预览资产有生命周期，可重建、可删除。
- 参考音频有来源和授权记录。
- `allowClone` 布尔值升级为 `clone_policy`：

```text
clone_policy:
  enabled
  consent_record_id
  allowed_scope: profile | character | workspace
  allow_reuse
  deleted_at
```

### 4.4 运行时

```text
speak -> voiceProfileId -> provider/model/speaker/ref asset
      -> TTS provider
      -> generated_audio asset
```

- 生成音频是资产，不是 profile 字段。
- 素材被删除时，引用检查必须阻止删除或要求迁移。
- 预览缓存丢失不影响合成。

---

## 5. 迁移状态机

### 5.1 对象级状态

```text
LEGACY_DISCOVERED
      │
      ▼
LEGACY_INDEXED
      │
      ▼
REGISTRY_IMPORTED
      │
      ▼
REGISTRY_VERIFIED
      │
      ▼
RUNTIME_SWITCHED
      │
      ▼
LEGACY_READ_DISABLED
```

每个状态必须有明确验收条件，不能“数据库导入完成 = 迁移完成”。

### 5.2 验收条件

| 状态 | 验收 |
|---|---|
| LEGACY_DISCOVERED | 源路径、checksum、类型已记录 |
| LEGACY_INDEXED | 能通过旧路径读取；registry 尚未接管 |
| REGISTRY_IMPORTED | registry 有完整 snapshot，原始件未改 |
| REGISTRY_VERIFIED | 旧/新解析结果一致；引用、voice/workflow ID 可解析 |
| RUNTIME_SWITCHED | Agent/Gateway 通过 registry 解析；对照测试通过 |
| LEGACY_READ_DISABLED | 旧读路径只读或关闭；无写入；回退脚本可用 |

### 5.3 回退

- 每个对象保存：来源路径、原始 checksum、迁移记录、迁移前后 snapshot checksum。
- 回退只能回到 `REGISTRY_VERIFIED` 或 `LEGACY_INDEXED`，不能在两套状态间双写。
- 旧文件 fallback 是有边界的临时兼容，不是永久第二写入路径。

---

## 6. API 工程契约

### 6.1 通用机制

| 机制 | 要求 |
|---|---|
| Schema Validation | 请求/响应 Pydantic/JSON Schema 校验 |
| Optimistic Lock | PUT/PUBLISH 携带 `expected_revision` 或 `If-Match` |
| Idempotency | 创建/导入支持 `Idempotency-Key` |
| Audit Log | 创建、编辑、发布、回滚、删除、导入 |
| Reference Check | 删除/禁用前检查引用 |
| Atomic Publish | 发布事务 + CAS |
| Pagination | `cursor`/`limit`，稳定排序 |
| Structured Errors | `code`, `message`, `details`, `request_id` |
| Soft Delete | 已发布资源默认 disable/archive |
| RBAC | 本地单用户可简化为“本机会话”，但接口保留 actor |

### 6.2 错误结构

```json
{
  "error": {
    "code": "revision_conflict",
    "message": "资源已被其他编辑更新",
    "details": {"expected": 3, "current": 4},
    "request_id": "..."
  }
}
```

### 6.3 删除/引用

```text
DELETE /api/registry/voice/profiles/{id}
  -> 409 reference_exists
     { references: [{type:"character", id:"bronya"}] }
```

已发布资源：

```text
POST /api/registry/{type}/{id}/disable
POST /api/registry/{type}/{id}/archive
```

物理删除仅允许未发布、无引用、无 revision 历史（或显式 purge 权限）。

### 6.4 发布接口

```text
POST /api/registry/{type}/{id}/publish
  body: { revision, change_note, expected_revision }
  -> 409 revision_conflict
  -> 422 validation_failed
  -> 200 published
```

---

## 7. Capability 抽象

### 7.1 Capability 与 Resource 分离

- **Capability**：能做什么，例如 `image.generate`、`voice.synthesize`。
- **Resource**：用什么做，例如某个 WorkflowTemplate、某个 VoiceProfile。
- **Provider**：实际执行者，例如 ComfyUI、Kokoro、GPT-SoVITS。
- **Gateway**：执行任务、管理 TaskStore，不决定发布状态。

```text
Agent
  │
  ▼
Capability Resolver
  │
  ├── character.select
  ├── voice.synthesize
  ├── image.generate
  ├── image.upscale
  └── image.remove_background
          │
          ▼
    Capability Registry
          │
          ▼
    Resource Resolver
          │
          ▼
    Provider / Gateway / TaskStore
```

### 7.2 最小 CapabilityManifest

```text
CapabilityManifest
  id
  version
  inputSchema
  outputSchema
  permissions
  providerTypes
  timeout
  resourceRequirements
```

第一版只需要：

- `image.generate`
- `voice.synthesize`
- 可选的 `image.upscale` / `image.remove_background`

不要现在做通用插件市场。

### 7.3 绑定关系

`WorkflowTemplate` 声明 `capabilities: ["image.generate", "image.upscale"]`。

`VoiceProfile` 自然提供 `voice.synthesize`。

`CharacterProfile` 提供 `character.select`，但角色不是媒体执行 provider；它是上下文资源。

---

## 8. 职责边界

| 模块 | 应负责 | 不应负责 |
|---|---|---|
| Core | 资源权威状态、权限、生命周期、Context Manager | 直接执行所有媒体计算 |
| Studio | 用户配置、资源管理、预览 | 保存独立业务配置 |
| Media Gateway | 媒体任务执行、TaskStore 写入、ComfyUI 调用 | 决定资源发布状态 |
| ComfyUI | 节点执行与模型计算 | 管理 Möbius 角色/资源 |
| Agent Runtime | 意图识别、能力选择、参数生成 | 绕过 Registry 直接读散落文件 |
| Adapter | 平台消息转换 | 角色、群聊、记忆业务策略 |
| TaskStore | 单次执行生命周期 | 资源定义/发布状态 |

### Registry 与 TaskStore 必须分离

```text
Capability Registry -> Workflow ID + parameters -> Media Gateway -> TaskStore
```

- Registry 记录“定义/发布/权限”
- TaskStore 记录“这次执行”
- 不要把 workflow 发布状态写进 task 状态，也不要把 task 失败当成资源不可用。

---

## 9. 修订后的实施路线

### Phase 0：架构契约

- 统一 `RegistryResource` / `RegistryRevision`
- 定义 publish/rollback/disable/archive
- 定义 Capability/Resource/Provider/Gateway/TaskStore 边界
- 定义 SQLite/PG 数据模型与迁移状态机
- 定义 API 并发/幂等/引用检查

验收：契约审查通过；不写业务页面。

### Phase 1：只读 Registry

- 索引现有角色
- 索引 `models.json`
- 索引 ComfyUI workflows
- 列表、详情、引用关系 API
- Studio 只读页面

验收：不改变运行时；所有现有资源可查询；旧路径仍在。

### Phase 2：资源管理

- 角色导入/编辑
- VoiceProfile/VoiceAsset 管理
- Workflow API Prompt 导入、四层校验、试跑
- Revision / publish / rollback
- 引用检查、软删除

验收：资源可创建、编辑、验证、发布、回滚；运行时不切换。

### Phase 3：运行时切换

- Agent 使用 published revision ID
- TTS 使用 VoiceProfile
- Gateway 使用已发布 Workflow
- 迁移状态机与旧文件回退

验收：新旧路径对照测试通过；运行时不再直接依赖散落配置；回退可用。

### Phase 4：统一能力调用

- Capability Resolver
- 权限检查
- Provider 路由
- 执行记录/审计

验收：不同 Agent 通过统一接口调用媒体能力，且不能绕过 Registry。

**阶段验收必须分开**：Phase 1 只读，Phase 2 才写入。不能为了完成 Phase 1 提前侵入 Phase 2 的写路径。

---

## 10. 最终 P0/P1 清单

### P0

- [ ] 统一资源生命周期和发布指针
- [ ] Workflow 四层验证与参数边界
- [ ] Capability/Resource/Provider/Gateway/TaskStore 边界

### P1

- [ ] Prompt Compiler 和 Lorebook Resolver
- [ ] 迁移状态机
- [ ] API 乐观锁、幂等、审计、引用检查

### 暂缓

- [ ] UI Workflow JSON 转换
- [ ] 自动安装自定义节点
- [ ] 第三方代码插件
- [ ] 通用 Capability 市场

---

## 11. 对外部项目的取舍

- [SillyTavern](https://github.com/SillyTavern/SillyTavern)：借角色卡字段、群聊策略、Prompt Inspector；不嵌入。
- [Character Card V2](https://github.com/malfoyslastname/character-card-spec-v2)：只做导入导出格式。
- [Open WebUI Functions](https://docs.openwebui.com/features/extensibility/plugin/functions/)：借插件类型/valves；不采用任意 Python 直接进 Core。
- [LangBot](https://github.com/langbot-app/LangBot)：参考平台适配/pipeline；不作为运行时依赖。
- [ComfyUI](https://github.com/Comfy-Org/ComfyUI) / [Manager](https://github.com/Comfy-Org/ComfyUI-Manager)：API Prompt + 节点依赖检查；不自动安装。
- [Letta](https://github.com/letta-ai/letta) / [Mem0](https://github.com/mem0ai/mem0) / [Graphiti](https://github.com/getzep/graphiti)：后续可选 memory provider，不进入 P0。
- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) / [Kokoro](https://github.com/hexgrad/kokoro)：VoiceProvider 的未来实现。
