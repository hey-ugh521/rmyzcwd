# Möbius 架构现状（AS-IS）

> 采集时间：2026-11-10 17:00–17:10（Asia/Shanghai）
> 采集对象：开发工作副本 `C:\Users\lenovo\Desktop\_说明文档\mobius` @ `9547fce`
> 方法：读取 `config.json`、Core `/api/services` 与 `/api/status`、进程/端口、仓库文档；未做压力/故障注入。
> 说明：本文记录**实际运行架构**，不是目标架构；目标见 `ARCHITECTURE-v1.1.md`，差异见 `ARCHITECTURE-GAP.md`。

## 1. 部署形态（当前）

存在两个不同的"Möbius":

| 位置 | 内容 | 状态 |
|---|---|---|
| `C:\Users\lenovo\Desktop\_说明文档\mobius` | 开发工作副本（含最新代码 `9547fce`） | 当前实际在运行的服务来自这里 |
| `D:\AI\Mobius` | 安装器安装副本（Inno Setup，0.2.0 基线） | 代码落后于 `9547fce`；本次未升级 |

> G-01：开发副本与安装副本不是同一份代码，"安装后用户实际跑什么"目前不可保证。产品化必须先消除这个分叉。

## 2. 进程与端口

### 2.1 运行中的服务（Core Supervisor 视角）

| 服务 | 状态 | 端口 | ready_when | 备注 |
|---|---|---|---|---|
| Core | 运行中 | 127.0.0.1:8900 | — | FastAPI，唯一控制面 |
| gateway | ready | 8899 | port | MCP `/mcp` + REST `/api/*` |
| langbot | ready | 5300 | health | 原生 Python 运行，非 Docker |
| napcat | **down / awaiting_login** | 3000, 3001 | login_probe | OneBot HTTP 502；QQ 进程存在但未注入 |
| comfyui | **down** | 8188 | health | on-demand，当前未启动 |
| kokoro | ready | 7870 | port | TTS |
| gsv | ready | 7871 | ports_any | TTS |
| f5 | disabled | 7871 | port | 与 gsv 端口冲突，已禁用 |
| embed | ready | 8903 | port | 向量/嵌入 |
| memory | ready | 8902 | port | 长期记忆 MCP |
| powercontext | ready | 8000 | health | 会话/上下文扩展 |
| postgres | ready | 55432 | ports_any | 便携 PostgreSQL 18.6 |

观测汇总：**8 个服务 ready，2 个 down（comfyui、napcat），1 个 disabled（f5）**。
QQ 侧当前不可用：NapCat 未登录，OneBot 无连接。

### 2.2 启动链

```
Mobius.exe / MobiusCLI.exe（launcher）
  → 读取 config.json + secrets.json
  → 启动 PostgreSQL（pg_ctl）
  → 启动 Core（python -m core.server）
  → Core Supervisor 按 autostart 拉起：
       gateway / langbot / kokoro / gsv / embed / memory / powercontext
       （napcat autostart=false，由 launcher 显式启动，需要管理员权限）
       （comfyui 按需启动）
  → Core 在 8900 提供 API + /console 静态控制台
```

## 3. 入口与调用链（实际）

### 3.1 桌面/控制台

```
Desktop launcher GUI / 浏览器 Console
  → Core :8900 /console + /api/*
  → Core Agent / Supervisor
  → Gateway :8899（REST/MCP）→ ComfyUI / TTS
```

### 3.2 QQ（当前断开）

```
QQ 客户端（QQ.exe，当前普通启动，未注入 NapCat）
  → NapCat（:3000 HTTP / :3001 WS，当前未监听）
  → LangBot :5300（LocalAgent + pipeline + memory MCP :8902）
  → Gateway :8899（MCP 工具）→ ComfyUI / TTS
  → LangBot 回传 QQ
```

关键事实：QQ 对话**不经过 Core**；人格/记忆/任务在 LangBot 侧自成一套，Core 只看到媒体任务镜像（见 `STATUS.md`）。

### 3.3 Web / Open WebUI

```
Open WebUI Pipe → Core /v1/chat（Core 人格/记忆/工具）
```

当前 Open WebUI 进程未运行，:3000 无监听；Pipe 脚本在仓库中但没有人负责拉起。

## 4. 数据所有权（实际）

| 数据 | 存储 | 实际写入者 | 备注 |
|---|---|---|---|
| Core 任务镜像 | PG `mobius_core.tasks` | Core（由 Gateway outbox 投影） | 不是唯一任务事实源 |
| 任务快照/outbox | PG `mobius.task_snapshot` | Gateway | Gateway 才是当前任务执行状态权威 |
| Core 审计/轮次/记忆 | PG `mobius_core`（audit/turns/user_memory/…） | Core | Web/Console 链路 |
| LangBot 会话/模型/bots | PG `langbot` | LangBot | QQ 链路 |
| 长期记忆（QQ） | PG `mobius`（memory MCP） | memory MCP | 与 Core 记忆不是同一套 |
| 媒体产物/上传 | 文件系统（media_gateway outputs/uploads） | Gateway | 路径由配置决定 |
| 配置 | `config.json`（本地） | 人/launcher/rotate | gitignored |
| 密钥 | `secrets.json`（SSOT）+ 各消费方视图 | rotate.py | LangBot/NapCat 仍有明文副本 |

**观察到的双权威**：

1. 任务状态：Gateway `task_snapshot` 与 Core `tasks` 双写（DOC/ADR 正在收敛，D1 已定 Core 唯一 owner，代码尚未完成）。
2. 记忆：LangBot memory MCP（QQ）与 Core memory（Web/Console）两套。
3. 会话/身份：LangBot 会话与 Core session 没有统一身份映射。

## 5. 关键外部依赖

| 依赖 | 当前形态 | 分发风险 |
|---|---|---|
| Python 运行时 | 多个便携 runtime（core/gateway/langbot/kokoro/memory 等） | 版本/体积/来源需锁定 |
| PostgreSQL | `D:\AI\pgsql-portable` 便携版 | 数据目录、口令、备份/迁移 |
| ComfyUI | 外部目录 `D:\AI\comic\ComfyUI_windows_portable` + 自备模型 | 模型/工作流/路径不可假设 |
| LangBot | `D:\AI\langbot-native` 原生源码 + 便携 venv | 升级/补丁/配置漂移 |
| NapCat / QQ | `D:\AI\napcat-native` + QQNT | 注入需管理员、登录态、版本绑定 |
| Kokoro/GSV | 本地 Python 服务 | 模型/端口/显存 |
| Docker | 不在 Möbius 主链，但 legacy boot 脚本仍等待 Docker | 与 README/Docker-free 口径冲突 |
| legacy watchdog | `ops/watchdog/watchdog.py` | 与 Core Supervisor 双保活 |
| 容器/源码热补丁 | `ops/patches/` + LangBot 内存补丁 | 升级/重建即丢失，缺自动重放 |

## 6. legacy 运行面（仍在仓库/安装器中）

- `scripts/boot-mobius.ps1`：等待 Docker → start-mobius → 拉起仓外 watchdog；`Write-BootVerdict` 写法不是合法函数定义，自检不会执行。
- `ops/watchdog/watchdog.py`：独立巡检/保活，与 Core Supervisor 职责重叠。
- 安装器 `installer/mobius.iss`：仍打包 `scripts\*`；卸载只删 `logs`（配置/密钥/数据保留策略未定义）。
- 这些属于 T8 退役范围，M0 只记录，不动。

## 7. 安全边界（现状）

- Core 仅监听 `127.0.0.1:8900`；回环请求免鉴权，非回环需 Bearer。
- Gateway 默认回环；`/api/*` 配置 token 后鉴权，非回环无 token fail-fast；`/mcp` 按 ADR-006 保持开放（依赖回环 + DNS-rebinding + fail-fast 补偿）。
- 密钥 SSOT 已存在，但 LangBot `data/config.yaml`、NapCat 配置仍存明文副本；历史泄露凭证已轮换（见事故文档）。
- 管理接口与任务接口未分级；本地进程可冒充用户（回环免鉴权 + body 自带 user_id）。

## 8. 已知运行时缺陷（AS-IS 记录）

1. NapCat 当前 `awaiting_login`（HTTP 502），QQ 入口不可用；QQ 进程是普通启动，未注入。
2. ComfyUI 当前 down（on-demand，未唤醒）。
3. Open WebUI 未运行，Web 入口当前断。
4. `f5` 与 `gsv` 端口相同（7871），f5 已 disabled——配置层面的遗留。
5. 开发副本与安装副本分叉（G-01）。
6. README/STATUS/ACCEPTANCE 与实际默认值/入口不一致（见 GAP）。

## 9. 复现采集的命令

```powershell
# 服务状态
Invoke-RestMethod http://127.0.0.1:8900/api/services
# 端口
netstat -ano | findstr "LISTENING"
# 进程
Get-CimInstance Win32_Process
# 配置
python - <<'PY'
import json; print(json.load(open('config.json')))
PY
```
