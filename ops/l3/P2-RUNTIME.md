# P2 运行时组件化（L3）

> 状态：2026-11-08 完成三套 Python 运行时组件化与组件包打样；LangBot/Kokoro/ComfyUI/GSV 的组件化留到 P2b（P3 下载器前）。

## 1. 成果

### 1.1 可重定位 Python 运行时（`uv venv --relocatable`，Python 3.12）

| 组件 | 安装体积 | 7z 包体积 | SHA256 | 用途 |
|---|---|---|---|---|
| `python-core` | **26 MB** | **6.22 MB** | `39e135d3…cffcbc` | Core 8900（20 包） |
| `python-gateway` | **56 MB** | **14.75 MB** | `5b0427ea…d354ece` | Media Gateway 8899 + 记忆 MCP 8902（35 包） |
| `python-memory` | **770 MB** | **141.14 MB** | `4994565…a7a214b73` | 嵌入服务 8903（50 包，torch CPU） |

对比原环境：Core 3.44GB → 26MB；Gateway 0.09GB → 56MB；Memory 1.07GB → 770MB。
包文件位于 `D:\AI\components\`（不入库），manifest 片段在 `ops/l3/packs/`。

### 1.2 配置切换（`config.json → paths.RUNTIME`）

```json
"paths": { "RUNTIME": "D:\\AI\\mobius-runtime" }
"services": {
  "gateway": { "python": "${RUNTIME}\\python-gateway\\Scripts\\python.exe" },
  "memory":  { "python": "${RUNTIME}\\python-gateway\\Scripts\\python.exe" },
  "embed":   { "python": "${RUNTIME}\\python-memory\\Scripts\\python.exe" }
}
"launcher": { "core_python": "${RUNTIME}\\python-core\\Scripts\\python.exe" }
```

## 2. 验收证据

| 测试 | 结果 |
|---|---|
| 全栈跑在精简运行时 | ✅ 9 个服务 ready（PG/LangBot/NapCat/网关/记忆/嵌入/Kokoro/GSV/PowerContext） |
| 嵌入服务 | ✅ `POST /v1/embeddings` → 1024 维 |
| **venv 可重定位** | ✅ 把整个 runtime（938MB）复制到 `D:\AI\mobius-runtime-moved`，配置切换后全栈正常启动 |
| 组件包解压 | ✅ core 包解压出 `python-core/`，`import fastapi/uvicorn/httpx/psycopg/psutil` 通过 |
| 包 SHA256 | ✅ `ops/l3/packs/*.json` 记录 size/sha256/urls 占位 |

## 3. 组件包规范

见 `docs/L3-COMPONENTS.md`；打包脚本 `ops/l3/build_component.ps1`（7z + SHA256 + manifest 片段，junction 暂存不复制大目录）。

## 4. 遗留（P2b / P3）

| 项 | 说明 |
|---|---|
| LangBot venv | 现有 venv 指向 uv Python，需 `--relocatable` 重建后再打包 |
| Kokoro | 拆为可选组件（带 `misaki[zh]` 中文 G2P），当前仍用旧 open-webui 环境 |
| ComfyUI / GSV | 便携 Python 目录打包（排除模型），模型走 model 组件 |
| GSV supervisor | `D:\AI\gsv_supervisor.py` 的 ROOT/ADAPTER/权重路径需环境变量化 |
| 下载器 | P3 实现（分卷/续传/多源/修复/本地覆盖） |
| junction | `comfyui-models` 打包前必须解引用 E 盘 junction |
