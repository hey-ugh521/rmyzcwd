# Möbius MVP 验收标准与流程（v1）

> 面向产品经理。每条都写明「怎么验、看到什么算过、证据在哪」。
> 自动化部分：`python tests/verify.py`。人工部分：明确标了你要做的动作。

---

## 0. 本版 MVP 定义（范围锁定）

**一句话**：把「QQ 群友 → 角色对话 → 生图/语音 → 回传 QQ」这条主链，
从「固定工作流、无 LoRA、无记忆归属」升级为「角色档案驱动、可查可用 LoRA、
任务可追踪、记忆可审核」，并把控制台和 Web 入口接到同一个 Core 上。

**验收优先级**（也是砍需求的顺序，从下往上砍）：

| # | 能力 | 本版要求 |
|---|---|---|
| A1 | QQ 生图带角色 LoRA | 必须 |
| A2 | 媒体服务自动/按需启动 | 必须 |
| A3 | 语音合成 → QQ 真语音 | 必须 |
| A4 | 统一任务中心与审计 | 必须 |
| A5 | 记忆四层 + 候选审批 | 必须（机制） |
| A6 | Open WebUI 接同一核心 | 必须（可跑通） |
| A7 | 图生图 / 参考图 | 加分，不阻塞 |
| A8 | 克隆音色 | 本版只留接口与状态，不做 |

---

## A0. 冷机开机自动恢复（已实测）

**步骤**

1. 重启电脑，不做任何手工操作。
2. 登录后等 1~2 分钟。
3. 打开 `http://127.0.0.1:8900/console`。

**判定通过**

- Core(8900)、Media Gateway(8899)、watchdog 自动起来；
- ComfyUI(8188) 保持 `down`（`on_demand` 不占显存），Kokoro 同理；
- 控制台「概览」四个灯状态与实际端口一致；
- 计划任务「Mobius Boot」状态为 Running。

**实测记录**

- 2026-10-28 22:22 机器重启 → 22:28 计划任务拉起：
  Core 19s 就绪，网关约 20s 后就绪，watchdog 同步起来，ComfyUI 保持 down。
- 入口脚本 `scripts\boot-mobius.ps1`；注册/卸载 `scripts\install-autostart.ps1`。
- 该脚本自带兜底：Core 或 watchdog 掉线会在 2 分钟内被重新拉起。

**想开机就预热 GPU**：控制台「设置」打开「开机预热」，或把 `config.json` 的
`core.prewarm_on_start` 设为 `true`。默认关闭，因为用户明确要求「闲暇时段别占显存」。

---

## A1. QQ 生图带角色 LoRA

**前置**：Media Gateway 已切到 v2（8899），ComfyUI 在跑。

**步骤**

1. 在控制台「角色」页确认 `bronya` 的 LoRA = `Bronya_Zaychik24in1_Hi3_IL-03.safetensors`，权重 0.75。
2. 在 QQ 群里 @机器人：「画一张布洛妮娅在雪地里看雪的样子」。
3. 观察回复：应先出现「正在处理/正在唤醒」，随后出一张图。
4. 控制台「任务」页应出现一条 `kind=image`、`character=bronya`、`status=succeeded` 的任务，
   参数里能看到实际注入的 LoRA 名单。
5. 「审计」页应有 `image.generate` 记录，含耗时。

**判定通过**

- QQ 收到的是**图片消息**而不是链接文本；
- 图片里角色特征与 LoRA 对应（不是纯 checkpoint 的随机造型）；
- 任务记录里 LoRA 名与角色档案一致。

**证据**：`data/mobius.db` 的 `tasks`/`audit` 表；`D:\AI\comic\ComfyUI_windows_portable\ComfyUI\output\` 下的 PNG；
ComfyUI 日志里该 prompt 的节点图包含 `LoraLoader`。

**常见失败**：回复里只有文字没有图 → 看任务 `error` 字段；`comfyui_unavailable` → 媒体模式为 off 或唤醒失败。

---

## A2. 媒体服务自动/按需启动

**步骤**

1. 控制台「概览」把媒体模式设为 `on_demand`。
2. 控制台点「休眠」（或等闲置 10 分钟），确认 ComfyUI 状态变 `down`。
3. 在 QQ 请求一次生图。
4. 观察：Core 日志出现 `service.start.comfyui`，任务从 `queued` → `running` → `succeeded`。
5. 再等闲置时间到，确认 ComfyUI 自动休眠（显存释放）。

**判定通过**

- 冷启动全流程 ≤ 7 分钟；**实测**：文件缓存热时 32.6s、刚重启后首启约 4~6 分钟
  （Defender 扫描 custom nodes，配置里 `start_timeout_sec=420`）；
- 唤醒失败时不重复狂拉：连续 3 次失败后进入 5 分钟退避（日志/审计可见）；
- **超时后不会重复 spawn 第二个进程**：超时但进程仍在跑时状态保持 `starting`，后续请求继续等；
- 面板上四个灯（ComfyUI / Gateway / Kokoro / F5）状态与实际端口一致。

**证据**：`logs/supervisor` 审计、`logs/comfyui.log`、`nvidia-smi` 前后对比。

**实测记录**

- 2026-10-28 22:47：杀掉 ComfyUI → `POST /api/media/wake` → **32.6s** 就绪，pid 归 Core 所有。
- 同日 22:36 重启后首启曾超过原 240s 上限，因此把超时提到 420s 并修掉重复 spawn。

---

## A3. 语音合成 → QQ 真语音

**步骤**

1. QQ 里说「用布洛妮娅的声音说一句『今天也辛苦了』」。
2. 回复末尾出现一条 `http://host.docker.internal:8899/audio?file=....wav`（这是 LangBot 发的暗号）。
3. LangBot 的 respback 补丁把它转成 QQ 语音消息（气泡可播放）。

**判定通过**

- 手机端收到的是语音条，不是 URL 文本；
- 控制台任务里 `kind=voice` `status=succeeded`，result 有 filename/秒数；
- 音色与角色档案 `voice.voice` 一致。

**已知风险**：respack 补丁是容器内热补丁，容器重建即失效。
本版处理方式：`scripts\replay-langbot-voice-patch.ps1`（幂等，已打过会跳过；带校验与重启）。

---

## A4. 统一任务中心与审计

**判定通过**

- 同一次生图，在 QQ 回复、控制台任务页、审计页看到的是**同一个 task id**；
- 任务有 `queued/running/succeeded/failed/canceled` 五态，失败有 `error` 文本；
- 控制台能取消一个进行中的任务，网关侧队列里对应项也消失；
- 审计能回答：谁（user_id）、在哪个入口（source）、用了哪个角色/LoRA/音色、耗时多少。

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

- 模型**不能**直接写入生效记忆，只能进候选队列（这是硬约束）；
- 审批后新会话立刻生效（不需要重启）；
- 角色宪法只能由控制台改，且版本号递增、旧版本保留。

---

## A6. Open WebUI 接同一核心

**步骤**

1. 安装 `openwebui/mobius_pipe.py` 为 Open WebUI 的 Function (Pipe)。
2. 模型列表出现 `Möbius`，选它对话。
3. 在 Web 里说的「叫博士」偏好，与 QQ 里是同一份记忆（若身份已绑定）。
4. 在 Web 里触发一次生图，控制台任务页显示 `source=web`。

**判定通过**

- Web 与 QQ 的行为差异仅限「入口」，人格/记忆/工具是同一套；
- Web 生图任务与 QQ 生图任务在同一个任务中心里。

---

## A7/A8. 加分项（不阻塞）

- 图生图：`POST /api/generate` 带 `reference_image`，产出与参考图构图相关。
- 克隆音色：本版仅 `/api/voices` 返回 `f5` 不可用状态，不验收。

---

## 端到端用户流程（验收时按这条走一遍）

```
① 冷机开机
   start-mobius.ps1 → Core(8900) + Gateway(8899) + Kokoro(7870)
   watchdog.py  → napcat / langbot 容器
   ComfyUI 不启动（on_demand 模式）

② 群友闲聊
   QQ → NapCat → LangBot → Core → DeepSeek → 回 QQ
   ComfyUI 全程不占显存

③ 群友要图
   "画张布洛妮娅" → Core 判媒体模式 → 唤醒 ComfyUI（约 60-90s）
   → 按 bronya 角色档案注入 LoRA → 生成 → 图片经 8899 代理回 QQ
   → 任务入中心，审计留痕
   → 闲置 10 分钟 → ComfyUI 自动休眠释放显存

④ 群友要语音
   "用她声音说一句…" → Kokoro 合成 → audio_url 附在回复最后一行
   → LangBot respback 补丁转真语音 → QQ 语音条

⑤ 群友纠正记忆
   "别叫我兄弟" → 候选记忆入队 → 控制台确认 → 下一轮生效

⑥ 你在后台
   Console：看状态/任务/审计、改角色档案、审记忆、切媒体模式
   Open WebUI：选 Möbius 模型调试同一套人格
```

---

## 回滚方案（验收失败时）

| 层 | 回滚动作 | 影响 |
|---|---|---|
| Media Gateway | 停 8899 上的 v2，`watchdog` 自动拉起旧版 `D:\AI\qqbot-mcp\comfyui_mcp.py`（旧文件未改动） | 回到「无 LoRA、固定工作流」 |
| LangBot 指向 | 未切换前无需回滚；切换脚本自带 `--rollback` | QQ 回到直连 DeepSeek |
| Core | 停 8900；QQ 链路不依赖它时无影响 | 失去控制台与 Web 入口 |
| 记忆 | `data/mobius.db` 可整库备份/回滚 | 记忆回到上一状态 |

---

## 不验收（本版明确不做）

- F5 克隆音色的完整可用（7871 离线）
- ControlNet / IP-Adapter / 人脸保持
- 多用户权限、OAuth、公网暴露
- 向量检索记忆（本版 SQL + 关键词）
- 自动身份合并（Web 身份与 QQ 身份需人工绑定）
