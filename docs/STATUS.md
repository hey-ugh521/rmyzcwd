# Möbius 现状全景（2026-10-28 23:10）

> 给产品经理做规划用的「一张图 + 能力账 + 局限账」。
> 事实来自运行中的系统与实测日志，不是设计意图。

---

## 1. 一句话

QQ 群友/私聊 → LangBot → **Media Gateway（MCP）** → ComfyUI / TTS 这条媒体链**已通且带 LoRA**；
**Möbius Core** 是另一条已经建成的统一大脑（记忆/任务/审计/媒体总开关），
目前接的是 **Console 网页**和 **Open WebUI**，**QQ 还没切过去**。

也就是说：现在有两条管线，媒体能力共享，大脑还没合并。

---

## 2. 技术路径（请求实际怎么走）

### 2.1 QQ 媒体链路（现在生产在用的）

```
QQ 用户
  → NapCat(容器)  OneBot v11
  → LangBot(容器, 5300)  LocalAgent 运行时
      ├─ 人格 system prompt（八重樱 / Möbius，写在流水线配置里）
      ├─ 模型 DeepSeek（deepseek-flash，走 LangBot 的 provider）
      └─ 工具：MCP over HTTP
  → Media Gateway (宿主机 8899)  /mcp
      ├─ comfyui_generate(character="布洛妮娅", prompt=…)
      │     → 解析角色档案 → 注入 LoraLoader 链 → ComfyUI 8188
      ├─ comfyui_list_loras / list_characters / task_* / media_*
      └─ tts_speak → Kokoro 7870
  → 网关把图片/音频挂在自己的 /img /audio 上（宿主机可访问）
  → LangBot 回复里带 URL → respback 补丁识别 image/*|audio/*
  → 转成 QQ 图片消息 / 语音消息
```

### 2.2 Core 链路（已建成，接的是 Web/Console）

```
Console 网页 / Open WebUI Pipe
  → Möbius Core (8900)
      ├─ Agent 循环（DeepSeek + 4 个工具：recall / remember / generate_image / speak）
      ├─ 记忆四层（SQLite）
      ├─ 任务中心 + 审计
      ├─ 媒体总开关（off / on_demand / prewarm）+ 服务监管
      └─ REST /api/* → Media Gateway（不是 MCP，是普通 HTTP）
  → Core 把图片 markdown 和 audio_url 直接拼进回复文本
```

### 2.3 两条链的关系

**共享**：ComfyUI、Kokoro、Media Gateway、角色档案、LoRA 库。
**不共享**：对话上下文、记忆、任务记录、人格 prompt。

合并开关在 `docs/LANGBOT-CUTOVER.md`：把 LangBot 的模型指向 Core 的 `/v1/chat/completions`，
QQ 与 Web 就变成同一套大脑。**没执行**，是可逆的一步。

---

## 3. 架构（组件与职责）

| 层 | 组件 | 端口 | 职责 | 事实来源？ |
|---|---|---|---|---|
| 入口 | QQ / NapCat | — | 收发消息 | — |
| 入口 | Console 网页 | 8900/console | 运维控制面 | — |
| 入口 | Open WebUI Pipe | — | 调试对话面 | — |
| 平台适配 | LangBot | 5300 | QQ 消息、人格 prompt、模型调用、工具分发 | 否（可被替代） |
| **核心** | **Möbius Core** | 8900 | 记忆、任务、审计、媒体模式、Agent 循环 | **是（Web 侧）** |
| 媒体网关 | Media Gateway | 8899 | MCP 面 + REST 面、LoRA 注入、任务队列、媒体代理 | 否（执行器） |
| 后端 | ComfyUI | 8188 | 生图 | 否 |
| 后端 | Kokoro | 7870 | 多音色 TTS | 否 |
| 后端 | F5/Möbius 克隆 | 7871 | 克隆音色 | **未启用** |
| 运维 | watchdog.py | — | 容器 + 会话 + 卡死自愈 | 否 |
| 运维 | 计划任务 Mobius Boot | — | 开机自启 | 否 |

**设计铁律**（已写进 `docs/ARCHITECTURE.md`）：
1. 记忆/任务/权限的事实来源只有 Core；入口不自存。
2. ComfyUI 不常驻，默认按需唤醒，闲置休眠。
3. 模型不能自行反复启停大服务（模式 + 冷却 + 退避三重约束）。
4. 角色核心设定只有人能改；模型只能写「候选记忆」。
5. 不替换旧 MCP，而是把它升级成 QQ 专用媒体网关。

---

## 4. 数据模型（Core 侧，`data/mobius.db`）

| 表 | 内容 | 写入者 |
|---|---|---|
| `constitution` | 角色宪法，版本化 | 仅 Console |
| `user_memory` | 已确认的用户偏好/关系 | 审批后落库 |
| `memory_candidates` | 候选记忆（模型只能写这里） | 模型 → 人工审批 |
| `timeline` | 事件/剧情时间线 | Core 自动 |
| `turns` | 临时工作记忆（按会话） | Core 自动，可过期 |
| `tasks` | 统一任务（五态 + 远程 id + 结果） | Core |
| `audit` | 谁在哪个入口、用了什么、耗时、成败 | Core |
| `kv` | 媒体模式等全局状态 | Core/Console |
| `identities` | Web 身份 ↔ QQ 身份绑定（人工） | Console |

角色档案是**文件**：`data/characters/*.json`（热生效，Core 与网关共用同一份）。
当前 3 个：bronya / elysia / kiana。机器里有 31 个 LoRA，够补更多角色。

---

## 5. 能力账（逐项，按证据）

| 能力 | 状态 | 证据 |
|---|---|---|
| MCP over HTTP 接入 | ✅ | 15 个工具，官方 SDK 探针真实握手 |
| 列 checkpoint | ✅ | 6 个 |
| 列 LoRA（含搜索） | ✅ | 31 个，`q=bronya` 命中 |
| 角色档案驱动（自动补 ckpt/LoRA/尺寸/画风） | ✅ | 显式参数 > 档案 > 工作流默认 |
| LoRA 链式注入（多 LoRA、去重、权重截断） | ✅ | ComfyUI payload 里 `lora_1..N` 节点可查 |
| 文生图（竖/横版） | ✅ | 多次真实出图，单张 47–95s |
| 异步任务 + 取消 | ✅ | 队列删除 / ComfyUI interrupt 都验过 |
| 音色列举 + TTS | ✅ | Kokoro 28 音色，wav 真实产出 |
| 图片/音频回传 QQ | ✅（补丁在位） | respback 补丁 grep 命中；**缺一次手机端人工确认** |
| Core 统一对话（SSE） | ✅ | 事件 meta/text/task/done |
| Open WebUI 接同一核心 | ✅ | Pipe 已装（`mobius.mobius`），实测 1.2s 回复 |
| OpenAI 兼容端点（给 LangBot/WebUI） | ✅ | 标准响应 + 流式分块 |
| 记忆四层 + 候选审批 + 遗忘 | ✅ | 候选可见→审批生效→遗忘 全链路 |
| 任务中心 + 审计 | ✅ | 同一次生图在 QQ/Console 用同一个 task id |
| Console 六页 | ✅ | 真实 Core 集成 19/19，mock 42/42 |
| 媒体总开关（off/on_demand/prewarm） | ✅ | 三档切换、唤醒、休眠 |
| 按需唤醒 + 闲置休眠 | ✅ | 冷启动 32.6s；闲置 10 分钟休眠 |
| 开机自启 | ✅ | 计划任务；重启后 19s Core 就绪 |
| MCP 会话自愈 | ✅（本轮新增） | 网关重启 → watchdog 56s 内重建会话 |
| 图生图 / 上传参考图 | ⚠️ 部分 | 上传与注入已实现，`upscale/removebg` 端到端**未验** |
| ControlNet / IP-Adapter / 人脸保持 | ❌ | 工作流文件在，网关明确拒绝 |
| 克隆音色（F5/Möbius） | ❌ | 7871 未启用，仅有状态位 |
| 身份自动合并 | ❌ | 设计上就不做，需人工绑定 |
| 向量检索记忆 | ❌ | MVP 用 SQL + 关键词 |

---

## 6. 局限（要规划，先看这些）

### 6.1 架构性（不解决会长歪）

1. **双大脑未合并**：QQ 走 LangBot 的 LocalAgent，Web 走 Core。人格/记忆/任务各一份。
   这是当前最大的结构性问题，也是 `LANGBOT-CUTOVER.md` 要解决的。
2. **LangBot 是被我们"绕过"的**：人格 prompt、记忆插件、AIImagePlugin 都还在它里面。
   一旦切 Core，这些要么关掉、要么迁移，否则双写。
3. **媒体网关是有状态的 MCP 服务**：进程一重启，所有客户端会话失效。
   LangBot 不会重连（本轮 bug 的根因），目前靠 watchdog 定点自愈兜底。
4. **任务在两个地方**：网关有内存任务队列，Core 有持久任务表。网关重启丢队列，
   Core 靠 `remote_id` 回收，但"正在跑的那一张"会变成孤儿。

### 6.2 工程性

5. **解释器分裂**：网关必须用 `D:\AI\qqbot-mcp\Scripts\python.exe`（mcp 2.3，无 fastapi），
   Core 必须用 `D:\Anaconda3\envs\open-webui\python.exe`（有 fastapi，mcp 1.27）。不能混。
6. **冷启动慢**：ComfyUI 首次 4–6 分钟（Defender 扫 custom nodes），缓存热后 30s。
   超时配置 420s，用户首次请求可能超时一次。
7. **语音回传是容器内热补丁**：`docker compose up --force-recreate` 会静默丢，
   要跑 `scripts\replay-langbot-voice-patch.ps1`。
8. **NapCat 的 3000/3001 宿主机端口没发布**（`docker inspect` 显示空映射）：
   消息链走容器内网没问题，但 watchdog 看不到它的 API，健康判定降级。
9. **单机单用户假设**：没有鉴权分级，Console 靠"只监听本机/回环免鉴权"。
10. **GPU 只有 8GB**：SDXL + LoRA 已占 6.4GB，多任务并发必然 OOM，所以队列并发锁死为 1。

### 6.3 产品性

11. **角色一致性只到"记忆 + 宪法"层**：没有向量检索、没有长期剧情压缩，
    聊多了会漂。LLM 本身也会漂。
12. **"可塑性"是半自动的**：模型写候选 → 人审批。全自动写入没做，也建议先别做。
13. **没有失败重试的产品化**：失败只留审计，用户要自己再要一次。
14. **Console 是运维台，不是聊天台**：群友永远不该进后台，这点是对的。

---

## 7. 形式（用户看到什么）

| 角色 | 入口 | 现在是什么样 |
|---|---|---|
| QQ 群友 | QQ 群/私聊 | 和角色聊天；要图 → 收到图；要语音 → 收到语音条 |
| 你（运营） | `http://127.0.0.1:8900/console` | 深色运营台：概览/任务/角色/记忆/审计/设置 |
| 你（调试） | Open WebUI + `Mobius` Pipe | 选 `Möbius` 模型，即接 Core（需先安装 Pipe 文件） |

Console 的五层信息结构：能不能用（概览）→ 它应该像谁（角色）→
它记住了什么（记忆）→ 它正在做什么（任务）→ 为什么会这样（审计）。

---

## 8. 本轮修的 bug（QQ 私聊「用 LoRA 生图报错」）

**现象**：私聊说"用 Lora 弄一张幽兰黛尔"，Agent 回错；LangBot 记录两次工具调用
`comfyui_list_loras` / `comfyui_list_characters` **各自卡满 300 秒超时**。

**取证**：网关日志在那段时间**一条请求都没有**；但用同一个失效 session id 从宿主机
直接打 `/mcp`，0.3 秒就返回 404 并留日志。

**根因**：网关在 22:47 被重启过，LangBot 手里 22:28 建的 MCP SSE 流变成死流，
**它不重连**，工具调用挂在死流上直到超时。不是 LoRA 的问题，不是 ComfyUI 的问题，
也不是权限问题——是**有状态 MCP + 客户端不重连**的组合缺陷。

**修复**：
1. 现场：重启 LangBot 重建会话（已恢复）。
2. 根治：`watchdog.py` 新增 `check_mcp_session()`——比较网关启动时间与 LangBot 容器启动时间，
   网关晚于 LangBot 就定点重启 LangBot。
3. 实测：网关重启 → 56 秒内判定 → 重启 LangBot → 16 秒恢复 → 新会话建立。
4. 附带修掉一个会让上述检查永远跑不到的坑：watchdog 原先把 LangBot 监控挂在
   `napcat_ok` 后面，而 NapCat 宿主机 API 不可达，导致整套 LangBot 监控被跳过；
   已解耦，并给"卡死判定"加了时间上界防止误重启。

---

## 9. 现在可以让你规划的选项（按代价排序）

| 选项 | 代价 | 收益 |
|---|---|---|
| A. 装 Open WebUI Pipe，接同一个 Core | 低 | 立刻有一个"同人格"的网页调试口 |
| B. QQ 切到 Core（双大脑合并） | 中，可逆 | 记忆/任务/人格统一，这是架构目标 |
| C. 补角色档案（31 个 LoRA → 更多角色） | 低 | 群友点谁就能画谁 |
| D. 图生图 / 参考图端到端 | 中 | "照这张图画" |
| E. 修复 F5 克隆音色 | 中高 | 真·角色声线 |
| F. 记忆向量检索 + 剧情压缩 | 中高 | 长程一致性 |
| G. 修 NapCat 端口发布 | ✅ 已修（本轮） | watchdog 恢复完整监督 + WebUI 可进 |

---

## 10. 2026-10-28 深夜修复记录（QQ 生图"又挂了"这一轮）

三个独立故障，都不是模型/LoRA 的问题。

### 10.1 生图成功后立刻被休眠（真凶）

- **现象**：23:17:12 刚成功出图，23:17:49 Core 就把 ComfyUI 睡了（审计 `idle_sec=606`）。
  之后群友再要图就"连接失败"。
- **原因**：QQ 走 `LangBot → 网关`，**Core 完全看不到这次媒体活动**；
  且网关只在任务**提交**时记账，一次 90s 的生图会让"闲置时长"虚高 90s。
- **修复**：
  1. 网关在 `/api/status` 暴露 `activity{last_activity_at, seconds_since, active_tasks}`；
     提交**和任务结束**都刷新活动时间。
  2. Core 的休眠判定改成 `min(Core 自己的闲置, 网关闲置)`，并且网关有在跑的任务时绝不休眠。
- **验证**：正向（出图后 30s 内不睡）+ 反向（无活动 60s 后正常休眠）都要过。

### 10.2 NapCat WebUI 进不去 / 端口消失

- **现象**：`docker inspect` 显示 `Ports: {"3000/tcp":[], "3001/tcp":[], "6099/tcp":[]}`，
  6099 打不开；watchdog 一直误判"未登录待扫码"，导致 LangBot 监控被整个跳过。
- **原因**：容器重建时没带端口发布；且 **3000 与 Open WebUI 默认端口冲突**。
- **修复**：compose 改为 `6099:6099`、`13000:3000`、`13001:3001`（宿主机侧避开 3000），
  重建容器后 `online: true`、6099 返回 200、快速登录成功。
- **现在是怎么走的**：LangBot 在 docker 网络内连 `ws://napcat:3001`，**不依赖宿主机映射**；
  宿主机端口只用于人访问 WebUI 和外部工具。

### 10.3 Open WebUI 装上了但起不来

- **现象**：启动卡在从 HuggingFace 下载 `all-MiniLM-L6-v2`。
- **原因**：国内直连 HF 不稳。
- **修复**：用 `HF_ENDPOINT=https://hf-mirror.com` 启动，72s 就绪，版本 0.11.0。
- **交付**：`scripts/install-openwebui-pipe.py` 一键把 Möbius Pipe 装进 Open WebUI
  （自动登录 → 创建 → 启用 → 校验模型列表）。实测 `mobius.mobius` 对话 1.2s 返回。

### 10.4 重启后「发消息没回复」（本轮真凶：开机竞态）

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

### 10.5 新增：一键诊断

```powershell
D:\Anaconda3\envs\open-webui\python.exe tests\diagnose.py
```

沿链路逐段检查并指出断点：NapCat 在线 → LangBot 健康 → Core → 网关 →
**LangBot↔MCP 工具集** → 近期入站消息 → 近期运行结果。

**群聊不回是另一回事**：流水线的群回复规则是 `at: true`，**群里必须 @ 机器人才回**；
普通群消息会被正常接收（`monitoring_messages` 里 status=success）但按规则不回。
