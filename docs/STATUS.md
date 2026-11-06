# Möbius 现状全景（2026-11-06 21:45）

> 给产品经理做规划用的「一张图 + 能力账 + 局限账」。
> 事实来自运行中的系统与实测日志，不是设计意图。
> 上一版是 2026-10-29；本版按 11-01 ~ 11-06 的实际改动全面重校。
> 旧版第 8/10 节的历史故障记录整体迁到 `docs/archive/reports-history/POSTMORTEM-20261028.md`。

---

## 1. 一句话

QQ 群友/私聊 → LangBot → **Media Gateway（MCP，已无状态化）** → ComfyUI / Kokoro / GPT-SoVITS
这条媒体链**已通、带角色卡、带多底模**；群聊已改成**自动判断是否回应 + 默认语音**。
**Möbius Core**（记忆/任务/审计/媒体总开关）仍是 Web/Console 侧的大脑，**QQ 还没切过去**，
而且 QQ 侧现在还有**第二套记忆库**（memory MCP）。

也就是说：现在不是一条管线，而是**两条并行管线共享同一个媒体执行器**——
大脑没合并，记忆也没合并。

---

## 2. 技术路径（请求实际怎么走）

### 2.1 QQ 链路（生产在用）

```
QQ 用户
  → NapCat(容器)  OneBot v11（容器间 ws://napcat:3001；宿主机 13000/13001/6099 只给人访问）
  → LangBot(容器, 5300)  LocalAgent 运行时
      ├─ 人格 system prompt：写在流水线里，基准文件 docs/prompts/pipeline-prompt.txt
      │   （当前人设 = 迷迭香；ops/pipeline_guard.py 每 30 分钟校验漂移并自动恢复）
      ├─ 模型 DeepSeek（LangBot 自己的 provider，不经 Core）
      └─ 工具：MCP over HTTP ×2
          ├─ Media Gateway  :8899/mcp   14 个工具（媒体）
          └─ memory MCP     :8902/mcp    6 个工具（长期记忆，独立库）
  → 网关出图/出声 → 挂在网关自己的 /img /audio 上
  → LangBot 回复里带 URL → respback 补丁识别 media/* → 转成 QQ 图片/语音消息
  → 群聊再多一层 <pass/> 闸门补丁：模型判断不值得回应就不发
```

**注意**：QQ 侧对话记忆走 memory MCP（PG `mobius` 库），**不经过 Core**；
因此 QQ 在 Core 的任务/审计里只有媒体任务，没有对话轮次。

### 2.2 Core 链路（Web/Console 侧）

```
Console 网页 / Open WebUI Pipe
  → Möbius Core (8900)
      ├─ Agent 循环（DeepSeek + recall / remember / generate_image / speak）
      ├─ 记忆四层（PG `mobius_core`）+ 候选审批
      ├─ 任务中心 + 审计
      ├─ 媒体总开关 + 服务监管（ComfyUI / Kokoro / gsv）
      └─ REST /api/* → Media Gateway（普通 HTTP，不是 MCP）
  → Core 把图片 markdown 和 audio_url 直接拼进回复文本
```

### 2.3 两条链的关系

| | QQ | Web/Console |
|---|---|---|
| 大脑 | LangBot LocalAgent | Möbius Core |
| 人格 | LangBot 流水线 prompt | Core 读角色档案 constitution |
| 记忆 | memory MCP（PG `mobius`）| Core 记忆四层（PG `mobius_core`）|
| 任务/审计 | 只有媒体任务落在 Core | 全量落 Core |
| 媒体执行 | **共享同一个网关** | 共享同一个网关 |

合并方案（把 LangBot 的模型指向 Core 的 `/v1/chat/completions`）在
`docs/archive/reports-history/LANGBOT-CUTOVER.md`，**仍未执行**，是可逆的一步。
但执行前必须先决定 memory MCP 的去留：切了 Core 之后它是**第三份记忆**，
要么关掉，要么改写成 Core 的工具，否则三写。

---

## 3. 架构（组件与职责）

| 层 | 组件 | 端口 | 职责 | 当前状态 |
|---|---|---|---|---|
| 入口 | QQ / NapCat | 13000/13001/6099 | 收发消息 + WebUI | 在线 |
| 入口 | Console 网页 | 8900/console | 运维控制面 | 在线 |
| 入口 | Open WebUI Pipe | 3000 | 调试对话面 | **进程未运行**（脚本仍在仓库）|
| 平台适配 | LangBot | 5300 | QQ 消息、人格、模型调用、工具分发 | 在线（可被替代）|
| **核心** | **Möbius Core** | 8900 | 记忆、任务、审计、媒体模式、Agent 循环 | **Web 侧事实来源** |
| 媒体网关 | Media Gateway | 8899 | MCP 面 + REST 面、多底模路由、LoRA 注入、任务队列、媒体代理 | 在线，**stateless**（执行器）|
| 记忆网关 | memory MCP | 8902 | QQ 侧长期记忆 remember/recall/forget/候选/统计 | 在线（**第二套记忆**）|
| 向量基建 | bge-m3 | 8903 | 嵌入（watchdog 拉起）| 在线，**仅 Graphiti 冒烟用** |
| 向量基建 | FalkorDB | 63790 | 图记忆 | **未接入生产** |
| 后端 | ComfyUI | 8188 | 生图 | **常驻**（prewarm）|
| 后端 | Kokoro | 7870 | 28 音色 TTS | 在线 |
| 后端 | GPT-SoVITS | 7873 原生 / 7871 OpenAI 适配 | 克隆音色 | 在线（只有一条参考音）|
| 存储 | PostgreSQL | 55432 | `langbot` / `mobius` / `mobius_core` | 在线 |
| 存储 | Redis | 56379 | 轮询节流（仅瞬时状态）| 在线 |
| 画像 | PowerContext | 8000 | 按 QQ 号独立画像 | 在线 |
| 运维 | watchdog.py | — | 容器/服务/提示词监督 + 自愈 + DSH 只读诊断 | 在线 |
| 运维 | 计划任务 Mobius Boot | — | 开机自启 | 在位 |

**设计铁律**（契约见 `docs/ARCHITECTURE-v1.1.md`）：
1. Web/Console 侧记忆/任务/权限的事实来源只有 Core；入口不自存。
   —— **QQ 侧目前是例外**（memory MCP 自存一套）。
2. ~~ComfyUI 不常驻，默认按需唤醒，闲置休眠。~~
   → **已由产品决策改为 `prewarm` 常驻**，见 `docs/adr/ADR-005-media-mode-default.md`。
3. 模型不能自行反复启停大服务（模式 + 冷却 + 退避三重约束）。
4. 角色核心设定只有人能改；模型只能写「候选记忆」。
5. 不替换旧 MCP，而是把它升级成 QQ 专用媒体网关。
6. **（新）MCP 面必须无状态**：网关重启不得让客户端工具集失效，
   见 `docs/adr/ADR-004-mcp-stateless.md`。

---

## 4. 数据模型（PG 三库，2026-11-02 起）

| 库 | 表 | 内容 | 写入者 |
|---|---|---|---|
| `mobius_core` | `constitution` | 角色宪法，版本化 | 仅 Console |
| | `user_memory` | 已确认的用户偏好/关系 | 审批后落库 |
| | `memory_candidates` | 候选记忆（模型只能写这里）| 模型 → 人工审批 |
| | `timeline` | 事件/剧情时间线 | Core 自动 |
| | `turns` | 临时工作记忆（按会话，可过期）| Core 自动 |
| | `tasks` | 统一任务（**六态** + 远程 id + 结果）| Core |
| | `audit` | 谁在哪个入口、用了什么、耗时、成败 | Core |
| | `kv` | 媒体模式等全局状态 | Core/Console |
| | `identities` | Web 身份 ↔ QQ 身份绑定 | Console（人工）|
| `mobius` | `memory_facts` / `memory_candidates` / `memory_events` | **QQ 侧长期记忆** | memory MCP（LangBot 调）|
| | 任务快照 + outbox | 网关任务持久化 | 网关 |
| `langbot` | pipeline / monitoring_messages / agent_run 等 | QQ 平台数据 | LangBot |

- 六态词表（含 `submission_unknown`）见 `docs/adr/ADR-002-state-vocabulary.md`。
- PG 迁移与分域见 `docs/adr/ADR-003-postgres-migration.md`。
- 角色档案是**文件**：`data/characters/*.json`（热生效，Core 与网关共用同一份），
  当前 **12 个**。
- `config.json` 已 `db_backend: "pg"`，旧的 `data/mobius.db` 不再使用（代码里仍有残留默认值）。

---

## 5. 能力账（逐项，按 2026-11-06 实测）

| 能力 | 状态 | 证据 |
|---|---|---|
| MCP over HTTP 接入 | ✅ | `/mcp` 探针真实握手，**14 个工具**（§5.1）|
| MCP **无状态化** | ✅ 本轮 | `stateless_http=True`，网关重启不再掉工具（ADR-004）|
| 列 checkpoint | ✅ | **1 个**（Illustrious-XL-v1.0）|
| 多底模路由 | ✅ 新 | **6 支**：anime_illustrious / qwen_image_2_1 / flux3_klein / flux1_schnell / animagine_xl_3_1 / minimax_h3（后者登记为不可用，请求直接拒）|
| 列 LoRA（含搜索）| ✅ | **31 个** |
| 角色档案驱动 | ✅ | **12 个角色**，中文名/别名命中，自动补 ckpt/LoRA/尺寸/画风 |
| 角色人格卡 | ✅ | 12 个角色各有 constitution / speech_style / taboo / visual_anchor |
| 生成可复现 | ✅ | seed 提交前落库；同 seed 字节级一致，换 seed 图必不同 |
| LoRA 链式注入 | ✅ | 多 LoRA、去重、权重截断，ComfyUI payload 里 `lora_1..N` 可查 |
| 文生图 | ✅ | 4 步 Schnell 约 20–40s；SDXL 角色单张 47–95s |
| 异步任务 + 取消 | ✅ | 队列删除 / ComfyUI interrupt 都验过 |
| 图生图 / 上传参考图 | ⚠️ 部分 | upscale/removebg 支持单张参考图注入，端到端**未系统验收** |
| ControlNet / IP-Adapter / 人脸保持 | ❌ | 工作流文件在，网关明确拒绝（契约 §10 范围外）|
| TTS（Kokoro）| ✅ | 28 音色，wav 真实产出 |
| **克隆音色（GPT-SoVITS）** | ⚠️ 可用但只有一条音色 | 7871 适配器 `/v1/models` 硬编码 `id=mobius`，参考音固定；`_post_tts` 会丢弃调用方传的 voice |
| 角色 → 克隆音色绑定 | ❌ | 12 份角色档案 `voice.engine` **全是 kokoro**，没有一个指向克隆引擎 |
| 图片/音频回传 QQ | ✅ | 11-06 有真实投递记录（respback 补丁在位）；手机端人工确认仍缺一次 |
| 群聊自动判断是否回应 | ✅ 新 | `<pass/>` 闸门补丁 + 流水线提示词规则 |
| 群聊默认语音回复 | ✅ 新 | 提示词规则：群聊每条回复调 `tts_speak(engine="mobius")`，8–60 字，失败退回文字 |
| Core 统一对话（SSE）| ✅ | 事件 meta/text/task/done |
| OpenAI 兼容端点 | ✅ | 标准响应 + 流式分块（给 LangBot/WebUI）|
| Core 记忆四层 + 候选审批 + 遗忘 | ✅ | 候选可见 → 审批生效 → 遗忘 全链路 |
| QQ 侧记忆（memory MCP）| ⚠️ 在线但脆弱 | 启动时 `ensure_schema()` 同步连 PG，PG 未就绪则端口迟迟不出现；LangBot 侧无 MCP 重连 |
| 向量检索记忆 | ❌ | Graphiti/FalkorDB/bge-m3 基建齐备，**只有冒烟脚本，未接入生产** |
| 任务中心 + 审计 | ✅ | 同一次生图在 QQ/Console 用同一个 task id |
| Console 六页 | ✅ | 真实 Core 集成 + mock 全通过 |
| 媒体总开关（off/on_demand/prewarm）| ✅ | 运行值来自 PG `kv.media_mode` = **prewarm**；代码兜底默认仍是 `on_demand`（ADR-005）|
| 闲置休眠 | ⚠️ 仅 on_demand 生效 | `idle_loop` 在非 on_demand 直接跳过：prewarm 下 **ComfyUI 不会被休眠**，`idle_sleep_sec=600` 用不上 |
| 开机自启 | ✅ | 计划任务 Mobius Boot；`/api/pulse` 供自检 |
| 服务监管与自愈 | ⚠️ 兜底可用、判据待重写 | 见 §6.2 第 9 条 |
| 按 QQ 号预设画像 | ✅ 新 | PowerContext，6 个账号独立 Scope |
| 身份自动合并 | ❌ | 设计上就不做，需人工绑定 |
| Open WebUI 接同一核心 | ❌ 当前断 | Pipe 脚本与安装器都在仓库，但没有任何东西负责拉起 Open WebUI，3000 端口无监听 |

### 5.1 网关 MCP 工具（14）

`comfyui_generate` · `comfyui_task_submit` · `comfyui_task_status` · `comfyui_task_cancel` ·
`comfyui_status` · `comfyui_list_models` · `comfyui_list_loras` · `comfyui_list_characters` ·
`tts_speak` · `tts_list_voices` · `media_status` · `media_wake` · `media_sleep` · `media_set_mode`

### 5.2 memory MCP 工具（6）

`memory_remember` · `memory_recall` · `memory_forget` · `memory_list_candidates` ·
`memory_review_candidate` · `memory_stats`

---

## 6. 局限（要规划，先看这些）

### 6.1 架构性（不解决会长歪）

1. **双大脑未合并，而且现在是「1 大脑 + 2 记忆」**：QQ 人格在 LangBot、
   QQ 记忆在 memory MCP、Web 人格与记忆在 Core。合并时必须同时决定 memory MCP 的去留。
2. **LangBot 是被我们"绕过"的**：人格 prompt、记忆工具、媒体补丁都寄生在它里面，
   切 Core 时要么关掉要么迁移，否则多写。
3. **任务在两处存**：网关有 PG 任务快照，Core 有持久任务表；网关重启时
   "正在跑的那一张"仍可能变孤儿（靠 `remote_id` 回收）。
4. **克隆音色没有产品化**：引擎在跑，但只有一条固定参考音，角色档案不指向它，
   传 voice 也会被丢弃。「每个角色用自己的声音说话」目前是**能力就绪、产品未接**。
5. **Core 侧服务名与实际不符**：`config.json` 里 `f5`（enabled=false, 7871）与
   `gsv`（enabled=true, 7871）是同端口的两个名字，网关只认 `f5`，
   Core 面板上的「F5 灯」照的其实是 GSV。

### 6.2 工程性

6. **解释器分裂**：网关必须用 `D:\AI\qqbot-mcp\Scripts\python.exe`（mcp 2.3，无 fastapi），
   Core/运维用 `D:\Anaconda3\envs\open-webui\python.exe`，记忆栈用 `D:\AI\mobius-memory\...`。不能混。
7. **冷启动慢**：ComfyUI 首启 4–6 分钟（Defender 扫 custom nodes），超时配置 420s。
   prewarm 之后用户侧感受消失，且 prewarm 模式下闲置休眠不生效（§5），显存全程常驻。
8. **三处容器内热补丁**：语音回传、`<pass/>` 群聊闸门、LangBot 超时参数。
   `docker compose up --force-recreate` 会静默丢失，而**只有语音补丁有重放脚本**
   （`scripts/replay-langbot-voice-patch.ps1`），群聊闸门没有自动安装器。
9. **watchdog 的 MCP 判据是推断不是探测**：只看端口存活、`/api/pulse` 的 uptime 先后，
   以及日志里 `Failed to start MCP server` 字符串；真正的 `tools/list` 探测只存在于离线脚本
   `tests/probe_mcp.py`。11-06 的重启风暴（12:10–13:33 共 13 次）根因在此——
   stateless 化之后「会话失效」这个判据已不再成立。
   冷却 300s、活动感知推迟、失败水位去重、单实例锁都已加，但**判据本身要重写**。
10. **单机单用户假设**：没有鉴权分级，Console 靠"只监听本机/回环免鉴权"。
11. **GPU 只有 8GB**：SDXL + LoRA 已占 ~6.4GB，队列并发锁死为 1；
    视频级模型（minimax_h3，19.5GB unet）只能登记为不可用。
12. **带代理环境变量时验收会崩**：`tests/verify.py` 会被 Clash 的 `[::1]:7897` 干扰
    （`httpx.InvalidURL: Invalid port`）。跑之前清空 proxy 环境变量；
    11-06 清空后 `--quick` = 12 通过 / 4 跳过 / 0 失败。
13. **凭据卫生刚起步**：字面量密钥已从代码里清零（`ops/credentials.py` 单一来源），
    但**历史上提交过的密钥未轮换**，`config.json.bak` 仍存在于 git 历史中。
14. **验收脚本的标签会误导**：`tests/verify.py:109` 的 `A1c「checkpoint 枚举」`
    实际断言的是**底模列表**（`/api/models.models`），和真正的 checkpoint 枚举
    （`/api/models.checkpoints`，当前 1 个）不是一回事。改名或改断言，别让人以为有 6 个 checkpoint。
15. **两处「默认值」与运行值不一致**：媒体模式代码兜底是 `on_demand`、运行值是 `prewarm`
    （只存在 PG `kv`，重装/换库就回到 on_demand）；`core/store.py:28` 的 SQLite 路径默认值
    也已失效。这类"配置漂移"需要一次统一收口。

### 6.3 产品性

16. **角色一致性只到"记忆 + 宪法"层**：没有向量检索、没有长期剧情压缩，聊多了会漂；
    LLM 本身也会漂。
17. **"可塑性"是半自动的**：模型写候选 → 人审批。全自动写入没做，也建议先别做。
18. **没有失败重试的产品化**：失败只留审计，用户要自己再要一次。
19. **Console 是运维台，不是聊天台**：群友永远不该进后台，这点是对的。
20. **Web 调试口现在是断的**：想按同人格在网页调试，得先把 Open WebUI 拉起来并装 Pipe。

---

## 7. 形式（用户看到什么）

| 角色 | 入口 | 现在是什么样 |
|---|---|---|
| QQ 群友（群）| QQ 群 | 机器人**自动判断**要不要接话；接话时**默认发语音**，要图发图 |
| QQ 群友（私聊）| QQ 私聊 | 正常文字回复 + 按需图/语音（私聊不出 `<pass/>`）|
| 你（运营）| `http://127.0.0.1:8900/console` | 深色运营台：概览/任务/角色/记忆/审计/设置 |
| 你（调试）| Open WebUI + `Mobius` Pipe | **当前不可用**（进程未启动）|

Console 的五层信息结构：能不能用（概览）→ 它应该像谁（角色）→
它记住了什么（记忆）→ 它正在做什么（任务）→ 为什么会这样（审计）。

---

## 8. 本轮（2026-11-06）改了什么

| 时间 | 提交 | 内容 |
|---|---|---|
| 11:55 | `5e86d7a` | 群聊改为自动判断是否回应（`<pass/>` 闸门 + 去掉 @ 过滤）|
| 12:09 | `bf383c2` | watchdog 同时检查 GSV 7871/7873，任一挂掉即整套重启并加载权重 |
| 12:16 | `c6b6635` | `<pass/>` 拦截兼容 At/Quote 段（之前会把 pass 当文本发出去）|
| 12:25 | `9bd8c4d` | 群聊默认走语音 |
| 13:10 | `708d18b` | TTS 原子落盘 + 写后校验 + 网关单实例锁 + 启动前清理残留进程 |
| 13:44 | `3691561` | **MCP 改为 stateless**，网关重启不再导致 LangBot 工具掉线（ADR-004）|
| 21:06 | `843c879` | 文档归档，`docs/` 根目录只留当前有效文档 |
| 21:2x | 未提交 | 多底模「画笔」路由 + 不支持组合显式拒绝（另有会话在改，勿覆盖）|
| 21:3x | 未提交 | 凭据收敛到 `ops/credentials.py`；`config.json.bak` 移出版本控制 |

**stateless 的根因回顾**：网关中午重启后，LangBot 手里旧的 MCP SSE 流变成死流且**不重连**，
工具调用挂在死流上直到 300s 超时，表现为「用 LoRA 生图报错」。
10-28 那轮的兜底是 watchdog 定点重启 LangBot；11-06 改成无状态——
每次请求独立，不再依赖长连接会话，也不再积累僵尸会话，兜底逻辑从此失去必要性（但还没删）。

---

## 9. 现在可以让你规划的选项（按代价排序）

| 选项 | 代价 | 收益 |
|---|---|---|
| A. watchdog 的 MCP 判据从「推断」改成「真探测 tools/list」 | 低 | 消除重启风暴，自愈变成有据可依 |
| B. 角色 → 克隆音色绑定（每角色一段参考音，`voice.engine=gsv`）| 中 | 「它用自己的声音说话」真正落地，产品差异点 |
| C. 三套记忆收敛（决定 memory MCP 去留）→ 顺带双大脑合并 | 中高 | 架构目标，之后记忆只写一处 |
| D. 群聊 `<pass/>` 闸门纳入自动重放脚本 | 低 | 容器重建不再静默丢行为 |
| E. Core 服务名 f5→gsv 正名 + 面板灯校正 | 低 | 运维看得懂，减少误判 |
| F. 已泄露凭据轮换 + git 历史里的 `config.json.bak` 清除 | 低（需你批准）| 安全债清掉 |
| G. 图生图 / 参考图端到端系统验收 | 中 | 「照这张图画」可用 |
| H. 向量检索记忆（Graphiti 接入生产）| 中高 | 长程一致性 |
| I. 重开 Open WebUI 调试口 | 低 | 恢复 Web 侧同人格调试 |
