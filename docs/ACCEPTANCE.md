# Möbius MVP 验收标准与流程（v1 · 2026-11-06 校正）

> 面向产品经理。每条都写明「怎么验、看到什么算过、证据在哪」。
> 自动化部分：`python tests/verify.py`。人工部分：明确标了你要做的动作。
>
> **本版为 2026-10-28 定档的 v1，2026-11-06 按实际现状就地校正**，
> 校正处标 `【11-06】`。当前全景见 `docs/STATUS.md`；
> 两条新决策记录见 `docs/adr/ADR-004-mcp-stateless.md`、`docs/adr/ADR-005-media-mode-default.md`。

---

## 0. 本版 MVP 定义（范围锁定）

**一句话**：把「QQ 群友 → 角色对话 → 生图/语音 → 回传 QQ」这条主链，
从「固定工作流、无 LoRA、无记忆归属」升级为「角色档案驱动、可查可用 LoRA、
任务可追踪、记忆可审核」，并把控制台和 Web 入口接到同一个 Core 上。

**验收优先级**（也是砍需求的顺序，从下往上砍）：

| # | 能力 | 本版要求 | 【11-06】实际状态 |
|---|---|---|---|
| A1 | QQ 生图带角色 LoRA | 必须 | 达成（12 角色 + 31 LoRA）|
| A2 | 媒体服务自动/按需启动 | 必须 | 达成，**但默认已改为 prewarm**（ADR-005）|
| A3 | 语音合成 → QQ 真语音 | 必须 | 达成（Kokoro 28 音色）|
| A4 | 统一任务中心与审计 | 必须 | 达成（六态 + PG）|
| A5 | 记忆四层 + 候选审批 | 必须（机制）| Web 侧达成；**QQ 侧另有一套 memory MCP**，两套未收敛 |
| A6 | Open WebUI 接同一核心 | 必须（可跑通）| **当前断**：进程未运行，脚本仍在仓库 |
| A7 | 图生图 / 参考图 | 加分，不阻塞 | 部分实现，端到端未系统验收 |
| A8 | 克隆音色 | 本版只留接口与状态，不做 | **引擎已上线**（GPT-SoVITS），但只有一条固定参考音、未绑角色 |

---

## A0. 冷机开机自动恢复

**步骤**

1. 重启电脑，不做任何手工操作。
2. 登录后等 1~2 分钟。
3. 打开 `http://127.0.0.1:8900/console`。

**判定通过**

- Core(8900)、Media Gateway(8899)、watchdog 自动起来；
- 【11-06】ComfyUI(8188) **也起来**（`prewarm` + `prewarm_on_start=true`）——
  旧版写的「保持 `down`、不占显存」是 on_demand 语义，**已废止**；
- Kokoro(7870) 就绪；克隆引擎 7871/7873 由 watchdog 拉起；
- 控制台「概览」各灯状态与实际端口一致；
- 计划任务「Mobius Boot」状态为 Running；
- boot 自检输出两行（`scripts/boot-mobius.ps1:174-175`）：
  `开机自检：Core=OK  Gateway=OK  LangBot=OK  NapCat=在线  MCP=OK` + `开机自检结论：PASS`。
  【11-06】其中 `MCP` 这项的判据是**读 LangBot 容器日志里有没有
  `Failed to start MCP server`**（推断，不是工具集探测），与 STATUS §6.2 第 9 条同源。

**实测记录**

- 2026-10-28 22:22 机器重启 → 22:28 计划任务拉起：Core 19s 就绪，网关约 20s 后就绪。
- 入口脚本 `scripts\boot-mobius.ps1`；注册/卸载 `scripts\install-autostart.ps1`。
- 该脚本自带兜底：Core 或 watchdog 掉线会在 2 分钟内被重新拉起。
- 【11-06】已知坑：开机时 Docker 容器与宿主机进程并行启动，LangBot 早于网关就绪会
  导致 MCP 初始化失败且不重试（`archive/reports-history/POSTMORTEM-20261028.md` §B.4）。
  现由「网关 `/api/pulse` + boot 竞态处理 + **MCP 无状态化**」三层消除，
  无状态化之后这一项应**不再需要**重启 LangBot 来重建会话。

**想改回按需唤醒**：Console「设置」把媒体模式设为 `on_demand`（可让闲置 600s 后卸载模型），
或把 `config.json` 的 `core.prewarm_on_start` 设为 `false`。
**当前产品决策是保持 prewarm**，理由与语义边界见 ADR-005。

---

## A1. QQ 生图带角色 LoRA

**前置**：Media Gateway 已在 8899 提供服务。

**步骤**

1. 在控制台「角色」页确认 `bronya` 的 LoRA = `Bronya_Zaychik24in1_Hi3_IL-03.safetensors`，权重 0.75。
2. 在 QQ 群里说：「画一张布洛妮娅在雪地里看雪的样子」。
   【11-06】群里**不再要求 @ 机器人**：是否回应由模型判断（`<pass/>` 闸门），
   因此「没回」也可能是它判断不值得回——验收时要连发 2–3 条不同内容确认。
3. 观察回复：应先出现「正在处理」，随后出一张图（prewarm 下无需唤醒）。
4. 控制台「任务」页应出现一条 `kind=image`、`status=succeeded` 的任务，
   参数里能看到实际注入的 checkpoint / LoRA / seed。
5. 「审计」页应有 `image.generate` 记录，含耗时。

**判定通过**

- QQ 收到的是**图片消息**而不是链接文本；
- 图片里角色特征与 LoRA 对应（不是纯 checkpoint 的随机造型）；
- 任务记录里 LoRA 名与角色档案一致；
- 【11-06】同 seed 重发应字节级一致（可复现是硬指标）。

**证据**：【11-06】PG `mobius_core` 的 `tasks` / `audit` 表（旧版 `data/mobius.db` 已停用）；
`D:\AI\comic\ComfyUI_windows_portable\ComfyUI\output\` 下的 PNG；
ComfyUI prompt 节点图包含 `lora_1..N`。

**常见失败**：
- 回复只有文字 → 看任务 `error` 字段；`comfyui_unavailable` → 媒体模式为 off 或唤醒失败；
- 【11-06，**工作区在制、尚未提交**】`model_unavailable` → 选了本机带不动的底模（如 minimax_h3）；
- 【11-06，同上】`workflow_no_character` / `workflow_no_lora` → 对非 Illustrious 底模传了角色卡或 LoRA，
  这是**有意的显式拒绝**，不是 bug：角色一致性只有 `anime_illustrious` 支持。

---

## A2. 媒体服务自动/按需启动

**步骤**

1. 控制台「概览」把媒体模式设为 `on_demand`（本项要验的是**能力**，不是默认值）。
2. 控制台点「休眠」（或等闲置 600s），确认 ComfyUI 状态变 `down`
   或 `comfy_models_unloaded: true`。
3. 在 QQ 请求一次生图。
4. 观察：Core 日志出现 `service.start.comfyui`，任务 `queued → running → succeeded`。
5. 再等闲置时间到，确认自动卸载模型（优先 `/free` 卸载而非杀进程，省冷启动）。
6. 【11-06】验完把模式切回 `prewarm`（当前默认，ADR-005）。

**判定通过**

- 冷启动全流程 ≤ 7 分钟；**实测**：文件缓存热时 32.6s、刚重启后首启约 4~6 分钟
  （Defender 扫描 custom nodes，配置里 `start_timeout_sec=420`）；
- 唤醒失败时不重复狂拉：连续 3 次失败后进入 5 分钟退避（日志/审计可见）；
- **超时后不会重复 spawn 第二个进程**：超时但进程仍在跑时状态保持 `starting`；
- 【11-06】面板灯与端口一致，但注意**「F5」这个灯现在照的是 GPT-SoVITS(7871)**：
  `config.json` 里 `f5` 与 `gsv` 是同端口两个名字，网关只认 `f5`。
  命名待正名（STATUS §6.1 第 5 条）。

**证据**：`logs/` 下的 supervisor 审计、`logs/comfyui.log`、`nvidia-smi` 前后对比。

---

## A3. 语音合成 → QQ 真语音

**步骤**

1. QQ 私聊里说「用布洛妮娅的声音说一句『今天也辛苦了』」。
2. 回复末尾出现一条 `http://host.docker.internal:8899/audio?file=....wav`（LangBot 侧暗号）。
3. LangBot 的 respback 补丁把它转成 QQ 语音消息（气泡可播放）。
4. 【11-06】群聊里**默认就应该是语音**：提示词规则要求每条群聊回复调
   `tts_speak(engine="mobius")`，8–60 字，失败退回文字。

**判定通过**

- 手机端收到的是语音条，不是 URL 文本；
- 控制台任务里 `kind=voice` `status=succeeded`，result 有 filename/秒数；
- 音色与角色档案 `voice.voice` 一致；
- 【11-06】**仍缺一次手机端人工确认**（日志能证明投递，不能证明你手机上听到的是语音条）。

**已知风险**：语音回传、`<pass/>` 群聊闸门、超时参数都是**容器内热补丁**。
`docker compose up --force-recreate` 会静默丢掉三者；
目前**只有语音补丁有重放脚本**（`scripts\replay-langbot-voice-patch.ps1`，幂等、带校验与重启），
群聊闸门（`ops/patches/respback_group_autoreply_gate.py`）**没有自动安装器**。

---

## A4. 统一任务中心与审计

**判定通过**

- 同一次生图，在 QQ 回复、控制台任务页、审计页看到的是**同一个 task id**；
- 【11-06】任务有 **六态**：`queued / running / succeeded / failed / canceled / submission_unknown`
  （旧版写五态；第六态见 `docs/adr/ADR-002-state-vocabulary.md`），失败有 `error` 文本；
- 控制台能取消一个进行中的任务，网关侧队列里对应项也消失；
- 审计能回答：谁（user_id）、在哪个入口（source）、用了哪个角色/底模/LoRA/音色、耗时多少；
- 【11-06】轮询要有节流与上限：同一任务轮询被 Redis `SET NX PX` 节流，
  轮询 60 次仍未结束应**停止轮询并告知任务仍在后台**，轮询耗尽 ≠ 失败。

---

## A5. 记忆四层 + 候选审批

**步骤**

1. QQ 里说：「以后别叫我兄弟，叫我博士」。
2. 回复正常。
3. 控制台「记忆 → 待确认」出现一条候选：`key=称呼 value=希望被叫博士`，来源是该 QQ 会话。
4. 点「确认」。
5. 再开一轮对话问「你该怎么称呼我」，机器人应使用「博士」。
6. 控制台「遗忘」该条，再问一次，应恢复默认称呼。

**判定通过**

- 模型**不能**直接写入生效记忆，只能进候选队列（硬约束）；
- 审批后新会话立刻生效（不需要重启）；
- 角色宪法只能由控制台改，且版本号递增、旧版本保留。

**【11-06】本项有一个结构性未决问题**：QQ 与 Web 走的是**两套记忆**——

| 入口 | 记忆系统 | 存储 | 审批 |
|---|---|---|---|
| Web/Console | Core 四层 | PG `mobius_core` | 有（候选 → 人工审批）|
| QQ | memory MCP `:8902`（6 工具）| PG `mobius` 三表 | 有（`memory_review_candidate`）|

所以第 3 步「控制台出现候选」对 QQ 会话**不一定成立**：QQ 的候选在 memory MCP 里，
不一定投影到 Core 控制台。验收前要先确认你看的是哪一套。
收敛方案是 STATUS §9 选项 C，未决策。
另注：memory MCP 启动时同步连 PG，PG 未就绪则端口迟迟不出现，LangBot 侧无重连
（历史上表现为 QQ 记忆工具间歇性不可用）。

---

## A6. Open WebUI 接同一核心

**【11-06】现状：本项当前无法验收——Open WebUI 进程没在跑（3000 端口无监听）。**
仓库侧的东西都还在：`openwebui/mobius_pipe.py`（打 Core `/v1/chat`）、
`scripts/install-openwebui-pipe.py`（登录 → 创建 → 启用 → 校验模型列表）。
没有任何 watchdog / boot 脚本负责拉起 Open WebUI。

**步骤（先恢复再验）**

0. 启动 Open WebUI（国内网络需 `HF_ENDPOINT=https://hf-mirror.com`，
   否则卡在下载 `all-MiniLM-L6-v2`；实测镜像下 72s 就绪）。
1. `python scripts/install-openwebui-pipe.py` 安装 Pipe。
2. 模型列表出现 `Möbius`，选它对话。
3. 【11-06】注意端口：NapCat 的 3000 已挪到宿主机 **13000**，WebUI 用 3000。
4. 在 Web 里说的「叫博士」偏好，与 QQ 里是同一份记忆
   （**仅在身份已绑定且记忆收敛后成立**）。
5. 在 Web 里触发一次生图，控制台任务页显示 `source=web`。

**判定通过**

- Web 与 QQ 的行为差异仅限「入口」，人格/记忆/工具是同一套
  —— 【11-06】**此条尚不成立**：QQ 人格在 LangBot 流水线，Web 人格在 Core，双大脑未合并。

---

## A7/A8. 加分项（不阻塞）

- **图生图**：`POST /api/generate` 带 `reference_image`，产出与参考图构图相关。
  【11-06】`upscale` / `removebg` 支持单张参考图注入，端到端**未系统验收**。
- **克隆音色**：【11-06 更新】原 v1 写「7871 离线，仅状态位」已过时。
  现在 GPT-SoVITS **在线**（7873 原生 + 7871 OpenAI 适配，watchdog 保活），
  但离"角色各自的声音"还差三步，因此**仍不列入验收**：
  1. 适配器 `/v1/models` 硬编码一条 `id=mobius`，参考音固定，**没有多音色可枚举**；
  2. `_post_tts` 会丢弃调用方传的 `voice`，传谁都是同一个音色；
  3. 12 份角色档案的 `voice.engine` **全是 kokoro**，没有一个指向克隆引擎。

  即：**能力就绪，产品未接**。要做的是 STATUS §9 选项 B。

---

## 端到端用户流程（验收时按这条走一遍）

```
① 冷机开机
   boot-mobius.ps1 → Core(8900) + Gateway(8899) + Kokoro(7870)
   watchdog.py → napcat / langbot 容器 + GSV(7871/7873) + bge-m3(8903)
   【11-06】ComfyUI(8188) 一起启动（prewarm，ADR-005），不再"保持 down"

② 群友闲聊
   QQ → NapCat → LangBot（DeepSeek + 流水线人格）→ 回 QQ
   是否接话由模型判断（<pass/>），群聊默认发语音
   【11-06】注意此路径**不经过 Core**，对话记忆在 memory MCP(8902)
   ComfyUI 全程常驻（显存一直在占，这是本次决策的代价）

③ 群友要图
   "画张布洛妮娅" → LangBot 调 网关 MCP → 按 bronya 档案注入 checkpoint+LoRA
   → 生成 → 图片经 8899 代理 → respback 补丁转 QQ 图片消息
   → 任务入 Core 任务中心，审计留痕
   【11-06】prewarm 下无唤醒等待；若切 on_demand 才有"唤醒 → 闲置卸载"两段

④ 群友要语音
   "用她声音说一句…" → Kokoro（或 GSV）合成 → audio_url 附在回复最后一行
   → LangBot respback 补丁转真语音 → QQ 语音条

⑤ 群友纠正记忆
   "别叫我兄弟" → 候选记忆入队 → 审批生效 → 下一轮生效
   【11-06】QQ 侧候选在 memory MCP，Web 侧在 Core；两套未收敛（A5）

⑥ 你在后台
   Console：看状态/任务/审计、改角色档案、审记忆、切媒体模式
   Open WebUI：【11-06】当前不可用，需先启动并装 Pipe
```

---

## 回滚方案（验收失败时）

| 层 | 回滚动作 | 影响 |
|---|---|---|
| Media Gateway | 停 8899 上的 v2；旧版单文件 MCP 仍在 `D:\AI\qqbot-mcp\comfyui_mcp.py`（未修改） | 回到「无 LoRA、固定工作流」 |
| MCP 会话 | 【11-06】stateless 化**不需要回滚**；若真要回滚见 ADR-004（须同时恢复"网关重启后重建会话"纪律） | — |
| 媒体模式 | Console 切 `on_demand` 或 `prewarm_on_start=false` | 回到按需唤醒 + 闲置休眠 |
| LangBot 指向 | 未切换前无需回滚；切换脚本自带 `--rollback` | QQ 回到直连 DeepSeek |
| Core | 停 8900；QQ 链路不依赖它时无影响 | 失去控制台与 Web 入口 |
| 记忆 | 【11-06】PG：`mobius_core`（Core）与 `mobius`（memory MCP）**两库分别备份**，`data/mobius.db` 已停用 | 记忆回到上一状态 |
| 任务存储 | 网关 `MOBIUS_TASK_STORE=sqlite`；Core `db_backend` 改回 sqlite（数据不回写） | 回到 SQLite，写锁风险重现（ADR-003）|

---

## 不验收（2026-11-06 口径）

- ControlNet / IP-Adapter / 人脸保持 / 多参考图 FaceID
- 多用户权限、OAuth、公网暴露
- **向量检索记忆**：Graphiti + FalkorDB(63790) + bge-m3(8903) 基建齐备，
  但**只有冒烟脚本 `ops/memory/smoke_graphiti.py`，未接入生产链路**
- 自动身份合并（Web 身份与 QQ 身份需人工绑定）
- **多角色克隆音色**（引擎在线，但单参考音、voice 参数被丢弃、角色档案未指向）
- 【11-06 新增】**双大脑合并 / 三套记忆收敛**：仍是最大结构性未决项
- 【11-06 新增】**密钥轮换与 git 历史清除**：代码字面量已清零（`ops/credentials.py`），
  历史提交里的密钥未轮换、`config.json.bak` 仍在历史中——需产品经理批准后再动

---

## 自动化验收怎么跑（【11-06】补充）

```bash
# 必须先清掉代理环境变量，否则 Clash 的 [::1]:7897 会让 httpx 抛 InvalidURL
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
    NO_PROXY="*" \
    D:/Anaconda3/envs/open-webui/python.exe tests/verify.py --quick
```

2026-11-06 实测：**12 通过 / 4 跳过 / 0 失败**。
跳过项通常是端到端人工项（手机端语音、Open WebUI、图生图）。
