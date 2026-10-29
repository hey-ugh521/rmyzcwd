# Möbius 统一核心接口规格 v1（MVP）

> 状态：**已拍板，实现中**。本文件是唯一契约：Core / Media Gateway / Console / Open WebUI Pipe 全部按此实现。
> 变更本文件 = 变更契约，必须由 Lead 统一改并同步所有实现。

---

## 0. 一条铁律

**事实来源只有一个：Möbius Core。**
QQ、Open WebUI、Console 都只和 Core 交互；任何界面不得自存人格、记忆或任务状态。
Media Gateway 只被 Core（生产路径）或调试入口（Open WebUI 直连 MCP）调用。

---

## 1. 拓扑与端口

```
NapCat(3000/3001) ──► LangBot(5300, container) ──┐
                                                  │ OpenAI 兼容 /v1/chat/completions
Open WebUI ──► Möbius Pipe ───────────────────────┤
                                                  ▼
                                          Möbius Core (8900, host)
                                          ├── memory / persona
                                          ├── task center / audit
                                          ├── supervisor（媒体总开关 + 唤醒/休眠）
                                          └── agent loop（DeepSeek + tools）
                                                  │ REST /api/*
                                                  ▼
                                     Media Gateway (8899, host)
                                     ├── MCP 协议面  /mcp   → LangBot / Open WebUI 直连调试
                                     ├── REST 面     /api/* → Core 生产路径
                                     ├── 媒体代理    /img /audio /upload
                                     └── ComfyUI(8188) / Kokoro(7870) / F5(7871)
```

宿主机服务（全部由 Core 的 supervisor 负责启停，不由页面各自管）：

| 服务 | 端口 | 是否常驻 | 说明 |
|---|---|---|---|
| Möbius Core | 8900 | 常驻 | 必须常驻，QQ/Web 都指向它 |
| Media Gateway | 8899 | 常驻 | 轻进程，不占 GPU |
| ComfyUI | 8188 | 按模式 | 占显存，默认按需唤醒 |
| Kokoro TTS | 7870 | 按模式 | 轻量，可常驻 |
| F5/Möbius 克隆 | 7871 | 按模式 | MVP 只做状态展示，不保证可用 |

Docker：`napcat` / `langbot` / `langbot_plugin_runtime` / `meme-generator` 由 `watchdog.py` 管。

---

## 2. 媒体模式（Core 级全局状态）

`mode ∈ {off, on_demand, prewarm}`，持久化在 Core 的 SQLite，重启保留。

| 模式 | 行为 |
|---|---|
| `off` | 所有入口不可调用图像/语音；角色温和拒绝，不偷偷唤醒 |
| `on_demand` | 收到明确媒体请求 → 返回"正在唤醒" → 启动服务 → 健康检查 → 执行；闲置 `idle_sleep_sec`（默认 600s）后休眠 ComfyUI |
| `prewarm` | Core 启动时即拉起媒体服务；不因闲置休眠 |

约束：
- 模型**没有**权限自行反复启停大服务；启停只由 `mode` + 用户当前请求 + 冷却时间共同决定。
- 冷却：同一服务两次启动间隔 ≥ 30s；唤醒超时 240s；启动失败 3 次进入 5 分钟退避。

---

## 3. 身份与会话

| 概念 | 规则 |
|---|---|
| `platform` | `qq` / `web` / `console` |
| `user_id` | 平台内稳定 id：QQ 用 `qq:<uin>`，Web 用 `web:<owui_user_id>` |
| `session_id` | 会话流：QQ 用 `qq:person:<uin>` / `qq:group:<gid>`；Web 用 `web:<chat_id>` |
| 身份绑定 | 默认**不自动合并**。Console 可人工把 `web:x` 绑定到 `qq:y`；绑定后共享记忆 |

记忆按 `user_id` 归属；群聊场景下 `user_id` 仍用发言者本人。

---

## 4. Core HTTP 接口

统一前缀：`/v1`（模型侧）、`/api`（控制台侧）。默认 `127.0.0.1:8900`，监听 `0.0.0.0` 以便 LangBot 容器经 `host.docker.internal` 访问。

鉴权：`Authorization: Bearer <core.api_key>`。来自回环地址的请求免鉴权（Console 同源）。

### 4.1 模型侧（OpenAI 兼容，给 LangBot）

`POST /v1/chat/completions`

- 请求：标准 OpenAI 格式；`messages`、`stream`、`model`（忽略内容，仅回显）。
- 身份：请求头 `X-Mobius-User` / `X-Mobius-Session` 优先；缺失则取 `body.user`；再缺省 `qq:unknown`。
- 返回（非流式）：
```json
{"id":"chatcmpl-x","object":"chat.completion","created":0,
 "model":"mobius-core",
 "choices":[{"index":0,"message":{"role":"assistant","content":"..."},"finish_reason":"stop"}],
 "usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}}
```
- 返回（流式）：`data: {...}` 增量，末行 `data: [DONE]`。
- **回复正文即媒体载荷**：图片用 `![](http://host.docker.internal:8899/img?filename=xxx.png)`，语音在最后一行原样放 `http://host.docker.internal:8899/audio?file=xxx.wav`。
  这是 LangBot 现有 respback 补丁已经认的格式，**不要改**。

`GET /v1/models` → `{"object":"list","data":[{"id":"mobius-core","object":"model"}]}`

### 4.2 原生流式对话（给 Console / Pipe）

`POST /v1/chat`  `Accept: text/event-stream`

```json
{"source":"web","user_id":"web:u1","session_id":"web:c1","text":"...","character":"bronya"}
```

SSE 事件：

| event | data |
|---|---|
| `meta` | `{"reply_id":"r_xxx"}` |
| `text` | `{"delta":"..."}` |
| `task` | 完整 task 对象 |
| `done` | `{"reply":"...","task_ids":["t_xxx"]}` |
| `error` | `{"error":"code","message":"..."}` |

### 4.3 状态 / 媒体

- `GET /api/status`
```json
{"core":{"ok":true,"uptime_sec":0},
 "media":{"mode":"on_demand","idle_sleep_sec":600,"prewarm_on_start":false,
          "active_tasks":0,"seconds_since_activity":0,
          "services":{"comfyui":{"state":"down|starting|ready|busy|error|disabled","port":8188,"last_check":0,"pid":null,"uptime_sec":0,"error":null},
                      "gateway":{"state":"ready","port":8899},
                      "kokoro":{"state":"ready","port":7870},
                      "f5":{"state":"disabled","port":7871}}},
 "counters":{"succeeded":0,"failed":0,"canceled":0,"running":0,"queued":0,"total":0},
 "langbot":{"reachable":true}}
```
> `counters` 是**今日**按状态计数；`state=disabled` 表示该服务在配置里关闭。
- `PUT /api/media/mode` `{"mode":"on_demand"}`
- `POST /api/media/wake` → 启动并等待 `{"ok":true,"state":"ready"}`；关闭模式时 `409 media_disabled`
- `POST /api/media/sleep` → 停 ComfyUI（不动 Core/Gateway）
- `PUT /api/media/config` `{"idle_sleep_sec":600,"prewarm_on_start":false}`

### 4.4 任务中心

Task 对象（统一 ID，所有界面共享）：

```json
{"id":"t_ab12cd34","kind":"image|voice","status":"queued|running|succeeded|failed|canceled",
 "source":"qq|web|console","user_id":"...","session_id":"...",
 "character":"bronya","prompt":"...","params":{},
 "result":{"url":"http://host.docker.internal:8899/img?filename=...","filename":"...","seconds":28.4},
 "error":null,"remote_id":"g_xxx",
 "created_at":0,"started_at":0,"finished_at":0}
```

- `GET /api/tasks?limit=50&status=&source=`
- `GET /api/tasks/{id}`
- `POST /api/tasks/{id}/cancel`
- `GET /api/tasks/{id}/events`（SSE，可选）

### 4.5 角色档案

```json
{"id":"bronya","name":"布洛妮娅","aliases":["板鸭","布洛妮亚"],
 "checkpoint":"Illustrious-XL-v1.0.safetensors",
 "loras":[{"name":"Bronya_Zaychik24in1_Hi3_IL-03.safetensors","weight":0.75}],
 "workflow":"portrait","size":[832,1216],
 "style_prompt":"masterpiece, best quality, ...",
 "negative_prompt":"...",
 "voice":{"engine":"kokoro","voice":"zf_xiaobei","speed":1.0},
 "media_policy":{"allow_image":true,"allow_voice":true,"allow_clone":false},
 "constitution_id":"bronya"}
```

- `GET /api/characters` / `GET /api/characters/{id}` / `PUT /api/characters/{id}`（写回 `data/characters/{id}.json`，热生效）
- 角色宪法：`GET /api/characters/{id}/constitution` → `{active, versions[], fallback}`（`active` 可能为 null，此时用 `fallback`）；`PUT` 同上（版本化，仅 Console 可写）
- 代理网关的枚举接口（Console 用）：`GET /api/loras?q=`、`GET /api/models`、`GET /api/voices`

### 4.6 记忆

四层，物理落在 `data/mobius.db`：

| 层 | 表 | 写入方 |
|---|---|---|
| 角色宪法 | `constitution` | 仅 Console，版本化，模型不可写 |
| 用户偏好/关系 | `user_memory` | 模型只能写 **候选**；Console 审批后生效 |
| 事件/剧情时间线 | `timeline` | Core 自动追加（可归档） |
| 临时工作记忆 | `turns` | Core 自动，按 TTL 过期 |

- `GET /api/memory/users` → `{"users":[{"user_id":"qq:123","n":4,"last":0}]}`
- `GET /api/memory/user/{user_id}` → `{"user_id","profile":[...],"timeline":[...],"candidates":[...]}`
- `GET /api/memory/candidates?status=pending` → 候选记忆列表
- `POST /api/memory/candidates/{id}/approve` / `/reject`
- `POST /api/memory/forget` `{"user_id":"...","key":"..."}` → 软删除
- `POST /api/memory/timeline` 手工补录事件

候选记忆对象：
```json
{"id":"m_xxx","user_id":"qq:123","character":"bronya","key":"称呼","value":"希望被叫博士",
 "confidence":0.7,"status":"pending","source":"qq:person:123#msgid","evidence":"原话","created_at":0}
```

### 4.7 审计

`GET /api/audit?limit=100&kind=image|voice|memory|tool`
```json
{"id":"a_xxx","ts":0,"actor":"qq:123","character":"bronya","action":"image.generate",
 "detail":{"loras":["Bronya..."],"workflow":"portrait","seconds":28.4},"result":"ok|fail","error":null,"task_id":"t_xxx"}
```

### 4.8 Console

`GET /console` → 单页应用（无构建步骤，原生 JS）。六个视图：概览 / 任务 / 角色 / 记忆 / 审计 / 设置。

---

## 5. Media Gateway 契约

同一个进程、同一份状态，对外**两个面**。

### 5.1 REST 面（Core 生产路径）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/status` | 各后端可用性 + 队列 + 设备 |
| GET | `/api/models` | checkpoint 列表 |
| GET | `/api/loras?q=` | LoRA 列表（含 `name/size/mtime/tags`） |
| GET | `/api/characters` | 读 `data/characters/*.json` |
| GET | `/api/voices` | Kokoro 音色 + 克隆音色 |
| POST | `/api/generate` | 见下 |
| POST | `/api/tts` | `{text,voice,engine,speed}` → `{ok,audio_url,filename,seconds}` |
| POST | `/api/upload` | multipart `file` → `{ok,upload_id,url}` |
| GET | `/api/tasks` | 网关内任务列表 |
| GET | `/api/tasks/{id}` | 单个任务 |
| POST | `/api/tasks/{id}/cancel` | 取消（从队列删 / 中断） |
| POST | `/api/wake` | 触发 ComfyUI 唤醒（内部转发到 Core `/v1/media/ensure`） |

`POST /api/generate`
```json
{"character":"bronya","prompt":"在雪地里","negative":"","workflow":"portrait",
 "loras":[{"name":"Bronya_Zaychik24in1_Hi3_IL-03.safetensors","weight":0.75}],
 "width":832,"height":1216,"seed":null,"reference_image":"upload_xxx.png",
 "async":true}
```
返回 `{"ok":true,"task_id":"g_xxx","status":"queued"}`；`async=false` 时直接等到结果返回
`{ok,filename,url,seconds,seed,checkpoint,workflow,loras,prompt,negative,width,height,character}`。

> **可复现约定（2026-10-29 起）**：`seed` 未传时由网关在**提交前**生成并落库，
> 结果与任务的 `params`/`result` 都会带上实际使用的 seed 和完整参数。
> 用同样的 seed + 同样的参数重发，ComfyUI 会命中计算图缓存直接返回同一张图（1 秒级），
> 清缓存后重算也是像素级相同。

规则：
- 显式 `character` 时，checkpoint / loras / workflow / size 从角色档案补齐，显式参数覆盖档案。
- `character` 接受 **id / 中文名 / 别名**（如 `bronya`、`布洛妮娅`、`板鸭`、`幽兰黛尔`），
  匹配逻辑见 `find_character()`；完整列表用 `GET /api/characters`。
- 角色档案可以在 `data/characters/*.json` 里追加 `constitution` / `speech_style` /
  `relation_stance` / `taboo` / `visual_anchor` 字段，Core 会用人格宪法优先读它。
- 无 LoRA、无角色时，行为和旧版完全一致（向后兼容）。
- ComfyUI 不可达时：若 `wake` 成功则重试一次；否则返回 `{"ok":false,"error":"comfyui_unavailable","hint":"..."}`。

### 5.2 MCP 面（调试/直连）

保留旧工具名（LangBot 已有绑定不能断）：

`comfyui_generate` / `comfyui_status` / `comfyui_list_models` / `tts_speak`

新增：

| 工具 | 参数 | 说明 |
|---|---|---|
| `comfyui_list_loras` | `query?` | 列 LoRA |
| `comfyui_list_characters` | — | 列角色档案 |
| `comfyui_generate` | 扩展 `character?`, `loras?`, `reference_image_url?` | 向后兼容 |
| `comfyui_task_submit` | 同 generate | 异步提交 |
| `comfyui_task_status` | `task_id` | 查询 |
| `comfyui_task_cancel` | `task_id` | 取消 |
| `comfyui_queue_status` | — | 队列/设备 |
| `tts_list_voices` | — | 音色列表 |
| `media_status` | — | 媒体模式与服务状态 |
| `media_wake` / `media_sleep` | — | 手动唤醒/休眠 |
| `media_set_mode` | `mode` | 仅 Console/管理员 |

### 5.3 代理路由

- `GET /img?filename=` → ComfyUI `/view` 转发
- `GET /audio?file=` → TTS 输出目录
- `GET /upload/{id}` → 上传的参考图
- `GET /health` → `{"ok":true}`

---

## 6. 工作流与 LoRA 注入

旧工作流是固定图（checkpoint → KSampler），没有 LoRA 节点。注入方式：

```
CheckpointLoaderSimple(ckpt)
  → [LoraLoader × N]（链式：每个节点 model/clip 输入接上一个）
  → CLIPTextEncode ×2 → KSampler → VAEDecode → SaveImage
```

实现规则：
- 按 `loras[]` 顺序链式插入 `LoraLoader` 节点（`id` 用 `lora_1..N`），`strength_model = strength_clip = weight`。
- 重接下游 `model` / `clip` 引用（原引用形如 `["4", 0]` 的，改成新链末端）。
- 去重：同名 LoRA 只注入一次（取最大权重）。
- 权重区间 0–1.5，越界截断并记录告警。
- 兼容旧格式：工作流文件是 `{"nodes": {...}}`（dict，不是 list）。

---

## 7. 配置

`mobius/config.json`（不入库）：

```json
{
  "core": {"host":"0.0.0.0","port":8900,"api_key":"<REDACTED>","idle_sleep_sec":600,"prewarm_on_start":false},
  "llm": {"base_url":"https://api.deepseek.com/v1","api_key":"sk-...","model":"deepseek-flash","timeout_sec":120},
  "gateway": {"url":"http://127.0.0.1:8899"},
  "services": {
    "comfyui": {"enabled":true,"python":"D:\\AI\\comic\\ComfyUI_windows_portable\\python_embeded\\python.exe",
                "cwd":"D:\\AI\\comic\\ComfyUI_windows_portable",
                "args":["-s","ComfyUI\\main.py","--windows-standalone-build","--fp16-vae","--preview-method","taesd","--port","8188"],
                "health_url":"http://127.0.0.1:8188/system_stats","start_timeout_sec":240},
    "kokoro": {"enabled":true,"python":"D:\\Anaconda3\\envs\\open-webui\\python.exe",
               "cwd":"D:\\TTS_Workspace","args":["kokoro_openai_api.py","--port","7870"],"port":7870},
    "f5": {"enabled":false,"port":7871},
    "gateway": {"enabled":true,"python":"D:\\AI\\qqbot-mcp\\Scripts\\python.exe",
                "cwd":"...mobius", "args":["media_gateway\\server.py","--port","8899"],"port":8899}
  },
  "characters_dir":"data/characters",
  "db":"data/mobius.db"
}
```

环境变量可覆盖（`MOBIUS_CONFIG`）。

---

## 8. Agent 工具集（Core 内循环，最多 4 轮）

| 工具 | 作用 | 副作用 |
|---|---|---|
| `recall(query)` | 召回相关记忆 | 无 |
| `remember(key,value,confidence)` | 写候选记忆 | 候选，不直接生效 |
| `generate_image(character?,prompt,loras?)` | 提交生图任务 | 建 task，消耗 GPU |
| `speak(text,voice?,engine?)` | 合成语音 | 建 task |

Prompt 注入顺序（system）：
`角色宪法（见 §4.5）→ 用户已确认记忆 → 最近 N 轮对话 → 工具使用规则 → 媒体模式提示`
原始 `messages` 里 LangBot 送来的 system prompt **保留**，放在最前。

拒绝规则：
- `mode=off` 且用户请求媒体 → 直接文本拒绝，不调工具。
- 同一用户 60s 内重复同类请求 → 复用进行中的 task，不重复提交。
- 单轮最多 1 张图 + 1 条语音。

---

## 9. 文件归属（并行开发边界）

| 路径 | 归属 | 说明 |
|---|---|---|
| `mobius/core/*.py`、`mobius/docs/*` | Lead | Core、文档、验收 |
| `mobius/media_gateway/**` | 队友 media-gateway | MCP v2 + REST 面 |
| `mobius/core/console/**` | 队友 console-ui | 控制台前端 |
| `mobius/openwebui/mobius_pipe.py` | 队友 console-ui | Open WebUI Pipe |
| `mobius/scripts/*`、`tests/*` | Lead | 启停、验收 |
| `qqbot/watchdog.py` | Lead | 改为只管 Docker + Core 存活 |

---

## 10. 明确不做（MVP 范围外）

- F5/Möbius 克隆链路修复（只做状态展示与占位）
- ControlNet / IP-Adapter 工作流接线
- 多用户权限体系、OAuth
- 记忆向量检索（MVP 用 SQL + 关键词）
- 自动身份合并
