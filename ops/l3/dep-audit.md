# L3 运行时组件包依赖审计（只读）

> 日期：2026-11-08 ｜ 范围：Core / Gateway / Memory 三套 Python 运行时 ｜ 属性：只读分析
>
> 方法：仓库运行时文件 AST import 扫描 → 顶层模块映射到 distribution → `importlib.metadata` 依赖闭包（含 marker 与 extras 展开）→ 按 `RECORD` 文件大小汇总体积。
> 只读声明：**未**安装/卸载/升级任何包，**未**启停任何服务，**未**修改三套环境；仅在仓库 `ops/l3/` 写本报告（临时采集脚本与数据最后已清理）。

---

## 0. 结论摘要

| # | 结论 |
|---|---|
| 1 | **Core 进程本身只需要 20 个包（约 33.7 MB）**，而不是当前的 350 个包 / 3.37 GB。当前 open-webui 环境里 **2.33 GB、252 个包**与 Möbius Core 无关（Open WebUI/Gradio/RAG、科学计算、云 SDK、训练脚本遗留）。 |
| 2 | 体积大头是 **Kokoro TTS**：`config.json` 里 Kokoro 以 `autostart=true` 用 Core 环境解释器拉起，`kokoro → misaki[en] → spacy/torch/transformers` 使 Core 环境实际必需集从 20 MB 膨胀到 **约 1.20 GB**（其中 torch 500 MB）。 |
| 3 | **Gateway 已经接近最小**：34 个包 + `psycopg-binary` ≈ **71.9 MB**；可删的只有 `pip` 10.9 MB、`pycdlib` 2.1 MB。`pywin32`（19.4 MB）与 `cryptography`（10.5 MB）是 `mcp 2.3` 的硬依赖，删不掉。 |
| 4 | **Memory 环境已为目标用途定制**：50 个包 ≈ **1.01 GB**；可删的只有 Graphiti 冒烟栈（openai/posthog/redis/neo4j/graphiti-core/FalkorDB 等）**57.3 MB**。真正的降本手段是去掉 torch（改 ONNX 嵌入），属于改代码，不是删包。 |
| 5 | 三套环境合计：**现状 4.59 GB → 精简后约 2.43 GB（省 ~2.16 GB，约 47%）**；若把 Kokoro 拆成可选组件，极简核心的 Python 环境从 3.53 GB 降到 **0.22 GB**（Core 0.09 + Gateway 0.13）。 |
| 6 | 三套 venv 的基底都不可分发：Gateway venv 指向 `C:\Users\lenovo\.dsh\...\python.exe`（DSH 自带运行时），Memory venv 指向 `D:\Anaconda3\envs\open-webui\python.exe`（就是 Core 环境）。**L3 必须用 `uv venv --relocatable` 重建**，否则重装 Core 环境会连带弄坏记忆环境。 |

---

## 1. 范围与方法

### 1.1 运行时映射（审计依据）

三套环境的"谁跑什么"不是猜的，来自 `config.json`、`ops/watchdog/watchdog.py` 常量、`docs/STATUS.md`，并与现场进程核对：

| 环境 | 解释器 | 实际由它运行的入口 | 证据 |
|---|---|---|---|
| **Core** | `D:\Anaconda3\envs\open-webui\python.exe`（3.11） | `core.server`；`ops/watchdog/watchdog.py`、`ops/pipeline_guard.py`、`ops/probe/probe_state.py`、`ops/pg_backup.py`、`ops/sqlite_backup.py`、`ops/sync_watchdog.py`、`ops/powercontext/provision.py`；**Kokoro TTS**（`D:\TTS_Workspace\kokoro_openai_api.py`，7870） | config.json:50/86/244；watchdog.py:57/560/1284；watchdog.py:62/1247 |
| **Gateway** | `D:\AI\qqbot-mcp\Scripts\python.exe`（3.12） | `media_gateway/server.py`（8899）；`ops/memory/memory_mcp.py`（8902）；`ops/mcp_ops_server.py`（8901）；`ops/warmup_media.py`；`ops/scheduled_report.py`；`ops/image/acceptance.py` | config.json:63/136；watchdog.py:47/616/647/693 |
| **Memory** | `D:\AI\mobius-memory\Scripts\python.exe`（3.11） | `ops/memory/embed_server.py`（8903，bge-m3） | config.json:119；watchdog.py:488 |

> 注意：**memory MCP（8902）跑在 Gateway 环境，不在 Memory 环境**；Memory 环境只跑嵌入服务。

### 1.2 扫描的文件集合（运行时相关）

- **Core**：`core/{__init__,agent,paths,server,services,store}.py`、`launcher/mobius_launcher.py`、`ops/watchdog/watchdog.py`、`ops/pipeline_guard.py`、`ops/probe/probe_state.py`、`ops/pg_backup.py`、`ops/sqlite_backup.py`、`ops/sync_watchdog.py`、`ops/powercontext/provision.py` + 仓库外 `D:\TTS_Workspace\kokoro_openai_api.py`
- **Gateway**：`media_gateway/{server,pg_taskstore,pollthrottle,taskstore}.py`、`ops/memory/memory_mcp.py`、`ops/mcp_ops_server.py`、`ops/warmup_media.py`、`ops/scheduled_report.py`、`ops/credentials.py`、`ops/image/acceptance.py`
- **Memory**：`ops/memory/embed_server.py`

**已排除（非运行时）**：`ops/voice_eval/`、`ops/tts/`（跑在 GSV 运行时）、`ops/yae_*`、`ops/teresa*`、`ops/kiana*`、`ops/indextts_test.py`、`ops/char_survey.py`、`ops/migrate_*.py`、`ops/p0_*`、`ops/patch_*`、`ops/patches/`、`logs/*.py`、`tests/`、`ops/tests/`、`core/console/_mock/`、`openwebui/mobius_pipe.py`（跑在 Open WebUI 进程内，当前 Open WebUI 未运行）。

### 1.3 计算口径

- **包体积**：该 distribution 在 `RECORD` 中列出的所有文件大小之和（含 `dist-info`、`Scripts/*.exe`），因此与 site-packages 目录实测值有约 1–2 MB 差异。
- **依赖闭包**：读取每个包 `Requires-Dist`，按**目标环境自己的 Python 版本**评估 marker，并展开 extras（例如 `pyjwt[crypto] → cryptography`、`misaki[zh] → pypinyin/jieba/pypinyin-dict/cn2an/ordered-set`）。
- **Python 基底**：`uv` 管理的 CPython 3.12 解压后 **62 MB**（本机实测 `%APPDATA%\uv\python\cpython-3.12-windows-x86_64-none`）；conda open-webui 环境非 site-packages 部分为 153 MB。
- 环境现状：Core 3,521 MB（site-packages 3,368.2 MB / 350 包）、Gateway 87 MB（83.4 MB / 37 包）、Memory 1,097 MB（1,093.9 MB / 72 包），与 `docs/L3-PLAN.md` 的 3.44 / 0.09 / 1.07 GB 一致。

---

## 2. 三套环境目标清单

> 表内所有条目都是**运行时必需**（即精简后应保留的闭包）。"直接 import"= 有运行时文件直接 `import`；"传递"= 依赖闭包带入。

### 2.1 Core 基础进程（20 个，33.7 MB）

| 包 | 版本 | 体积(MB) | 直接 import | 用途 / 说明 |
|---|---|---|---|---|
| `pydantic_core` | 2.46.4 | 5.5 | 传递 | 依赖：pydantic |
| `pydantic` | 2.13.4 | 3.8 | 传递 | 依赖：fastapi |
| `psycopg` | 3.3.4 | 1.8 | 是 | PostgreSQL 客户端（`core/store.py`，`db_backend=pg`） |
| `fastapi` | 0.136.3 | 1.4 | 是 | HTTP API 框架（`core/server.py`） |
| `anyio` | 4.14.2 | 1.2 | 传递 | 依赖：httpx、starlette |
| `click` | 8.5.0 | 0.9 | 传递 | 依赖：uvicorn |
| `psutil` | 7.2.2 | 0.8 | 是 | 进程存活检测（软依赖：缺失时 `core/services.py` 回退 wmic） |
| `httpx` | 0.28.1 | 0.7 | 是 | 出站 HTTP（Core→Gateway/LLM） |
| `uvicorn` | 0.51.0 | 0.7 | 是 | ASGI 服务器 |
| `starlette` | 1.6.0 | 0.6 | 传递 | 依赖：fastapi |
| `httpcore` | 1.0.9 | 0.6 | 传递 | 依赖：httpx |
| `idna` | 3.19 | 0.6 | 传递 | 依赖：anyio、httpx |
| `tzdata` | 2026.3 | 0.6 | 传递 | 依赖：psycopg |
| `typing_extensions` | 4.16.0 | 0.3 | 传递 | 依赖：anyio、fastapi |
| `certifi` | 2026.7.22 | 0.2 | 传递 | 依赖：httpcore、httpx |
| `h11` | 0.16.0 | 0.2 | 传递 | 依赖：httpcore、uvicorn |
| `typing-inspection` | 0.4.4 | 0.1 | 传递 | 依赖：fastapi、pydantic |
| `annotated-types` | 0.8.0 | 0.1 | 传递 | 依赖：pydantic |
| `annotated-doc` | 0.0.5 | 0.0 | 传递 | 依赖：fastapi |
| `psycopg-binary` | 3.3.4 | 13.5 | — | **必须保留**：`psycopg[binary]` 的 libpq 轮子；Windows 无系统 libpq。元数据上属于 extra，闭包不自动带入，重建时要写 `psycopg[binary]` |

**Core 基础进程合计：约 33.7 MB（site-packages）。** 加 Python 3.12 基底 62 MB ≈ **95.7 MB**（0.09 GB）。不含 Kokoro/语音/生图。

### 2.2 Kokoro TTS 追加集（84 个，1,161.6 MB）

> 由 `config.json services.kokoro`（`autostart=true`）与 watchdog 的 `check_tts` 以 Core 环境解释器拉起；脚本在仓库外 `D:\TTS_Workspace\kokoro_openai_api.py`。
> 若 Kokoro 被拆为独立/可选组件，以下包随组件走；若只保留中文音色，英文/多语种 G2P 组（spacy 系 ≈ 195 MB）可裁剪。

| 包 | 版本 | 体积(MB) | 直接 import | 用途 / 说明 |
|---|---|---|---|---|
| `torch` | 2.13.0 | 499.5 | 是 | Kokoro 推理后端（CPU 轮子，`torch_cpu.dll` 291 MB） |
| `spacy` | 3.8.16 | 99.6 | 传递 | 依赖：misaki[en]（英文 G2P） |
| `transformers` | 5.5.4 | 86.8 | 传递 | 依赖：Kokoro 栈（KModel 加载） |
| `sympy` | 1.14.0 | 72.1 | 传递 | 依赖：torch |
| `numpy` | 2.4.6 | 53.5 | 是 | 数值计算 |
| `babel` | 2.18.0 | 30.1 | 传递 | 依赖：misaki[en]→spacy |
| `blis` | 1.3.3 | 21.5 | 传递 | 依赖：spacy→thinc |
| `espeakng-loader` | 0.2.4 | 18.0 | 传递 | 依赖：misaki[en]（英文音素化） |
| `networkx` | 3.6.1 | 15.8 | 传递 | 依赖：torch |
| `misaki` | 0.9.4 | 14.8 | 传递 | G2P（Kokoro 依赖 misaki[en]） |
| `thinc` | 8.3.13 | 11.0 | 传递 | 依赖：spacy |
| `hf-xet` | 1.6.0 | 9.4 | 传递 | 依赖：huggingface_hub |
| `Pygments` | 2.21.0 | 8.3 | 传递 | 依赖：misaki→rich |
| `tokenizers` | 0.22.2 | 7.4 | 传递 | 依赖：transformers |
| `huggingface_hub` | 1.28.0 | 6.7 | 是 | 模型/音色下载 |
| `rdflib` | 7.6.0 | 5.1 | 传递 | 依赖：misaki[en]→spacy→csvw |
| `mpmath` | 1.3.0 | 4.6 | 传递 | 依赖：sympy |
| `srsly` | 2.5.3 | 4.5 | 传递 | 依赖：spacy |
| `setuptools` | 83.0.0 | 3.3 | 传递 | 依赖：torch（`setuptools>=77`） |
| `curated-tokenizers` | 0.0.10 | 2.8 | 传递 | 依赖：spacy-curated-transformers |
| `soundfile` | 0.13.1 | 2.4 | 是 | WAV 读写 |
| `rich` | 13.9.4 | 2.1 | 传递 | 依赖：typer |
| `joblib` | 1.5.3 | 2.0 | 传递 | 依赖：spacy→thinc |
| `regex` | 2026.5.9 | 1.8 | 传递 | 依赖：misaki |
| `fsspec` | 2026.6.0 | 1.7 | 传递 | 依赖：torch、huggingface_hub |
| `language-tags` | 1.3.1 | 1.5 | 传递 | 依赖：misaki[en]→spacy→csvw |
| `csvw` | 4.1.0 | 1.2 | 传递 | 依赖：misaki[en]→spacy |
| `Jinja2` | 3.1.6 | 1.2 | 传递 | 依赖：torch |
| `typer` | 0.27.1 | 1.1 | 传递 | 依赖：spacy |
| `cffi` | 2.1.1 | 1.1 | 传递 | 依赖：espeakng-loader |
| `jsonschema` | 4.26.0 | 1.1 | 传递 | 依赖：huggingface_hub |
| `num2words` | 0.5.14 | 1.0 | 传递 | 依赖：misaki[en] |
| `pyparsing` | 3.3.2 | 1.0 | 传递 | 依赖：rdflib |
| `spacy-curated-transformers` | 0.3.1 | 0.9 | 传递 | 依赖：misaki[en] |
| `urllib3` | 2.7.0 | 0.9 | 传递 | 依赖：requests |
| `safetensors` | 0.8.0 | 0.9 | 传递 | 依赖：transformers |
| `charset-normalizer` | 3.5.1 | 0.8 | 传递 | 依赖：requests |
| `filelock` | 3.32.4 | 0.8 | 传递 | 依赖：torch、huggingface_hub |
| `python-dateutil` | 2.9.0.post0 | 0.7 | 传递 | 依赖：rdflib |
| `PyYAML` | 6.0.3 | 0.7 | 传递 | 依赖：transformers |
| `markdown-it-py` | 4.2.0 | 0.6 | 传递 | 依赖：rich |
| `rpds-py` | 2026.6.3 | 0.6 | 传递 | 依赖：jsonschema |
| `tqdm` | 4.70.0 | 0.6 | 传递 | 依赖：huggingface_hub/transformers |
| `wrapt` | 2.3.0 | 0.5 | 传递 | 依赖：spacy→thinc |
| `phonemizer-fork` | 3.3.2 | 0.5 | 传递 | 依赖：misaki[en] |
| `cloudpathlib` | 0.25.0 | 0.5 | 传递 | 依赖：spacy |
| `requests` | 2.34.2 | 0.5 | 传递 | 依赖：spacy |
| `packaging` | 26.3 | 0.5 | 传递 | 依赖：torch/transformers |
| `loguru` | 0.7.3 | 0.5 | 传递 | 依赖：kokoro |
| `smart_open` | 8.0.1 | 0.4 | 传递 | 依赖：csvw |
| `attrs` | 26.1.0 | 0.4 | 传递 | 依赖：jsonschema |
| `pycparser` | 3.0 | 0.4 | 传递 | 依赖：cffi |
| `weasel` | 1.0.0 | 0.4 | 传递 | 依赖：spacy |
| `preshed` | 3.0.13 | 0.3 | 传递 | 依赖：spacy→thinc |
| `referencing` | 0.37.0 | 0.3 | 传递 | 依赖：jsonschema |
| `confection` | 1.3.3 | 0.2 | 传递 | 依赖：spacy |
| `rfc3986` | 1.5.0 | 0.2 | 传递 | 依赖：rdflib |
| `wasabi` | 1.1.3 | 0.2 | 传递 | 依赖：spacy |
| `spacy-legacy` | 3.0.12 | 0.2 | 传递 | 依赖：spacy |
| `segments` | 2.4.0 | 0.2 | 传递 | 依赖：csvw |
| `colorama` | 0.4.6 | 0.2 | 传递 | 依赖：tqdm/typer |
| `spacy-loggers` | 1.0.5 | 0.1 | 传递 | 依赖：spacy |
| `curated-transformers` | 0.1.1 | 0.1 | 传递 | 依赖：spacy-curated-transformers |
| `kokoro` | 0.9.4 | 0.1 | 是 | Kokoro TTS 模型/管线 |
| `isodate` | 0.7.2 | 0.1 | 传递 | 依赖：rdflib |
| `catalogue` | 2.0.10 | 0.1 | 传递 | 依赖：spacy |
| `cymem` | 2.0.13 | 0.1 | 传递 | 依赖：spacy→thinc |
| `six` | 1.17.0 | 0.1 | 传递 | 依赖：python-dateutil |
| `docopt` | 0.6.2 | 0.1 | 传递 | 依赖：spacy |
| `uritemplate` | 4.2.0 | 0.1 | 传递 | 依赖：csvw |
| `MarkupSafe` | 3.0.3 | 0.1 | 传递 | 依赖：Jinja2 |
| `murmurhash` | 1.0.15 | 0.0 | 传递 | 依赖：spacy |
| `jsonschema-specifications` | 2025.9.1 | 0.0 | 传递 | 依赖：jsonschema |
| `mdurl` | 0.1.2 | 0.0 | 传递 | 依赖：markdown-it-py |
| `shellingham` | 1.5.4 | 0.0 | 传递 | 依赖：typer |
| `termcolor` | 3.3.0 | 0.0 | 传递 | 依赖：spacy |
| `addict` | 2.4.0 | 0.0 | 传递 | 依赖：misaki |
| `win32_setctime` | 1.2.0 | 0.0 | 传递 | 依赖：loguru |
| `pypinyin-dict` | 0.9.0 | 107.6 | 传递 | **中文 G2P**：misaki[zh]（Kokoro 默认音色 `zf_xiaobei` 必需） |
| `jieba` | 0.42.1 | 41.1 | 传递 | 中文 G2P：misaki[zh] |
| `pypinyin` | 0.55.0 | 3.6 | 传递 | 中文 G2P：misaki[zh] |
| `cn2an` | 0.5.24 | 0.5 | 传递 | 中文 G2P：misaki[zh] |
| `ordered-set` | 4.1.0 | 0.0 | 传递 | 中文 G2P：misaki[zh] |

> **中文 G2P 是本次审计的重要发现**：`pip install kokoro` 只会带 `misaki[en]`，而 Kokoro 默认音色与全部 `zf_*/zm_*` 中文音色走 `misaki[zh]`，需要额外 5 个包（其中 `pypinyin-dict` 107.6 MB）。重建时必须显式加 `misaki[zh]`，否则中文语音会缺依赖。

**Kokoro 追加配合计 ≈ 1,161.6 MB**（= Kokoro 环境下闭包 1,028.9 MB − Core 基础 20.2 MB + 中文 G2P 152.9 MB）。Core 基础 + Kokoro = **1,195.4 MB**；加 62 MB 基底 ≈ **1.23 GB**。

### 2.3 Gateway（35 个，71.9 MB）

| 包 | 版本 | 体积(MB) | 直接 import | 用途 / 说明 |
|---|---|---|---|---|
| `pywin32` | 312 | 19.4 | 传递 | **mcp 2.3 的 Windows 硬依赖**（`pywin32>=311; sys_platform=='win32'`） |
| `cryptography` | 50.0.2 | 10.5 | 传递 | 依赖：PyJWT[crypto]（mcp 硬依赖） |
| `pydantic_core` | 2.50.1 | 5.8 | 传递 | 依赖：pydantic |
| `pydantic` | 2.14.1 | 3.7 | 是 | MCP 工具参数模型（`media_gateway/server.py`） |
| `mcp` | 2.3.0 | 2.5 | 是 | MCP 协议服务端（Media Gateway / memory MCP / ops MCP） |
| `psycopg` | 3.3.6 | 1.7 | 是 | PostgreSQL（任务队列、memory MCP） |
| `anyio` | 4.15.1 | 1.2 | 传递 | 依赖：httpx、httpx2 |
| `cffi` | 2.1.1 | 1.0 | 传递 | 依赖：cryptography |
| `jsonschema` | 4.26.0 | 1.0 | 传递 | 依赖：mcp |
| `httpx2` | 2.16.0 | 1.0 | 传递 | 依赖：mcp（MCP 2.x 自带 HTTP 客户端分支） |
| `click` | 8.5.0 | 0.9 | 传递 | 依赖：uvicorn |
| `uvicorn` | 0.54.0 | 0.7 | 是 | ASGI 服务器 |
| `httpx` | 0.28.1 | 0.7 | 是 | 出站 HTTP（ComfyUI/Kokoro/Core） |
| `httpcore2` | 2.16.0 | 0.6 | 传递 | 依赖：httpx2 |
| `starlette` | 1.10.0 | 0.6 | 是 | ASGI 路由/MCP 底座 |
| `mcp-types` | 2.3.0 | 0.6 | 传递 | 依赖：mcp |
| `idna` | 3.20 | 0.6 | 传递 | 依赖：anyio、httpx |
| `httpcore` | 1.0.9 | 0.6 | 传递 | 依赖：httpx |
| `rpds-py` | 2026.9.1 | 0.6 | 传递 | 依赖：jsonschema |
| `tzdata` | 2026.6 | 0.6 | 传递 | 依赖：psycopg |
| `pycparser` | 3.12 | 0.5 | 传递 | 依赖：cffi |
| `attrs` | 26.1.0 | 0.4 | 传递 | 依赖：jsonschema |
| `opentelemetry-api` | 1.45.1 | 0.4 | 传递 | 依赖：mcp |
| `typing_extensions` | 4.16.0 | 0.3 | 传递 | 依赖：anyio、httpx2 |
| `referencing` | 0.37.0 | 0.3 | 传递 | 依赖：jsonschema |
| `PyJWT` | 2.15.1 | 0.2 | 传递 | 依赖：mcp |
| `certifi` | 2026.10.23 | 0.2 | 传递 | 依赖：httpcore、httpx |
| `python-multipart` | 0.0.32 | 0.2 | 传递 | 依赖：mcp |
| `h11` | 0.16.0 | 0.2 | 传递 | 依赖：httpcore、httpcore2 |
| `truststore` | 0.10.5 | 0.1 | 传递 | 依赖：httpx2（Windows 证书库） |
| `typing-inspection` | 0.4.4 | 0.1 | 传递 | 依赖：mcp、pydantic |
| `sse-starlette` | 3.6.0 | 0.1 | 传递 | 依赖：mcp |
| `annotated-types` | 0.8.0 | 0.1 | 传递 | 依赖：pydantic |
| `jsonschema-specifications` | 2025.9.1 | 0.0 | 传递 | 依赖：jsonschema |
| `psycopg-binary` | 3.3.6 | 14.3 | — | **必须保留**：libpq 轮子（同 Core 说明） |

> `media_gateway/server.py` 里 `import psutil` 在 `try/except` 内，未安装时回退 `os.kill`，**不是必需包**（未列入清单）。

**Gateway 合计 ≈ 71.9 MB（site-packages）；加基底 62 MB ≈ 134 MB（0.13 GB）。**

### 2.4 Memory（50 个，1,038.9 MB）

| 包 | 版本 | 体积(MB) | 直接 import | 用途 / 说明 |
|---|---|---|---|---|
| `torch` | 2.15.0 | 523.8 | 传递 | 依赖：sentence-transformers（bge-m3 推理，CPU 轮子） |
| `scipy` | 1.17.1 | 132.3 | 传递 | 依赖：scikit-learn、sentence-transformers |
| `transformers` | 5.22.1 | 108.0 | 传递 | 依赖：sentence-transformers |
| `sympy` | 1.14.0 | 72.1 | 传递 | 依赖：torch |
| `numpy` | 2.4.6 | 53.5 | 传递 | 依赖：scipy/scikit-learn |
| `scikit-learn` | 1.9.1 | 42.2 | 传递 | 依赖：sentence-transformers |
| `networkx` | 3.6.1 | 15.8 | 传递 | 依赖：torch |
| `hf-xet` | 1.8.0 | 9.4 | 传递 | 依赖：huggingface_hub |
| `setuptools` | 79.0.1 | 9.1 | 传递 | 依赖：torch |
| `Pygments` | 2.21.0 | 8.3 | 传递 | 依赖：rich |
| `tokenizers` | 0.23.3 | 7.9 | 传递 | 依赖：transformers |
| `huggingface_hub` | 2.4.0 | 7.0 | 传递 | 依赖：sentence-transformers |
| `pydantic_core` | 2.50.2 | 6.0 | 传递 | 依赖：pydantic |
| `sentence-transformers` | 6.2.0 | 5.4 | 是 | bge-m3 加载与编码 |
| `narwhals` | 2.28.0 | 4.8 | 传递 | 依赖：scikit-learn |
| `mpmath` | 1.3.0 | 4.6 | 传递 | 依赖：sympy |
| `pydantic` | 2.14.2 | 3.9 | 是 | 请求/参数模型 |
| `rich` | 15.0.0 | 2.6 | 传递 | 依赖：typer |
| `joblib` | 1.6.0 | 2.0 | 传递 | 依赖：scikit-learn |
| `fsspec` | 2026.9.0 | 1.9 | 传递 | 依赖：torch、huggingface_hub |
| `regex` | 2026.9.29 | 1.8 | 传递 | 依赖：transformers |
| `fastapi` | 0.162.0 | 1.7 | 是 | HTTP API 框架（`/v1/embeddings`） |
| `anyio` | 4.16.0 | 1.3 | 传递 | 依赖：httpx2、starlette |
| `filelock` | 4.2.6 | 1.2 | 传递 | 依赖：torch、huggingface_hub |
| `Jinja2` | 3.1.6 | 1.2 | 传递 | 依赖：torch |
| `typer` | 0.27.3 | 1.1 | 传递 | 依赖：transformers |
| `httpx2` | 2.16.0 | 1.0 | 传递 | 依赖：huggingface_hub |
| `packaging` | 26.3 | 1.0 | 传递 | 依赖：transformers/huggingface_hub |
| `click` | 8.5.0 | 0.9 | 传递 | 依赖：uvicorn/typer |
| `safetensors` | 0.8.0 | 0.9 | 传递 | 依赖：transformers |
| `uvicorn` | 0.54.0 | 0.8 | 是 | ASGI 服务器 |
| `PyYAML` | 6.0.3 | 0.7 | 传递 | 依赖：transformers |
| `starlette` | 1.10.0 | 0.7 | 传递 | 依赖：fastapi |
| `httpcore2` | 2.16.0 | 0.7 | 传递 | 依赖：httpx2 |
| `idna` | 3.20 | 0.6 | 传递 | 依赖：anyio/httpx2 |
| `markdown-it-py` | 4.2.0 | 0.6 | 传递 | 依赖：rich |
| `tqdm` | 4.70.1 | 0.6 | 传递 | 依赖：transformers/sentence-transformers |
| `opentelemetry-api` | 1.45.2 | 0.4 | 传递 | 依赖：fastapi |
| `typing_extensions` | 4.16.0 | 0.3 | 传递 | 依赖：anyio/fastapi |
| `h11` | 0.16.0 | 0.2 | 传递 | 依赖：uvicorn/httpcore2 |
| `colorama` | 0.4.6 | 0.2 | 传递 | 依赖：tqdm/typer |
| `threadpoolctl` | 3.7.0 | 0.2 | 传递 | 依赖：scikit-learn |
| `cloudpickle` | 3.1.2 | 0.1 | 传递 | 依赖：joblib |
| `truststore` | 0.10.5 | 0.1 | 传递 | 依赖：httpx2 |
| `typing-inspection` | 0.4.4 | 0.1 | 传递 | 依赖：fastapi/pydantic |
| `annotated-types` | 0.8.0 | 0.1 | 传递 | 依赖：pydantic |
| `MarkupSafe` | 3.0.4 | 0.1 | 传递 | 依赖：Jinja2 |
| `mdurl` | 0.1.2 | 0.0 | 传递 | 依赖：markdown-it-py |
| `shellingham` | 1.5.4 | 0.0 | 传递 | 依赖：typer |
| `annotated-doc` | 0.0.5 | 0.0 | 传递 | 依赖：fastapi/typer |

**Memory 合计 ≈ 1,038.9 MB（site-packages，约 1.01 GB）；加 62 MB 基底 ≈ 1,100.9 MB（约 1.08 GB）。** bge-m3 模型在 `D:\AI\models\bge-m3-st`（约 2.6 GB），不计入环境体积。

---

## 3. 可删除包类别估计

### 3.1 Core 环境（可删 ≈ 2,218 MB / 246 个包）

Core+ Kokoro 必需集之外，当前环境有 **2,384.7 MB / 252 个包**未被任何运行时文件使用；其中 6 个是"必留但闭包未覆盖"的（中文 G2P 5 个 + `psycopg-binary`），扣掉后**可删 2,218.3 MB / 246 个包**：

| 类别 | 包数 | 体积 | 典型包 |
|---|---|---|---|
| 科学计算/训练/媒体处理 | 30 | **953.6 MB** | `scipy` 132.3、`llvmlite` 116.8、`bitsandbytes` 117.9、`pyarrow` 86.3、`wandb` 83.8、`pandas` 64.4、`ctranslate2` 60.5、`av` 66.8、`scikit-learn` 42.0、`onnxruntime` 40.8、`matplotlib` 33.0、`numba` 27.8、`torchcodec` 23.5 |
| Open WebUI / Web / RAG 栈 | 85 | **865.7 MB** | `open-webui` 321.6、`opencv-python`+`headless` 232.4、`gradio` 74.2、`chromadb` 67.2、`rapidocr` 31.7、`fonttools` 16.3、`pillow` 15.4、`nltk` 11.9、`lxml` 8.9、`langchain*`/`langgraph*`、`opensearch*`、office 解析系列 |
| 云 SDK / 可观测性 | 41 | **273.6 MB** | `google-api-python-client` 95.6、`kubernetes` 77.5、`boto3`+`botocore` 40+、`grpcio` 12.6、`protobuf`、`azure-*`、`msal`、`opentelemetry-*`、`sentry-sdk`、`cryptography` 10.6 |
| 其他遗留/重复 | 55 | **35.9 MB** | `pywin32` 19.7、`openai` 7.5、`redis` 5.0、`anthropic` 3.0、`mcp` 1.8（核心不用 MCP）、`aiohttp`、`pydantic-settings`、`hydra-core`/`omegaconf` |
| F5-TTS / 语音训练（`f5` 在 config 中 `enabled=false`） | 22 | **57.3 MB** | `f5-tts`、`vocos`、`encodec`、`x-transformers`、`torchaudio`、`librosa`、`en_core_web_sm` 14.5、`yt-dlp` 21.8 |
| 开发/构建工具 | 13 | **32.3 MB** | `black`、`build`、`wheel`、`pip` 5.4、`pyproject_hooks`、`tomli`、`pathspec` |
| **可删小计** | **246** | **2,218.3** | — |
| 保留（闭包未覆盖但必需） | 6 | 166.4 | `pypinyin-dict` 107.6、`jieba` 41.1、`pypinyin` 3.6、`cn2an` 0.5、`ordered-set`、`psycopg-binary` 13.5 |
| **合计** | **252** | **2,384.7** | — |

> 另：`open-webui` 包若作为"网页调试面"保留，其完整依赖闭包实测 **262 个包 / 2,492.5 MB**——比整个精简后 Core 还大，建议 L3 不做 Web UI 组件（或延后）。

### 3.2 Gateway 环境（可删 ≈ 13 MB）

| 包 | 版本 | 体积 | 说明 |
|---|---|---|---|
| `pip` | 25.0.1 | 10.9 MB | 交付 venv 不需要 pip（构建期用 uv） |
| `pycdlib` | 1.22.0 | 2.1 MB | 仓库无任何引用，疑似历史遗留 |

其余 34 个包 + `psycopg-binary` 均在闭包内。Gateway 环境本身已经很干净，没有"类别级"可删空间。

### 3.3 Memory 环境（可删 ≈ 57.3 MB）

Graphiti 冒烟栈（`ops/memory/smoke_graphiti.py` 是手工脚本，不在运行时链路）：

| 包 | 版本 | 体积 |
|---|---|---|
| `openai` | 3.42.1 | 17.0 MB |
| `pip` | 26.2.2 | 10.9 MB |
| `posthog` | 7.81.1 | 10.0 MB |
| `redis` | 8.1.0 | 5.5 MB |
| `neo4j` | 6.4.1 | 3.3 MB |
| `graphiti-core` | 0.31.2 | 3.3 MB |
| `FalkorDB` | 1.8.1 | 0.3 MB |
| 其余传递依赖（`httpx`/`httpcore`/`requests`/`urllib3`/`pytz`/`jiter`/`six`/`backoff` 等） | — | 6.3 MB |
| **合计** | 22 个包 | **57.3 MB** |

> Memory 环境 **95% 的体积落在 `sentence-transformers` 推理栈上**（torch 523.8 + scipy 132.3 + transformers 108.0 + scikit-learn 42.2 + …），删包救不了；要显著瘦身只能改实现（见 §5/§6）。

---

## 4. 精简后预计体积与重建

### 4.1 体积对比（安装占用）

| 组件 | 现状 | 精简后（site-packages） | 精简后（含 Python 基底） | 省 |
|---|---|---|---|---|
| Core 基础进程（无 TTS） | 3.44 GB 环境（3.37 GB sp） | **33.7 MB** | **约 96 MB** | ≈ 3.35 GB |
| Kokoro TTS（并入 Core 环境） | 同上 | 1,161.6 MB | — | — |
| **Core + Kokoro 同环境（维持现拓扑）** | 3.44 GB | **1,195.4 MB** | **≈ 1.23 GB** | ≈ 2.21 GB |
| Gateway | 0.09 GB | **71.9 MB** | **≈ 134 MB** | 基本不变 |
| Memory | 1.07 GB | **1,038.9 MB** | **≈ 1.08 GB** | ≈ 57 MB（可删 Graphiti 栈） |
| **三套合计（各自基底 62 MB）** | **4.59 GB** | — | **≈ 2.43 GB** | **≈ 2.16 GB（-47%）** |
| 备选：Kokoro 拆为独立可选组件 | — | Core 33.7 + Kokoro 1,161.6 | Core 96 + Kokoro 1.22 GB + GW 134 + Mem 1.08 | 极简核心 Python 环境 **3.53 GB → 0.22 GB** |
| 备选：三套共享一个 Python 基底 | — | — | 约 **2.31 GB** | 再省 62 MB |

> 口径说明：现状 Gateway/Memory 是 venv，基底 Python 在各自目录之外（Gateway 用 DSH 的 Python、Memory 用 open-webui 的 Python），所以现状 4.59 GB 不重复计基底；精简后按"每套组件自带 62 MB 基底"保守计算，若三套共享一个基底则见最后一行。

**7z 压缩后下载量粗估**（用实测压缩率校准：`torch/lib` DLL 组 351.4 MB → 95.2 MB，ratio 0.27；纯 Python 按 0.35–0.45）：

| 组件包 | 解压占用 | 压缩后估算 |
|---|---|---|
| Core 基础 | 96 MB | **15–20 MB** |
| Kokoro TTS | 1.22 GB（含自有基底）/ 1.16 GB（site-packages） | **0.37–0.41 GB** |
| Gateway | 134 MB | **45–55 MB** |
| Memory | 1.08 GB | **0.33–0.37 GB** |
| 合计 | ≈ 2.43 GB | **≈ 0.8 GB** |

> 估算仅用于容量规划，未实际打包；Kokoro 体积主要来自 `torch_cpu.dll`（291 MB）+ `spacy` + `pypinyin-dict`（数据文件）。

### 4.2 Kokoro 是 Core 环境瘦身的关键决策点

现状下 Core 环境 = 20 个包（33.7 MB）+ Kokoro 追加 84 个包（1,161.6 MB），**98% 的体积是语音**。三种方案：

| 方案 | Core 组件 | 语音组件 | 说明 |
|---|---|---|---|
| A. 维持现拓扑 | 1.23 GB | — | 改动最小，但"极简核心"被拖到 1.2 GB |
| B. **Kokoro 拆为可选 `kokoro-tts` 组件（推荐）** | 96 MB | 1.22 GB | Core 组件 <100 MB；不装语音也能文本聊天（符合 L3 §9 能力矩阵） |
| C. Kokoro 改 ONNX 实现（`kokoro-onnx`） | 96 MB | 估 0.25–0.40 GB | 可去 torch/spacy/transformers；**需改 `kokoro_openai_api.py`，未验证，属额外开发** |

> 若只能做一件事：**选 B**。它让"极简核心"从 4.8 GB 量级降到约 **1.5 GB** 量级（选 A 则约 2.6 GB），同时不改变现有语音功能。

### 4.3 建议的 requirements（由本次闭包导出，可直接冻结）

**A. `ops/l3/requirements/core-runtime.txt`**（19 行；`psycopg` 用 `[binary]`）：

```text
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.14.2
certifi==2026.7.22
click==8.5.0
fastapi==0.136.3
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
idna==3.19
psutil==7.2.2
psycopg[binary]==3.3.4
pydantic==2.13.4
pydantic_core==2.46.4
starlette==1.6.0
typing-inspection==0.4.4
typing_extensions==4.16.0
tzdata==2026.3
uvicorn==0.51.0
```

**B. `ops/l3/requirements/kokoro-tts.txt`**（Core 环境内的语音追加；与 A 一起安装）：

```text
addict==2.4.0
attrs==26.1.0
babel==2.18.0
blis==1.3.3
catalogue==2.0.10
cffi==2.1.1
charset-normalizer==3.5.1
cloudpathlib==0.25.0
colorama==0.4.6
confection==1.3.3
csvw==4.1.0
curated-tokenizers==0.0.10
curated-transformers==0.1.1
cymem==2.0.13
dlinfo==2.0.0
docopt==0.6.2
espeakng-loader==0.2.4
filelock==3.32.4
fsspec==2026.6.0
hf-xet==1.6.0
huggingface_hub==1.28.0
isodate==0.7.2
Jinja2==3.1.6
joblib==1.5.3
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
kokoro==0.9.4
language-tags==1.3.1
loguru==0.7.3
markdown-it-py==4.2.0
MarkupSafe==3.0.3
mdurl==0.1.2
misaki[en,zh]==0.9.4
mpmath==1.3.0
murmurhash==1.0.15
networkx==3.6.1
num2words==0.5.14
numpy==2.4.6
packaging==26.3
phonemizer-fork==3.3.2
preshed==3.0.13
pycparser==3.0
Pygments==2.21.0
pyparsing==3.3.2
python-dateutil==2.9.0.post0
PyYAML==6.0.3
rdflib==7.6.0
referencing==0.37.0
regex==2026.5.9
requests==2.34.2
rfc3986==1.5.0
rich==13.9.4
rpds-py==2026.6.3
safetensors==0.8.0
segments==2.4.0
setuptools==83.0.0
shellingham==1.5.4
six==1.17.0
smart_open==8.0.1
soundfile==0.13.1
spacy==3.8.16
spacy-curated-transformers==0.3.1
spacy-legacy==3.0.12
spacy-loggers==1.0.5
srsly==2.5.3
sympy==1.14.0
termcolor==3.3.0
thinc==8.3.13
tokenizers==0.22.2
torch==2.13.0
tqdm==4.70.0
transformers==5.5.4
typer==0.27.1
uritemplate==4.2.0
urllib3==2.7.0
wasabi==1.1.3
weasel==1.0.0
win32_setctime==1.2.0
wrapt==2.3.0
```

> `misaki[en,zh]==0.9.4` 会带入 `pypinyin-dict==0.9.0 / jieba==0.42.1 / pypinyin==0.55.0 / cn2an==0.5.24 / ordered-set==4.1.0`（已计入体积）。
> 若只保留中文音色（不用 `af_*/am_*/bf_*…` 英文/多语种音色），可改为 `misaki[zh]` 并用 `--no-deps` 手工裁掉 spacy 系（约省 195 MB），但会牺牲英文音色，属产品决策。

**C. `ops/l3/requirements/gateway-runtime.txt`**（34 行；`psycopg[binary]` 会带入第 35 个包 `psycopg-binary`）：

```text
annotated-types==0.8.0
anyio==4.15.1
attrs==26.1.0
certifi==2026.10.23
cffi==2.1.1
click==8.5.0
cryptography==50.0.2
h11==0.16.0
httpcore==1.0.9
httpcore2==2.16.0
httpx==0.28.1
httpx2==2.16.0
idna==3.20
jsonschema==4.26.0
jsonschema-specifications==2025.9.1
mcp==2.3.0
mcp-types==2.3.0
opentelemetry-api==1.45.1
psycopg[binary]==3.3.6
pycparser==3.12
pydantic==2.14.1
pydantic_core==2.50.1
PyJWT==2.15.1
python-multipart==0.0.32
pywin32==312
referencing==0.37.0
rpds-py==2026.9.1
sse-starlette==3.6.0
starlette==1.10.0
truststore==0.10.5
typing-inspection==0.4.4
typing_extensions==4.16.0
tzdata==2026.6
uvicorn==0.54.0
```

**D. `ops/l3/requirements/memory-embed.txt`**（50 行）：

```text
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.16.0
click==8.5.0
cloudpickle==3.1.2
colorama==0.4.6
fastapi==0.162.0
filelock==4.2.6
fsspec==2026.9.0
h11==0.16.0
hf-xet==1.8.0
httpcore2==2.16.0
httpx2==2.16.0
huggingface_hub==2.4.0
idna==3.20
Jinja2==3.1.6
joblib==1.6.0
markdown-it-py==4.2.0
MarkupSafe==3.0.4
mdurl==0.1.2
mpmath==1.3.0
narwhals==2.28.0
networkx==3.6.1
numpy==2.4.6
opentelemetry-api==1.45.2
packaging==26.3
pydantic==2.14.2
pydantic_core==2.50.2
Pygments==2.21.0
PyYAML==6.0.3
regex==2026.9.29
rich==15.0.0
safetensors==0.8.0
scikit-learn==1.9.1
scipy==1.17.1
sentence-transformers==6.2.0
setuptools==79.0.1
shellingham==1.5.4
starlette==1.10.0
sympy==1.14.0
threadpoolctl==3.7.0
tokenizers==0.23.3
torch==2.15.0
tqdm==4.70.1
transformers==5.22.1
truststore==0.10.5
typer==0.27.3
typing-inspection==0.4.4
typing_extensions==4.16.0
uvicorn==0.54.0
```

### 4.4 uv 重建命令建议

```powershell
# 0) 基底：三套共用同一个 uv 管理 Python（本机已有 3.12；若要严格保持 3.11 需 uv python install 3.11）
#    uv 的 CPython 自带 tkinter/tcl（启动器 GUI 需要），解压后约 62 MB
uv python install 3.12

# 1) Core（relocatable venv，便于整体打包/移动）
uv venv --relocatable runtime\python-core --python 3.12
uv pip install --python runtime\python-core\Scripts\python.exe -r ops\l3\requirements\core-runtime.txt
#    （仅当需要在 Core 环境内跑 Kokoro 时追加）
uv pip install --python runtime\python-core\Scripts\python.exe -r ops\l3\requirements\kokoro-tts.txt

# 2) Gateway
uv venv --relocatable runtime\python-gateway --python 3.12
uv pip install --python runtime\python-gateway\Scripts\python.exe -r ops\l3\requirements\gateway-runtime.txt

# 3) Memory
uv venv --relocatable runtime\python-memory --python 3.12
uv pip install --python runtime\python-memory\Scripts\python.exe -r ops\l3\requirements\memory-embed.txt

# 4) 复核：安装后重新冻结（manifests 里存这个，而不是本次的推导结果）
uv pip freeze --python runtime\python-core\Scripts\python.exe > ops\l3\requirements\core-runtime.lock.txt
```

离线/内网构建：`uv pip download -r ... -d wheels/` + `uv pip install --no-index --find-links wheels/`；组件包用 7z 分卷（按 `docs/L3-PLAN.md` §2.2）。

### 4.5 重建后必须验证（不做则清单不可信）

1. `runtime\python-core\Scripts\python.exe -m core.server` 在干净 config 下起 8900 并 `/health` 通；
2. `runtime\python-gateway\Scripts\python.exe media_gateway\server.py --port 8899` 起 MCP + REST，`GET /api/status` 通；
3. `runtime\python-memory\Scripts\python.exe ops\memory\embed_server.py --port 8903`，`POST /v1/embeddings` 返回 1024 维；
4. Kokoro（若装）：`python kokoro_openai_api.py --port 7870`，`GET /v1/models` 且中文 `zf_xiaobei` 合成成功（验证 `misaki[zh]`）；
5. `python launcher\mobius_launcher.py` 能出 GUI（验证 tkinter/tcl 随基底存在）。

---

## 5. 不确定项与风险

| # | 风险/不确定项 | 影响 | 建议 |
|---|---|---|---|
| 1 | **三套 venv 基底不可分发**：Gateway 指向 `C:\Users\lenovo\.dsh\...\python.exe`，Memory 指向 `D:\Anaconda3\envs\open-webui\python.exe`（即 Core 环境）。 | 换机/重建 Core 环境会连带破坏 Memory 环境；组件包拷到别的机器 Python 路径不存在。 | 必须 `uv venv --relocatable` 重建三套（§4.4），不要直接打包现有 venv 目录。 |
| 2 | **Kokoro 脚本在仓库外**（`D:\TTS_Workspace\kokoro_openai_api.py`），F5（`mobius_tts_api.py`）、GSV（`D:\AI\gsv_*.py`）同样不在仓库。 | 只扫仓库会漏掉这些依赖；L3 组件化时这些"代码资产"没有被版本管理。 | L3 P2 把 TTS 适配层收进仓库（`ops/tts/` 已有 GSV 适配器，但 Kokoro 没有），并纳入组件清单。 |
| 3 | **`misaki[zh]` 是代码推断，不是包元数据声明**：`kokoro` 只声明 `misaki[en]`；中文 G2P 由 `KPipeline(lang_code='z')` 在运行时按需 import。 | 只按 `pip install kokoro` 重建会缺 `pypinyin-dict`/`jieba` 等，中文语音首跑报错。 | requirements 显式写 `misaki[en,zh]`（§4.3-B），并加"中文合成"验收（§4.5-4）。 |
| 4 | **Python 版本不统一**：Core/Memory 是 3.11，Gateway 是 3.12。本次按各自 3.11/3.12 评估 marker；若统一到 uv 3.12 重建，部分版本（尤其 `torch 2.13/2.15`、`pywin32 312`）需确认 3.12 轮子可用。 | 统一 3.12 可能触发依赖重解，版本号与本次清单不一致。 | 二选一：`uv python install 3.11` 保持三套 3.11；或接受 3.12 并重新冻结 + 做 §4.5 验证。不要假设 3.11 的 wheel 能直接装 3.12。 |
| 5 | **`psycopg` 必须带 binary extra**：元数据里 `psycopg-binary` 只是 `psycopg[binary]` 的 extra，基础闭包不会自动带入；Windows 无系统 libpq。 | 只写 `psycopg==x` 会在 `connect()` 时报 `libpq` 找不到。 | 两个 requirements 都写 `psycopg[binary]==…`（已写）。 |
| 6 | **动态 import 未发现，但软 import 存在**：全仓仅 `ops/yae_pipeline_stepA.py`（一次性脚本，已排除）用 `importlib`；运行时文件里的 `psutil`、`import psycopg` 等都在 `try/except` 或函数内（如 `core/store.py:58`、`core/services.py:138`、`media_gateway/server.py` 的 psutil）。 | 若把 psutil 完全删掉：Core 回退 wmic（能用但慢）；Gateway 回退 `os.kill`（可用）。 | **Core 清单保留 psutil（0.8 MB）**；Gateway 清单未列（软依赖，不装也安全）。 |
| 7 | **mcp 2.3 的 Windows 依赖偏重且会变**：`pywin32`（19.4 MB）+ `PyJWT[crypto]→cryptography`（10.5 MB）；升级 mcp 版本时依赖面可能变化。 | Gateway 的"最小体积"由 mcp 版本决定。 | 锁死 `mcp==2.3.0`/`mcp-types==2.3.0`，升级前重跑本审计。 |
| 8 | **Memory 体积瓶颈是 torch 而非包管理**：`sentence-transformers→torch 523.8 MB + scipy 132.3 + transformers 108`，删包只能省 57 MB。 | 记忆组件仍占约 1.08 GB；与 L3 "组件按需下载"配合尚可，但"标准安装"会明显变重。 | 若要压到 300–400 MB，需改 `embed_server.py` 走 ONNX（`onnxruntime` + `optimum`/`fastembed`，bge-m3 需确认导出）；属额外开发，建议 L3 后置评估。 |
| 9 | **Kokoro 依赖 torch 是为推理而非训练**；`torch` 2.13/2.15 均为 CPU 轮子（`torch_cpu.dll` 291 MB，无 CUDA DLL），`.lib` 静态库文件约 40 MB 可再裁。 | 体积估算略保守（实际可再省 ~40 MB/环境）。 | 打包时删除 `torch/lib/*.lib`、`*.pdb` 等非运行文件。 |
| 10 | **未做安装实测**：按任务要求只读，本报告未执行 `uv pip install` 验证清单闭合；若某包在 3.12 下无轮子/版本冲突，需在真实重建时修正。 | 清单"理论闭合"而非"安装验证闭合"。 | L3 P2 首次重建时把 `uv pip freeze` 结果与本报告 diff，差异回写此文档。 |
| 11 | **Open WebUI Pipe 未计入**：`openwebui/mobius_pipe.py` 跑在 Open WebUI 进程内（当前未运行），其依赖属 Open WebUI 组件；若 L3 保留网页调试面，需单独评估（实测 `open-webui` 闭包 **262 包 / 2,492.5 MB**）。 | 本报告的精简结论不包含 Web UI 能力。 | L3 组件矩阵里明确"不含 Open WebUI"；如保留则按独立可选组件计 2.5 GB。 |
| 12 | **文件体积统计口径**：按 RECORD 累计（含 dist-info、Scripts），与 site-packages 实测有 1–2 MB 差异；压缩后体积为估算。 | 容量规划误差 ~±5%。 | 打包后以 7z 实测大小为准更新 manifest。 |

---

## 6. 附录：审计产物与复现

- 本报告：`ops/l3/dep-audit.md`
- 审计方法（可复现）：对 §1.2 文件集做 `ast` 扫描 → 顶层模块名 → 用**目标环境自身**的 `importlib.metadata` 做 distribution 映射 → `Requires-Dist` + marker/extras 闭包 → 按 `RECORD` 文件 `stat` 求和。
- 体积原始值（便于复核）：Core 3,521 MB / site-packages 3,368.2 MB / 350 dists；Gateway 87 MB / 83.4 MB / 37；Memory 1,097 MB / 1,093.9 MB / 72。三个环境各取一次全量递归 `Get-ChildItem` 校验。
- 临时采集脚本与 JSON 中间数据已从**工作区**删除；如需重新生成，按上述方法重建即可（或在 L3 P2 把采集脚本作为 `ops/l3/tools/` 固化）。
- ⚠️ 仓库卫生提示：审计期间（并发）提交 `0669b15` 误把本次临时产物（`ops/l3/_data/*`、`ops/l3/_dep_*.py`、`ops/l3/_scope_*.txt`，含 95 MB 的 `torchlib.zip`）带进了 git 历史。工作区删除已就绪，下次提交会记录删除；若要彻底从历史移除该 95 MB blob，需在收尾时重写该提交（`git rebase`/`filter-repo`），注意不要影响同分支其他改动。
