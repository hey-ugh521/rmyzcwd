# P2b 运行时组件化（LangBot / Kokoro / ComfyUI / GSV）

> 状态：2026-11-08 完成。所有运行时组件已用可重定位方式重建并打包（7z + SHA256），
> manifest 片段汇总在 `ops/l3/packs/fragments.json`。

## 1. 成果总表

| 组件 | 安装体积 | 7z 包体积 | SHA256 前缀 | 说明 |
|---|---|---|---|---|
| `python-langbot` | 637 MB | 137.3 MB | `831f3b238284…` | LangBot 依赖（uv export，Python 3.12，可重定位） |
| `langbot` | 440 MB（源码+data/plugins，排除 .venv/logs） | 174.2 MB | `45b8521c1002…` | LangBot 应用源码 |
| `python-kokoro` | 904 MB | 145.1 MB | `2ca9383f0567…` | core-runtime + kokoro-tts（含 misaki[zh]） |
| `comfyui-runtime` | ~7 GB | **3,964.7 MB** | `833c3ccd74ce…` | 已排除 `ComfyUI\models`（校验：包内无模型） |
| `gsv-runtime` | ~13.2 GB | **7,927.4 MB** | `c897b6744e5c…` | 已排除 4 个角色权重目录（校验：0 条目） |
| `python-core` | 26 MB | 6.2 MB | `39e135d3d0b7…` | Core 环境 |
| `python-gateway` | 56 MB | 14.7 MB | `5b0427ea2576…` | 网关 + 记忆 MCP |
| `python-memory` | 770 MB | 141.1 MB | `4994565114bf…` | 嵌入服务（torch CPU） |

**8 个组件合计 12.22 GB**（包文件在 `D:\AI\components\`，不入库）。

## 2. 各项验收

### 2.1 LangBot

- 用 `uv export --frozen --no-dev` 导出依赖（4214 行，去掉 `-e .`）
- `uv venv --relocatable python-langbot` + 安装 → 637 MB
- 配置从 `uv run --no-sync main.py` 改为 `python-langbot\Scripts\python.exe main.py`
- 验收：LangBot `ready`，OneBot 与 NapCat 建立 1 条连接，插件正常加载

### 2.2 Kokoro

- 脚本从 `D:\TTS_Workspace` 收进仓库：`ops/tts/kokoro_openai_api.py`
- `MODEL_DIR` 支持 `MOBIUS_KOKORO_DIR`，默认 `<MOBIUS_ROOT>/models/kokoro`
- `python-kokoro` = core-runtime + kokoro-tts（含 `misaki[en,zh]`）→ 904 MB
- 验收：`POST /v1/audio/speech`（`zf_xiaobei`，中文）→ HTTP 200，**132 KB wav**

### 2.3 ComfyUI

- 打包 `D:\AI\comic\ComfyUI_windows_portable`，排除 `ComfyUI\models\*`、`cache\*`
- 验收：包内 `ComfyUI\models\` 条目为 0（仅 custom_nodes 下无关 readme 命中）；包 3.96 GB
- 模型（含 15 个指向 `E:\comfy-models` 的 junction）仍按模型组件/迁移处理

### 2.4 GPT-SoVITS

- `D:\AI\gsv_supervisor.py` 已环境变量化：`MOBIUS_GSV_ROOT` / `MOBIUS_GSV_ADAPTER` / `MOBIUS_GSV_LOG` / `MOBIUS_GSV_GPT_WEIGHTS` / `MOBIUS_GSV_SOVITS_WEIGHTS`（均有原值兜底）
- 打包排除 `GPT_weights_v2/v4`、`SoVITS_weights_v2/v4`
- 验收：包内 4 个角色权重目录条目均为 0；包 7.93 GB

## 3. 组件矩阵更新

`docs/L3-COMPONENTS.md` 已更新为实测体积；`ops/l3/packs/fragments.json` 为下载器 manifest 的初始数据。

## 4. 遗留

| 项 | 说明 |
|---|---|
| Kokoro 模型文件 | `MOBIUS_KOKORO_DIR`（hf_home/voices）作为 model 组件，P3 清单 |
| GSV 角色权重 | 不可再分发 → 仅迁移（manifest `byo: true`） |
| ComfyUI/GSV 配置里的路径 | 组件包内仍有部分绝对路径，解压到新机器需要安装器重写（P3/P4） |
| 下载器 | P3 实现（分卷/续传/多源/修复/本地覆盖） |
