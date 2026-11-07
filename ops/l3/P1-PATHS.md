# P1 路径抽象（L3）

> 状态：2026-11-08 完成核心验收。
> 目标：应用目录可复制/改名/换盘启动，不再依赖硬编码应用路径；外部运行时根目录全部走配置变量。

## 1. 路径契约

配置中的字符串支持 `${NAME}` 占位符：

| 变量 | 来源 | 默认 |
|---|---|---|
| `${ROOT}` | `MOBIUS_ROOT` 环境变量，或应用根（含 config.json 的目录） | — |
| `${RUNTIME}` | `MOBIUS_RUNTIME` 或 `${ROOT}/runtime` | 运行时根 |
| `${MODELS}` | `MOBIUS_MODELS` 或 `${ROOT}/models` | 模型根 |
| `${DATA}` | `MOBIUS_DATA` 或 `${ROOT}/data` | 数据根 |
| `${LOGS}` | `${ROOT}/logs` | 日志 |
| 自定义 | `config.json → paths.{NAME}` | 如 `${AI}`、`${CONDA}`、`${HOME}` |
| 环境变量兜底 | 以上都没有时读同名环境变量 | `${USERPROFILE}` 等 |

实现：`core/paths.py`（Core 启动时展开 `CFG`）；`launcher/mobius_launcher.py` 内置同规则展开；
`config.json` 只写变量，不写死盘符。

## 2. 改造内容

| 项 | 变化 |
|---|---|
| `core/paths.py` | 新增：根目录解析、变量表、递归展开配置 |
| `core/store.py` | 加载配置后 `expand_config(CFG)` |
| `launcher` | 读取配置时展开；启动 Core 时注入 `MOBIUS_ROOT` |
| `config.json` | 所有服务/启动器的绝对路径改为 `${AI}` / `${CONDA}` / `${HOME}` / `${TTS_WS}` |
| `gateway` env | 新增 `MOBIUS_WORKFLOWS_DIR`、`MOBIUS_COMFY_OUTPUT_DIR`（变量化） |
| `embed` env | `MOBIUS_EMBED_PATH=${AI}\models\bge-m3-st` |
| `memory` env | `MOBIUS_ENV_FILE=${AI}\mobius\.env` |
| `gsv` env | `MOBIUS_GSV_ROOT`、`MOBIUS_GSV_ADAPTER`（供 supervisor 使用） |
| 示例配置 | `config.example.json`（已脱敏，通用布局） |

## 3. 验收证据

| 测试 | 结果 |
|---|---|
| 复制应用到 `D:\AI\MobiusP1` 启动 | ✅ 9 个服务 ready，PG/Core/LangBot/网关正常 |
| 目录改名 `D:\AI\MobiusP1-moved` 再启动 | ✅ 全服务 ready（未改任何文件） |
| 启动器停止（含 PostgreSQL 优雅停止） | ✅ 端口全部释放 |
| 配置展开抽查 | ✅ `${AI}\qqbot-mcp...` → `D:\AI\qqbot-mcp...` |

## 4. 未纳入本次的硬编码（P2 处理）

| 位置 | 说明 |
|---|---|
| `ops/tts/start-gsv.ps1` | GSV 启动脚本仍写死 D:\AI（Core 现在直接用 `services.gsv` 配置启动，脚本仅 watchdog 用） |
| `D:\AI\gsv_supervisor.py`（仓库外） | ROOT/ADAPTER/权重路径硬编码，P2 打包时改成读环境变量 |
| `media_gateway/server.py` 默认常量 | `DEFAULT_WORKFLOWS_DIR` 等；配置已注入 env，缺省值仅兜底 |
| `ops/watchdog/watchdog.py` | 旧看护器，L3 由启动器替代 |
| `ops/credentials.py` 默认 ENV_FILE | 默认 `D:/AI/mobius/.env`；Core 已通过 `MOBIUS_ENV_FILE` 注入 |
