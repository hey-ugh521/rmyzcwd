# 画笔（生图底模）接入与验收

本目录维护 Möbius 生图底模的接入与验收。网关侧模型注册表在
`D:\AI\comic\workflows\models.json`，工作流模板在 `D:\AI\comic\workflows\comic-*.json`。

## 当前画笔

| id | 底座 | 定位 | 8GB 实测耗时 | 角色/LoRA |
|---|---|---|---|---|
| `anime_illustrious` | Illustrious XL v1.0 (SDXL ckpt) | 二次元 / 角色一致性（12 张角色卡） | 约 70-81s | ✅ / ✅ |
| `qwen_image_2_1` | 千问 Image 2.1 Turbo (int8) | 默认画笔：写实 / 通用 | 约 70s | ❌ / ❌ |
| `flux3_klein` | FLUX 3 Klein (int8) | 写实 / 电影感 / 文字渲染 | 约 61-91s | ❌ / ❌ |
| `animagine_xl_3_1` | Animagine XL 3.1 (SDXL UNET) | 通用二次元插画 | 约 81-91s | ❌ / ❌ |
| `flux1_schnell` | FLUX.1 Schnell GGUF Q4 | **本机禁用**：8GB 显存反复 offload，768×768 4 步 >9 分钟且会中断 | — | ❌ / ❌ |
| `minimax_h3` | MiniMax H3（视频+音频） | **本机不可用**（需 ≥24GB 显存） | — | ❌ / ❌ |

> 耗时是 `comfyui_task_submit` → `completed` 的实测值（2026-11-06，8GB 显存，含模型加载）。
> 只有 `anime_illustrious` 支持 character 角色卡与 LoRA；其余底模传 character/loras/checkpoint 会返回明确错误。
> `flux1_schnell` 工作流已接入（`comic-flux1.json`），但在 8GB 机器上 `available: false`；换 ≥12GB 显存后可改回 true 启用。

## 新增一支画笔的步骤

1. 把底模放进 ComfyUI 对应目录（`checkpoints` / `unet` / `diffusion_models` / `clip` / `vae` / `text_encoders`）
2. 复制一个相近的 `comic-*.json`，改加载器与文件名，保存为 `comic-<name>.json`
3. `media_gateway/server.py` 的 `WORKFLOW_MAP` 加一行；若是不经 CheckpointLoaderSimple 的架构，加入 `MODEL_WORKFLOWS`
4. `models.json` 加一条：`id/name/aliases/workflow/size/params/style/supports_character/supports_lora/notes/timeout_sec`
5. 重启网关 → 跑验收

## 验收

```powershell
D:\AI\qqbot-mcp\Scripts\python.exe ops\image\acceptance.py
```

- 产物：`ops/image/acceptance.csv` + `ops/image/acceptance-report.md`
- 判据：A1 注册表可见；A2 生成 completed；A3 产物可下载且尺寸匹配；A4 不支持角色/LoRA 的底模被明确拒绝；A5 证据落盘
- 可选：`--only flux1_schnell,animagine_xl_3_1` 只测指定画笔；`--no-boundary` 跳过边界检查

## 注册表字段说明（对 bot 生效的部分）

`comfyui_list_models` 会把这些字段原样给 agent：
`id / name / aliases / workflow / style / size / supports_character / supports_lora / available / timeout_sec / notes`

- `notes` 是 bot 选画笔的主要依据，要写清"适合什么 / 多快 / 限制"
- `available: false` 的模型会被网关直接拒绝（`model_unavailable`），避免误触发长时间失败
- `timeout_sec` 是该底模的任务等待上限（默认取 `MOBIUS_TASK_TIMEOUT=900`）

## LoRA

- 角色 LoRA 只在 Illustrious 底座生效（SDXL 系）
- 迷迭香 LoRA 适配说明见 `docs/prompts/` 与角色卡 `data/characters/rosmontis.json`

