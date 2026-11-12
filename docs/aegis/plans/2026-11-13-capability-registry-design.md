# 能力控制面设计（角色卡 / 音色 / ComfyUI 工作流）

> 状态：设计稿，未实现。  
> 范围：Core registry + Studio 控制面；不包含群聊记忆、上下文压缩、插件市场、协议适配器的完整实现。  
> 目标：先让角色、音色、生图工作流变成**可视、可验证、可版本化**的 registry，再让 Agent/Gateway 只依赖 registry，而不是散落文件。

## 1. 设计原则

1. **Core 是唯一 owner**：Studio/Console 只是控制面，不拥有第二套角色/音色/工作流配置。
2. **外部格式只做导入导出**：SillyTavern V2/V3、PNG 角色卡、ComfyUI UI/API JSON 都先归一化。
3. **原始输入不可变**：导入的原始 JSON/PNG/工作流保存为 source，编辑产生 revision，不覆盖原始件。
4. **运行时只认 registry id**：Agent 不拼文件路径，Gateway 不接受任意节点图。
5. **先可读后可写**：第一阶段可以只读索引现有文件，第二阶段再迁移为可编辑 DB。
6. **所有变更可回滚**：角色、音色、工作流都有版本和发布状态。

## 2. 三个 Registry

### 2.1 Character Registry

#### Canonical 对象

```ts
type CharacterProfile = {
  id: string;                    // slug: [a-z0-9_-]
  schemaVersion: 1;
  source: {
    format: "native" | "chara_card_v2" | "chara_card_v3" | "png_card" | "legacy_json";
    originalName?: string;
    checksum?: string;
    importedAt?: string;
    raw?: unknown;               // 原始导入数据，只读
  };

  identity: {
    name: string;
    aliases: string[];
    avatarAssetId?: string;
    tags: string[];
    creator?: string;
    version?: string;
    notes?: string;
  };

  prompt: {
    blocks: PromptBlock[];
    postHistoryInstructions?: string;
    depthPrompt?: { text: string; depth: number; role: "system" | "user" | "assistant" };
    lorebook?: Lorebook;         // character_book 归一化；第一阶段可只保存 raw
  };

  expression: {
    speechStyle?: string;
    relationStance?: string;
    taboo: string[];
    visualAnchor?: string;
    talkativeness?: number;     // 群聊激活概率 0..1
  };

  voiceProfileId?: string;
  imageDefaults: {
    modelId?: string;            // ImageModelProfile
    workflowId?: string;         // WorkflowTemplate
    checkpoint?: string;
    size?: [number, number];
    stylePrompt?: string;
    negativePrompt?: string;
    loras?: { name: string; weight: number }[];
  };

  mediaPolicy: {
    allowImage: boolean;
    allowVoice: boolean;
    allowClone: boolean;
  };

  memoryPolicy: {
    enabled: boolean;
    scope: "user" | "session" | "character" | "none";
    autoNoteExtraction: boolean;
  };

  extensions: Record<string, unknown>; // 卡内 extensions、regex 等，原样保留
  revision: number;
  published: boolean;
  createdAt: string;
  updatedAt: string;
};

type PromptBlock = {
  id: string;
  kind: "system" | "description" | "personality" | "scenario" | "greeting" | "example" | "lore" | "custom";
  title?: string;
  content: string;
  enabled: boolean;
  order: number;
  placement: "before_history" | "after_history" | "depth";
  depth?: number;
};
```

#### 导入映射

| 外部字段 | 内部字段 |
|---|---|
| `name` / `data.name` | `identity.name` |
| `description` / `data.description` | `prompt.blocks[kind=description]` |
| `personality` | `prompt.blocks[kind=personality]` |
| `scenario` | `prompt.blocks[kind=scenario]` |
| `first_mes` | `prompt.blocks[kind=greeting]` |
| `alternate_greetings` | 多个 greeting blocks |
| `mes_example` | `prompt.blocks[kind=example]` |
| `system_prompt` | `prompt.blocks[kind=system]` |
| `post_history_instructions` | `prompt.postHistoryInstructions` |
| `character_book` | `prompt.lorebook` / raw |
| `extensions` | `extensions` |
| V2/V3 `spec` | `source.format` |

#### 角色编辑页结构

```text
角色库
  ├─ 搜索 / 标签 / 收藏 / 导入角色卡
  ├─ 列表（头像、名称、声线、默认生图模型、状态）
  └─ 角色详情
       ├─ 概览：名称、别名、标签、头像、媒体权限
       ├─ 人设：描述、性格、场景、表达风格、关系立场、禁忌
       ├─ 系统提示词：PromptBlock 排序、启停、深度、版本
       ├─ 开场/示例：first message、alternate greetings、mes_example
       ├─ 世界书/扩展：lorebook、raw extensions
       ├─ 语音：voiceProfileId、试听
       ├─ 生图：默认 model/workflow、size、LoRA、风格词
       ├─ 记忆：scope、auto note 策略
       └─ 版本：revision、diff、发布/回滚
```

#### API 草案

```text
GET    /api/registry/characters
POST   /api/registry/characters
GET    /api/registry/characters/{id}
PUT    /api/registry/characters/{id}
DELETE /api/registry/characters/{id}
POST   /api/registry/characters/import      # JSON / PNG / auto
GET    /api/registry/characters/{id}/export # native / chara_card_v2 / v3
GET    /api/registry/characters/{id}/revisions
POST   /api/registry/characters/{id}/publish
POST   /api/registry/characters/validate    # dry-run，不落库
```

### 2.2 Voice Registry

#### Canonical 对象

```ts
type VoiceProvider = {
  id: string;                  // kokoro / gpt-sovits / fish-speech / f5 / ...
  name: string;
  kind: "local" | "remote";
  status: "ready" | "missing" | "error";
  capabilities: {
    clone: boolean;
    multilingual: boolean;
    streaming: boolean;
    weight: boolean;
  };
  configSchema: Record<string, unknown>;
  config: Record<string, unknown>;   // 敏感字段走 secrets
};

type VoiceProfile = {
  id: string;                  // bronya_default
  name: string;
  providerId: string;
  modelId?: string;
  speakerId?: string;
  referenceAssetId?: string;   // 克隆参考音频
  language?: string;
  speed?: number;
  weight?: number | null;      // 预留给带权重模型
  style?: string;
  enabled: boolean;
  status: "draft" | "ready" | "error";
  previewUrl?: string;
  createdAt: string;
  updatedAt: string;
};
```

#### 角色与音色关系

- 角色只存 `voiceProfileId`。
- 一个 VoiceProfile 可被多个角色复用。
- `voiceProfileId` 缺失时使用 `tts.defaultProfileId`。
- Agent/TTS 调用只解析 profile → provider/model/speaker，不直接读角色卡 engine 字段。
- 旧角色卡 `voice.{engine,voice,speed}` 在迁移时自动生成 legacy VoiceProfile。

#### 音色页结构

```text
语音
  ├─ Provider 状态（Kokoro / GPT-SoVITS / Fish Speech / F5）
  ├─ 音色档案
  │    ├─ 名称、provider、model、speaker/ref audio
  │    ├─ 语言、speed、weight、style
  │    ├─ 试听 / 重新生成预览
  │    └─ 被哪些角色引用
  ├─ 素材库：参考音频、模型文件、权重
  └─ 默认音色 / fallback 设置
```

#### API 草案

```text
GET    /api/registry/voice/providers
POST   /api/registry/voice/providers/{id}/test
GET    /api/registry/voice/profiles
POST   /api/registry/voice/profiles
PUT    /api/registry/voice/profiles/{id}
DELETE /api/registry/voice/profiles/{id}
POST   /api/registry/voice/profiles/{id}/preview
POST   /api/registry/voice/assets/import
```

### 2.3 Workflow Registry（ComfyUI）

#### Canonical 对象

```ts
type WorkflowTemplate = {
  id: string;
  name: string;
  version: number;
  format: "api_prompt" | "ui_workflow";
  source: {
    raw: unknown;              // 原始 JSON，不可变
    checksum: string;
    importedAt: string;
  };
  graph: Record<string, unknown>; // 归一化后的 API prompt 图
  parameters: WorkflowParameter[];
  injectors: WorkflowInjector[];   // 参数 -> 节点输入
  requirements: {
    nodeClasses: string[];     // 依赖的自定义节点
    models: string[];
    vramGb?: number;
    timeoutSec?: number;
  };
  capabilities: ("txt2img" | "img2img" | "controlnet" | "ipadapter" | "upscale" | "removebg")[];
  output: { kind: "image" | "image_batch"; filenamePrefix?: string };
  status: "draft" | "valid" | "missing_nodes" | "invalid" | "disabled";
  validation: {
    checkedAt?: string;
    missingNodes: string[];
    warnings: string[];
    errors: string[];
  };
  published: boolean;
};

type WorkflowParameter = {
  key: string;                 // prompt / negative / seed / width / height / steps / cfg
  type: "string" | "number" | "boolean" | "enum";
  required: boolean;
  default?: unknown;
  min?: number;
  max?: number;
  description?: string;
};

type WorkflowInjector = {
  parameter: string;
  nodeId: string;
  input: string;
};
```

#### 导入流程

```text
拖入 JSON
  -> 识别 UI workflow / API prompt
  -> 解析节点和连接
  -> 调 ComfyUI /object_info 检查节点
  -> 找输出节点与可注入参数
  -> 生成参数 schema 和 injectors
  -> 缺失自定义节点 / 模型告警
  -> 保存 draft
  -> 试跑
  -> publish
```

导入必须明确：

- **UI workflow JSON 不能直接执行**，需要转换/导出 API format。
- 只允许 registry 中已发布模板被 Agent 调用。
- 不允许 LLM 提交任意节点图；LLM 只能选模板、填参数。
- 缺失自定义节点不自动 pip/install；交给 ComfyUI-Manager 或人工安装。

#### 工作流页结构

```text
工作流
  ├─ 模板列表（状态、能力、显存、使用次数）
  ├─ 导入向导
  │    ├─ 源文件 / API format 检查
  │    ├─ 节点依赖检查（缺失节点列表）
  │    ├─ 参数映射（prompt/negative/seed/size/...）
  │    ├─ 试跑（不发布）
  │    └─ 发布
  ├─ 模板详情：节点摘要、参数 schema、依赖、版本
  └─ 运行记录：任务 id、耗时、失败原因
```

#### API 草案

```text
GET    /api/registry/workflows
POST   /api/registry/workflows/import
POST   /api/registry/workflows/validate
GET    /api/registry/workflows/{id}
PUT    /api/registry/workflows/{id}
POST   /api/registry/workflows/{id}/test
POST   /api/registry/workflows/{id}/publish
GET    /api/registry/workflows/{id}/versions
```

## 3. 存储与迁移

### 3.1 首选形态

- 结构化数据：Core DB（PostgreSQL/SQLite 兼容）表。
- 原始导入件、图片、音频、工作流 JSON：外部数据目录 `data/registry/` 或 DB blob。
- 敏感字段：secrets，不进 registry JSON。

### 3.2 表草案

```text
characters(id, schema_version, identity_json, prompt_json, expression_json,
           voice_profile_id, image_defaults_json, media_policy_json,
           memory_policy_json, extensions_json, source_json,
           revision, published, created_at, updated_at)

character_revisions(character_id, revision, snapshot_json, created_at, note)

voice_providers(id, name, kind, status, capabilities_json, config_json)
voice_profiles(id, name, provider_id, model_id, speaker_id, reference_asset_id,
               language, speed, weight, style, status, preview_url, created_at, updated_at)
voice_assets(id, kind, path, checksum, metadata_json)

workflow_templates(id, name, version, format, graph_json, source_json,
                   parameters_json, injectors_json, requirements_json,
                   capabilities_json, output_json, status, validation_json,
                   published, created_at, updated_at)
workflow_revisions(workflow_id, version, snapshot_json, created_at, note)

image_models(id, name, workflow_id, checkpoint, aliases_json, capabilities_json,
             default_params_json, vram_gb, available, default_flag)
```

### 3.3 兼容迁移

- 现有 `data/characters/*.json`：导入为 legacy 角色，保留原文件，不删除。
- 现有 `models.json`：导入为 ImageModelProfile；保留文件作为导出/兼容源。
- 现有 `workflows/*.json`：登记为 workflow templates，先只读索引；用户点击“接管”后再写入 DB。
- 现有 `voice` 字段：生成 legacy VoiceProfile（`provider=kokoro`）。
- 迁移期间 API 双读：registry 优先，旧文件 fallback；写只写 registry。

## 4. 运行时接入

### Agent

- `generate_image` 只接受：
  - `modelId` / `workflowId`
  - `prompt`、`negative`、`size`、`seed`、白名单 params
- 参数由 `WorkflowTemplate.parameters` 校验。
- `character` 显式传入时才挂角色档案；普通物体图不自动挂默认角色。

### Gateway

- 新增 `/api/registry/workflows/{id}/submit` 或扩展 `/api/generate` 接受 `workflow_id`。
- Gateway 只执行已发布模板，校验 graph checksum。
- 不执行 registry 外的任意 JSON。

### TTS

- `speak` 解析 `voiceProfileId` → provider/model/speaker/ref audio。
- 当前 Kokoro 作为 legacy provider。
- 未来 GPT-SoVITS / Fish-Speech / F5 作为新 provider 接入，不改角色 schema。

## 5. Studio 页面结构

建议在现有 Studio 导航新增“能力库”分组：

```text
创作
  ├─ 创作
  ├─ 作品
  ├─ 任务
  └─ 能力库
       ├─ 角色
       ├─ 语音
       └─ 工作流
```

角色页保留现有 `/characters` 并扩展为 tab 详情页；语音 `/voices`、工作流 `/workflows` 新增。

共用组件：

- `RegistryTable`：搜索、筛选、状态、发布状态
- `ImportWizard`：拖入文件 → 格式识别 → 校验 → 预览 → 保存
- `ValidationBadge`：valid / warning / missing nodes / error
- `VersionHistory`：diff、发布、回滚
- `PreviewPanel`：角色试聊、音色试听、工作流试跑
- `ReferencePanel`：哪些角色/任务引用了当前对象

## 6. 验收标准（第一阶段）

- 可以导入 SillyTavern V2/V3 JSON / PNG 角色卡，生成 canonical 角色。
- 可以编辑系统提示词、表达、语音 profile、生图默认值。
- 可以创建/编辑音色 profile，并试听；角色可引用。
- 可以导入 ComfyUI API workflow JSON，看到缺失节点和参数映射。
- 可以发布/回滚角色、音色、工作流。
- Agent 只能使用已发布的 workflow / voice profile。
- 原始导入文件保留，registry 写入不覆盖原始件。
- Console/Studio 不出现第二套 owner。

## 7. 明确不做（本阶段）

- 不做第三方代码插件、UI 主题市场、上下文压缩、群成员记忆的完整实现。
- 不让 LLM 生成任意 ComfyUI 节点图。
- 不自动安装 ComfyUI 自定义节点。
- 不把 SillyTavern/Open WebUI/LangBot 作为运行时依赖。

## 8. 实施顺序

1. **只读 registry + API**：索引现有角色、models.json、workflows，提供列表/详情。
2. **可编辑 + 导入校验**：角色卡导入、音色 profile、workflow 导入向导。
3. **运行时切换**：Agent/ Gateway 使用 registry id；保留 legacy fallback。
4. **版本/发布/回滚**：revisions、publish、引用检查。
5. **群聊/上下文/插件**：在 registry 稳定后继续。

## 9. 架构优劣摘要

- **自建 registry**：工作量最大，但唯一 owner、可测试、可分发，适合本地单机产品。
- **直接嵌入 SillyTavern/Open WebUI**：上手快，但数据模型、UI、license、安全边界都会分裂。
- **直接依赖 LangBot**：会重新绑回我们正在拆掉的依赖。
- **LLM 直接生成节点图**：不可验证，显存和安全风险高；只允许模板 + 参数。
- **任意 UI 插件**：必须沙箱；否则会泄露 local token 或破坏升级。
