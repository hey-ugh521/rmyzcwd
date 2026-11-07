# Möbius L2 实施计划：脱离 Docker + Core 统管 + 单 exe 启动

> 状态：执行中（P0 完成）
> 日期：2026-11-07
> 决策记录：
> 1. 数据库：**便携 PostgreSQL**（保留 PG，不迁移 SQLite）
> 2. LangBot 数据：**全新配置**（不迁移历史；历史备份保留可查）
> 3. Docker：**保留为回退路径**（原生优先，Docker 可选）

---

## 1. 目标

1. 新机器无需 Docker Desktop 即可运行 Möbius
2. Core 作为唯一 Supervisor：启动/停止/健康检查/重启/日志，一处看全栈
3. 双击 `Mobius.exe` → Core 起来 → 机器人可用（文本/语音/生图/记忆）
4. Docker 模式保留为 fallback（配置开关，默认原生）

## 2. 目标架构

```
Mobius.exe（PyInstaller 启动器，只负责拉起 Core 与托盘/控制台）
└─ Core 8900（唯一 Supervisor，扩展 core/services.py）
   ├─ LangBot 原生（uv venv + Python 3.12，SQLite 或本地 PG）
   ├─ NapCat 原生（Windows 版，OneBot 3000/3001）
   ├─ PostgreSQL 便携版（Core 托管；pgvector 扩展）
   ├─ Memory MCP 8902 + Embed 8903（bge-m3 本地快照）
   ├─ Media Gateway 8899
   ├─ PowerContext 8000
   ├─ ComfyUI 8188（按需唤醒，已有逻辑）
   └─ GPT-SoVITS 7873/7871（可配常驻或按需）
```

## 3. 基线（P0 已完成）

### 3.1 版本

| 组件 | 版本/标识 |
|---|---|
| Möbius 仓库 | `7dbf085` |
| LangBot | 4.11.2（镜像 `sha256:69ad47a1…`） |
| NapCat | 镜像 `mlikiowa/napcat-docker:latest` `sha256:b101b896…`（NapCat.Shell） |
| ComfyUI | 0.43.1（portable） |
| GPT-SoVITS | v4 portable 20250529 |
| PostgreSQL | `pgvector/pgvector:pg18` |
| Python 环境 | open-webui(346) / qqbot-mcp(36) / mobius-memory(70) / comfyui(214) |

### 3.2 依赖清单

`ops/l2/requirements/{open-webui,qqbot-mcp,mobius-memory,comfyui}.txt`

### 3.3 硬编码路径盘点

`ops/l2/hardcoded-paths.csv`：**45 个文件、160 处**（watchdog 36 处最多）。
P1 需引入统一配置（`config.json` + 环境变量），逐项替换；ops/ 下的一次性脚本可延后。

### 3.4 数据备份

位置：`D:\AI\backups\l2-baseline-20261107\`

| 内容 | 大小 | 校验 |
|---|---|---|
| `pg/langbot.sql` | 55.8 MB | 52 张表 |
| `pg/mobius.sql` | 515 KB | 6 张表 |
| `pg/mobius_core.sql` | 81 KB | 9 张表 |
| `volumes/langbot-data/` | 377.7 MB | 流水线/插件/监控数据 |
| `volumes/napcat/` | 231.9 MB | QQ 登录态与配置 |

## 4. 阶段与验收

### P0 基线（完成）
- [x] 版本固定
- [x] 依赖清单
- [x] 三库 dump + 数据目录备份
- [x] 硬编码路径清单
- 交付：`docs/L2-PLAN.md`、`ops/l2/*`

### P1 Core 全栈 Supervisor
- 服务定义：id/cmd/cwd/env/health/端口/启动超时/重启退避
- API：`GET /api/services`、`POST /api/services/{id}/start|stop|restart`、`POST /api/services/start-all|stop-all`
- `/console` 增加服务面板（状态、操作、日志尾部）
- 失败可见：连续失败 N 次标记 `failed`，不无限重启
- **验收**：Core 单进程 start-all 起 8–10 个服务；stop-all 全部退出；任一服务挂掉可单独重启；页面上能看到状态与错误

### P2 LangBot 原生化（高风险）
- 固定 LangBot 4.11.2 源码 + `uv venv`（Windows）
- 数据库：优先 LangBot SQLite；若插件/监控需要 PG 则用便携 PG
- **全新配置**：重跑流水线（模型/提示词/插件/mcp_servers/事件绑定），可参照现有配置值（`langbot` 库备份可查）
- 插件运行时：先尝试内置运行；不行再决定保留容器
- **验收**：原生 LangBot 收发私聊/群聊；`memory`、`comfyui` MCP 加载；PowerContext 插件加载；语音/生图工具可用

### P3 NapCat 原生化
- NapCatQQ Windows 版，OneBot HTTP 3000 / WS 3001 + token
- 扫码登录，登录态落盘
- **验收**：私聊/群聊消息进入 LangBot；图片/语音发送成功；重启后免扫码

### P4 数据库去容器
- 便携 PostgreSQL（zenith/EDB zip）由 Core 托管，初始化 + pgvector
- 导入 `langbot`（可选，若用 SQLite 则跳过）、`mobius`、`mobius_core`
- 去掉 Redis（网关已有进程内回退）
- **验收**：记忆写入/召回、任务持久化、Core 审计正常；重启数据不丢

### P5 启动器 exe
- PyInstaller（onefile 或 onedir）启动器：托盘 + 控制台入口 + 日志
- 首次向导：数据目录、模型目录、QQ 登录、可选 Docker 模式
- 开机自启、退出时优雅停止全部子进程
- **验收**：双击 → 全链路可用；关闭 → 端口全部释放

### P6 安装包
- Inno Setup：安装目录、快捷方式、卸载
- 模型 50GB 不打包：首次启动检测 + 下载/迁移引导
- **验收**：干净机器安装后按向导完成配置，三条消息内可聊天

## 5. 风险与回退

| 风险 | 影响 | 回退 |
|---|---|---|
| LangBot 原生插件运行时不可用 | P2 卡住 | 保留 Docker LangBot（决策 3） |
| NapCat Windows 登录不稳 | P3 卡住 | 保留容器 NapCat |
| 便携 PG 初始化/pgvector 装不上 | P4 卡住 | 继续用容器 PG（混合模式） |
| 50GB 模型路径 | 安装体验 | 首次向导 + 目录检测 |
| 分发许可（AGPL/GPL/Civitai） | 只宜自用 | 不对外分发 |

## 6. 当前状态

- 所有项目进程/容器已停止（按用户要求释放内存）
- 备份期间临时启动过 `mobius-pg`，完成后已停止
- 下一步：P1（Core Supervisor 扩展）
