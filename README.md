# Möbius —— 统一 Agent 核心（MVP）

> **文档状态（2026-11-10）**：本 README 部分内容仍描述历史运行方式（已退役的 Docker/boot/watchdog、旧默认值、旧安装路径），
> 与当前代码/运行值不完全一致。产品边界、实际架构与差距以以下三份为准：
> - `docs/PRODUCT-BOUNDARY.md`（v1.0 交付边界）
> - `docs/ARCHITECTURE-AS-IS.md`（实际运行架构）
> - `docs/ARCHITECTURE-GAP.md`（目标 vs 实际 / 收敛计划）
>
> 当前事实速记：数据库 PostgreSQL（`db_backend=pg`）；媒体运行值 `prewarm`；启动以 launcher + Core Supervisor 为准；
> Docker/boot/watchdog 已退役（T8）；启动统一走 launcher + Core Supervisor；QQ 为可选接入。


把「QQ 群友 ↔ 角色 ↔ 生图/语音」从一堆拼装服务收成**一个原生系统**：

```
NapCat ─► LangBot ─┐
                   ├─► Möbius Core (8900) ──► Media Gateway (8899) ──► ComfyUI / TTS
Open WebUI ─Pipe───┤        ├─ 记忆 / 角色宪法
Console ───────────┘        ├─ 任务中心 / 审计
                            └─ 媒体总开关（off / on_demand / prewarm）
```

- **事实来源只有一个**：Core。QQ、Web、控制台都是入口，不各存一份记忆。
- **ComfyUI 不常驻**：默认按需唤醒，闲置自动休眠，释放显存。
- **角色档案驱动**：模型说「布洛妮娅」，系统解析成 checkpoint + LoRA + 权重 + 声线。

## 目录

| 路径 | 说明 |
|---|---|
| `docs/ARCHITECTURE-v1.1.md` | 统一接口规格（唯一契约，改它要同步所有实现；v1.0 已归档到 `docs/archive/reports-history/`） |
| `docs/ACCEPTANCE.md` | 验收标准与端到端流程（产品侧看这个） |
| `core/` | Core 本体：`store.py`（库/档案）`services.py`（监管+网关客户端）`agent.py`（LLM+工具）`server.py`（HTTP） |
| `core/console/` | 内嵌单页控制台（无构建） |
| `media_gateway/` | Media Gateway：MCP 面 + REST 面 + LoRA 注入 + 异步任务 |
| `openwebui/mobius_pipe.py` | Open WebUI Pipe（把 Open WebUI 变成同一核心的对话入口） |
| `data/characters/` | 角色档案 JSON（热生效） |
| `scripts/` | 启停脚本 |
| `tests/verify.py` | 自动化验收 |

## 快速开始

```powershell
# 1. 启动（launcher 拉起 PG/Core/Supervisor）
#    安装版：.\Mobius.exe；开发版：.\launcher\dist\Mobius.exe
.\launcher\dist\Mobius.exe

# 或 CLI（无 GUI）
.\launcher\dist\MobiusCLI.exe --headless start

# 2. 打开控制台
start http://127.0.0.1:8900/console

# 3. 停止
.\launcher\dist\MobiusCLI.exe --headless stop
```

## 开机自启（推荐）

```powershell
# 安装：登录后自动拉起 Möbius（launcher + Core Supervisor）
.\scripts\install-autostart.ps1

# 查看 / 卸载
.\scripts\install-autostart.ps1 -Status
.\scripts\install-autostart.ps1 -Uninstall
```

实现方式是当前用户的计划任务（不需要管理员权限，可在任务计划程序里停用）。
legacy 的 `scripts\boot-mobius.ps1` 与 watchdog 已退役（T8）；启动/保活统一由 launcher + Core Supervisor 负责。

## 媒体模式

| 模式 | 含义 |
|---|---|
| `off` | 所有入口不可生图/语音，角色会礼貌说明 |
| `on_demand` | 按需（非默认）。收到明确请求才启动 ComfyUI，闲置 10 分钟休眠 |
| `prewarm` | 开机即拉起，适合需要秒回图的时候 |

模式是 **Core 的全局状态**，在控制台「概览」切换，QQ 与 Web 同时生效。

## 验证

```powershell
python tests\verify.py           # 全量
python tests\verify.py --quick   # 只测连通性与机制，不真生图
```

## 关键约束

- 角色宪法只能由控制台改；模型只能写「候选记忆」，审批后生效。
- 模型的启停权限被模式 + 冷却 + 退避三重约束，不会自行反复唤醒 GPU 服务。
- LangBot 的 QQ 媒体回传依赖 `respback` 补丁，容器重建后需重放（见 `docs/ACCEPTANCE.md`）。

## 回滚

旧版单文件 MCP 仍保留在 `D:\AI\qqbot-mcp\comfyui_mcp.py`（未修改）。
把 8899 上的进程换回旧文件、并让 LangBot 恢复原模型配置即可完全回退。
