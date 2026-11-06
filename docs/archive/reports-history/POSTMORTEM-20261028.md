# 故障档案：2026-10-28 / 10-29 两轮 QQ 链路根因记录

> 从 `docs/STATUS.md` 旧版（2026-10-29 18:19）的第 8、10 节整体迁出，内容未改写。
> STATUS.md 只描述当前状态，历史根因留在这里。
> 迁出日期：2026-11-06。

---

## A. 「用 LoRA 生图报错」（2026-10-28 晚）

**现象**：私聊说"用 Lora 弄一张幽兰黛尔"，Agent 回错；LangBot 记录两次工具调用
`comfyui_list_loras` / `comfyui_list_characters` **各自卡满 300 秒超时**。

**取证**：网关日志在那段时间**一条请求都没有**；但用同一个失效 session id 从宿主机
直接打 `/mcp`，0.3 秒就返回 404 并留日志。

**根因**：网关在 22:47 被重启过，LangBot 手里 22:28 建的 MCP SSE 流变成死流，
**它不重连**，工具调用挂在死流上直到超时。不是 LoRA 的问题，不是 ComfyUI 的问题，
也不是权限问题——是**有状态 MCP + 客户端不重连**的组合缺陷。

**当时的修复**：
1. 现场：重启 LangBot 重建会话。
2. 根治（第一阶段）：`watchdog.py` 新增 `check_mcp_session()`——比较网关启动时间与
   LangBot 容器启动时间，网关晚于 LangBot 就定点重启 LangBot。
3. 实测：网关重启 → 56 秒内判定 → 重启 LangBot → 16 秒恢复 → 新会话建立。
4. 附带修掉一个坑：watchdog 原先把 LangBot 监控挂在 `napcat_ok` 后面，而 NapCat
   宿主机 API 不可达，导致整套 LangBot 监控被跳过；已解耦，并给"卡死判定"加了时间上界。

> **后续（2026-11-06）**：这条链路最终由 MCP 无状态化根治，网关重启不再需要重建会话。
> 见 `docs/adr/ADR-004-mcp-stateless.md`。

---

## B. 2026-10-28 深夜：QQ 生图"又挂了"的三个独立故障

都不是模型/LoRA 的问题。

### B.1 生图成功后立刻被休眠（真凶）

- **现象**：23:17:12 刚成功出图，23:17:49 Core 就把 ComfyUI 睡了（审计 `idle_sec=606`）。
  之后群友再要图就"连接失败"。
- **原因**：QQ 走 `LangBot → 网关`，**Core 完全看不到这次媒体活动**；
  且网关只在任务**提交**时记账，一次 90s 的生图会让"闲置时长"虚高 90s。
- **修复**：
  1. 网关在 `/api/status` 暴露 `activity{last_activity_at, seconds_since, active_tasks}`；
     提交**和任务结束**都刷新活动时间。
  2. Core 的休眠判定改成 `min(Core 自己的闲置, 网关闲置)`，并且网关有在跑的任务时绝不休眠。
- **验证**：正向（出图后 30s 内不睡）+ 反向（无活动 60s 后正常休眠）都要过。

### B.2 NapCat WebUI 进不去 / 端口消失

- **现象**：`docker inspect` 显示 `Ports: {"3000/tcp":[], "3001/tcp":[], "6099/tcp":[]}`，
  6099 打不开；watchdog 一直误判"未登录待扫码"，导致 LangBot 监控被整个跳过。
- **原因**：容器重建时没带端口发布；且 **3000 与 Open WebUI 默认端口冲突**。
- **修复**：compose 改为 `6099:6099`、`13000:3000`、`13001:3001`（宿主机侧避开 3000），
  重建容器后 `online: true`、6099 返回 200、快速登录成功。
- **现在是怎么走的**：LangBot 在 docker 网络内连 `ws://napcat:3001`，**不依赖宿主机映射**；
  宿主机端口只用于人访问 WebUI 和外部工具。

### B.3 Open WebUI 装上了但起不来

- **现象**：启动卡在从 HuggingFace 下载 `all-MiniLM-L6-v2`。
- **原因**：国内直连 HF 不稳。
- **修复**：用 `HF_ENDPOINT=https://hf-mirror.com` 启动，72s 就绪，版本 0.11.0。
- **交付**：`scripts/install-openwebui-pipe.py` 一键把 Möbius Pipe 装进 Open WebUI
  （自动登录 → 创建 → 启用 → 校验模型列表）。实测 `mobius.mobius` 对话 1.2s 返回。

### B.4 重启后「发消息没回复」（真凶：开机竞态）

- **现象**：机器重启后 Docker 里 napcat/langbot 都显示 Up，但 QQ 发消息没有回复。
- **证据链**：LangBot 容器日志 23:53:00 出现
  `Failed to start MCP server comfyui: Connection failed`；
  23:52 之前 gateway 还没起来（Core 23:53:02 才启动，网关更晚）。
- **根因**：开机时 Docker 容器与宿主机进程**并行启动**。LangBot 常常先于
  Media Gateway 就绪，它初始化 MCP 失败后**不会重试**——于是这一整轮的
  工具集是废的，对话跑不起来，表现就是「收不到回复」。
- **为什么 watchdog 没救**：`check_mcp_session` 用 `/api/status` 取网关启动时间，
  但该接口要探测 ComfyUI/Kokoro/F5/Core，最坏 9 秒；watchdog 超时 6 秒，
  于是**每一分钟都在超时报错**，自愈从未真正执行。且解析还沿用了旧字段
  （`gateway.uptime_sec`），换到 `/api/pulse` 后恒为 0 → 静默 return。
- **修复**：
  1. 网关新增 **`GET /api/pulse`**：纯内存，不探测后端（0.0x 秒返回，
     含 `uptime_sec / last_activity_at / active_tasks`）。
  2. Core 的闲置休眠、watchdog 的会话自愈都改用它。
  3. watchdog 的判定修好，并**新增直接证据**：读 LangBot 本次启动以来的容器日志，
     出现 `Failed to start MCP server` 就重启 LangBot（比时间先后更硬）。
  4. **boot-mobius.ps1 增加开机竞态处理 + 自检结论**：
     等网关就绪 → 比较启动时间，必要时重启 LangBot → 输出一行
     `开机自检：Core=OK Gateway=OK LangBot=OK NapCat=在线 MCP=OK → PASS`。
- **实测**：人工制造竞态（网关比 LangBot 晚 114s）→ watchdog 正确判定并重启
  LangBot → 网关日志出现新 MCP 会话；boot 自检输出 PASS。

### B.5 一键诊断

```powershell
D:\Anaconda3\envs\open-webui\python.exe tests\diagnose.py
```

沿链路逐段检查并指出断点：NapCat 在线 → LangBot 健康 → Core → 网关 →
**LangBot↔MCP 工具集** → 近期入站消息 → 近期运行结果。

### B.6 当时的群聊回复规则（已废止）

> 原文：「流水线的群回复规则是 `at: true`，**群里必须 @ 机器人才回**；
> 普通群消息会被正常接收（`monitoring_messages` 里 status=success）但按规则不回。」

**2026-11-06 现状**：@ 过滤已移除，改为模型自动判断（`<pass/>` 闸门）+ 群聊默认语音。
见 `docs/prompts/pipeline-prompt.txt` 与 `ops/patches/respback_group_autoreply_gate.py`。
