# Möbius Media Gateway v2

单进程同时提供三张脸（契约：`mobius/docs/ARCHITECTURE.md` §1 / §5 / §6 / §7）。

| 面 | 入口 | 用途 |
|---|---|---|
| MCP（Streamable HTTP） | `/mcp` | LangBot / Open WebUI 直连调试，保留旧工具名 |
| REST | `/api/*` | Möbius Core 生产路径 |
| 媒体代理 | `/img` `/audio` `/upload/{id}` `/health` | 图片 / 语音 / 参考图转发 |

## 启动

必须使用装有 `mcp 2.x` 的解释器（`D:\AI\qqbot-mcp\Scripts\python.exe`，Python 3.12）：

```powershell
cd C:\Users\lenovo\Desktop\_说明文档\mobius\media_gateway
D:\AI\qqbot-mcp\Scripts\python.exe server.py --port 8899
```

调试端口用 8898（8899 由旧版 MCP / watchdog 占用时）：

```powershell
D:\AI\qqbot-mcp\Scripts\python.exe server.py --port 8898
```

环境变量：`MOBIUS_CONFIG`、`MOBIUS_COMFYUI_URL`、`MOBIUS_KOKORO_URL`、`MOBIUS_F5_URL`、
`MOBIUS_CORE_URL`、`MOBIUS_CORE_API_KEY`、`MOBIUS_WORKFLOWS_DIR`、`MOBIUS_CHECKPOINTS_DIR`、
`MOBIUS_LORAS_DIR`、`MOBIUS_COMFY_OUTPUT_DIR`、`MOBIUS_GATEWAY_PUBLIC_URL`、
`MOBIUS_COMFY_START_TIMEOUT`、`MOBIUS_VIEW_TIMEOUT`。

## REST

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | `{"ok":true}` |
| GET | `/api/status` | 后端可用性 + 队列 + 设备 |
| GET | `/api/models` | checkpoint 列表 |
| GET | `/api/loras?q=` | LoRA 列表（name/size/mtime/tags） |
| GET | `/api/characters`、`/api/characters/{id}` | 角色档案 |
| GET | `/api/voices` | Kokoro 音色 + 克隆音色 |
| POST | `/api/generate` | 生图，`async` 默认 true；`async=false` 同步等待 |
| POST | `/api/tts` | `{text,voice,engine,speed,character?}` → `{ok,audio_url,filename,seconds}` |
| POST | `/api/upload` | multipart `file` → `{ok,upload_id,url}` |
| GET | `/api/tasks?limit=&status=&source=` | 网关任务列表 |
| GET | `/api/tasks/{id}` | 单个任务 |
| POST | `/api/tasks/{id}/cancel` | 取消（排队删除 / 已提交则中断 ComfyUI） |
| POST | `/api/wake` | 触发 Core `/v1/media/ensure` 唤醒 ComfyUI |

## MCP 工具

旧工具名（LangBot 绑定，不能改）：`comfyui_generate`、`comfyui_status`、`comfyui_list_models`、`tts_speak`。

新增：`comfyui_list_loras`、`comfyui_list_characters`、`comfyui_task_submit`、`comfyui_task_status`、
`comfyui_task_cancel`、`comfyui_queue_status`、`tts_list_voices`、`media_status`、`media_wake`、
`media_sleep`、`media_set_mode`。

## 关键实现

- **LoRA 链式注入**：`CheckpointLoaderSimple → LoraLoader(lora_1) → … → LoraLoader(lora_N)`；
  同名去重取最大权重；权重截断到 0–1.5；下游 `model`/`clip` 引用重接到链末端，VAE 引用保持不变。
- **角色档案**：显式参数 > `data/characters/{id}.json` > 工作流默认；
  `character` 自动补 checkpoint / loras / workflow / size / style_prompt / negative_prompt。
- **异步任务队列**：内存 FIFO，并发 1；状态 `queued/running/succeeded/failed/canceled`；
  取消排队任务直接移出队列，已提交 ComfyUI 的任务从 `/queue` 删除 pending 或 `/interrupt`。
- **自动唤醒**：ComfyUI 不可达时 `POST {core}/v1/media/ensure`（兼容 `/api/media/wake`），
  成功则等待 `/system_stats` 就绪并重试一次；失败返回 `comfyui_unavailable` + hint。

## 自测

```powershell
D:\AI\qqbot-mcp\Scripts\python.exe test_gateway.py --base http://127.0.0.1:8898
```

覆盖：列模型 / 列 LoRA / 无 LoRA 生图 / 带 LoRA 生图（真实 PNG + 字节数）/ character 解析 +
异步轮询 / 取消（排队 + 运行中）/ TTS / 上传 / MCP 工具清单。报告写入 `logs/test_report.json`。

## 已知限制（MVP，契约 §10）

- `panel`（FaceID + ControlNet 多参考图）工作流不接线，返回 `workflow_unsupported`。
- `upscale` / `removebg` 支持通过 `reference_image` 注入单张 LoadImage，但依赖的放大/抠图模型需 ComfyUI 侧已装。
- 任务队列为内存态，网关重启后任务列表清空（Core 才是任务中心事实源）。
- `/api/tasks/{id}/events`（SSE）未实现（契约标为可选）。
- `media_sleep` / `media_set_mode` 仅转发 Core，网关不自行启停 ComfyUI。
