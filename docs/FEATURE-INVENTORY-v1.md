# 目标功能清点（v1）—— 对照 bot 当前能力

> 时间：2026-11-03 00:35
> 依据：仓库文档（ACCEPTANCE/ARCHITECTURE/STATUS）+ bot 运行时实测（14 个 MCP 工具、7 个插件、当前 prompt、日志）
> 原则：只写有证据的状态；没验证的标「未验证」，不写成已完成

> **时效提示（2026-11-06）**：本文件是 11-03 的快照，保留原样作为规划输入。
> 其中「语音送达未验证」「群聊触发规则未做」「14 个工具但语音链路未知」等条目
> 已被 11-06 的改动改变，当前事实以 `docs/STATUS.md` 为准。

---

## 1. MVP 目标（来自 ACCEPTANCE.md）

| # | 目标能力 | 目标口径 | 现状 | 证据 / 缺口 |
|---|---|---|---|---|
| A0 | 冷机开机自动恢复 | Core/Gateway/watchdog 自启 | ✅ 已达成 | 今晚修了 boot 的端口检测；Core/Gateway/LangBot/NapCat/ComfyUI/Kokoro 全部开机自启 |
| A1 | QQ 生图带角色 LoRA | 角色档案驱动、LoRA 可查可用 | ⚠️ 部分 | MCP 工具支持 character/loras；`comic-panel-faceid` 等工作流存在；**未验证 LoRA 在 QQ 生图里真实生效** |
| A2 | 媒体服务自动/按需启动 | off / on_demand / prewarm | ✅ 已达成 | Core 监管；当前 prewarm（ComfyUI 常驻，不睡眠） |
| A3 | 语音合成 → QQ 真语音 | TTS 产物作为语音消息送达 | ⚠️ 部分 | `tts_speak` 回归通过（kokoro）；**QQ 端语音消息送达未验证**（图片桥接有，语音桥接未确认） |
| A4 | 统一任务中心与审计 | 任务可追踪/可审计 | ✅ 已达成 | Gateway PG 任务快照 + outbox → Core 投影（今晚 outbox 0 积压）+ 探针 + 审计表 |
| A5 | 记忆四层 + 候选审批 | 记忆可审核 | ❌ 未达成 | 见下节「记忆现状」 |
| A6 | Open WebUI 接同一核心 | 同一 Core 服务 Web 与 QQ | ❓ 未验证 | STATUS 记载 Core 接 Open WebUI；当前未验证 Open WebUI 是否在运行、是否走 Core |
| A7 | 图生图 / 参考图 | 加分项 | ⚠️ 未验证 | 工作流存在（panel-faceid、reference_image 参数），未做端到端验证 |
| A8 | 克隆音色 | 只留接口 | ❌ 未做 | F5-TTS 未部署 |

## 2. 记忆现状（目标 A5 的实际情况）

| 事实 | 证据 |
|---|---|
| LangBot 已启用 **LongTermMemory** 插件（rev=2） | `plugin_settings`：`candidate_extraction_enabled=false`、`candidate_auto_apply=false`、`auto_recall_top_k=3` |
| 但记忆检索**报错**，实际不可用 | 插件运行时日志：`retrieve_knowledge failed with ActionCallError [RetrievalError][kb_id=59bb4d88...]` |
| 根因线索 | LTM 配置的嵌入模型是 `bge-m3`，走 **Ollama 本地 provider**；Ollama 当前不可用/无模型 → 嵌入失败 → 知识库检索失败 |
| 我们的重型方案 | Graphiti + FalkorDB + 本地 bge-m3 已完成 **Phase A 基建**（中文写入/检索实测通过），Phase B/C/D 未做 |

**结论**：记忆有两条路在并行：LTM 插件（轻、已接入、但嵌入后端坏了）和 Graphiti（重、基建好了、未接入）。需要决定分工，否则会重复投入。

## 3. Bot 当前能力基线（实测）

| 维度 | 现状 |
|---|---|
| 模型 | deepseek-flash（LangBot provider） |
| 对话通道 | QQ 私聊（OneBot v11）+ WebChat；非流式回复（今晚改） |
| MCP 工具（14） | comfyui_generate / task_submit / task_status / task_cancel / comfyui_status / list_models / list_loras / list_characters / tts_speak / tts_list_voices / media_status / media_wake / media_sleep / media_set_mode |
| 插件（7，全启用） | LocalAgent、LangRAG、WebSearch、LongTermMemory、LinkAnaly、AIImagePlugin、meme-plugin-api |
| 人设 prompt | 零玖 + P0.4 生图规则 + 图片 URL 格式规则，共 2,731 字符，守卫保护 |
| 图片送达 | ✅ 桥接已修（今晚 00:13:50 真实命中：`已把 1 张图片 URL 转为图片消息`） |

## 4. 与 bot 对齐后发现的问题（需要决策）

1. **记忆双轨**：修 LTM（把嵌入接到本地 bge-m3 :8903，或换 LangBot Models 嵌入）？还是继续 Graphiti？还是分工（LTM=对话记忆，Graphiti=世界状态/时间线）？
2. **生图双通道**：AIImagePlugin 与 ComfyUI MCP 工具重叠，近期日志未见 AIImage 被调用——建议明确停用或指定唯一主通道。
3. **语音送达**：`tts_speak` 有产物，但 QQ 端是否收到语音消息未验证（图片桥接已做，语音是否同样处理未知）。
4. **双大脑未合并**：LangBot（QQ）与 Core（Console/Web）各有一套上下文/记忆/任务；STATUS 指出的合并开关（`docs/archive/reports-history/LANGBOT-CUTOVER.md`）尚未执行。
5. **群聊/多用户**：当前只验证过私聊；群聊触发规则、权限、多用户隔离未做。
6. **上下文管理**：仍无历史摘要/预算，长时间对话会稀释人设（今晚已踩过）。

## 5. 建议优先级（待你确认）

| 优先级 | 事项 | 理由 |
|---|---|---|
| P0 | 记忆路线拍板（LTM 修复 or Graphiti 接入 or 分工） | 决定后续 1–2 天投入方向 |
| P0 | 语音送达验证（A3） | 目标里的「必须」，当前状态未知 |
| P1 | LoRA 生效验证（A1） | 目标里的「必须」，只有工具层证据 |
| P1 | 停用/明确 AIImagePlugin 通道 | 避免双通道冲突 |
| P2 | 上下文管理（历史摘要） | 保护人设与成本 |
| P2 | 群聊/多用户设计 | 你之前明确说后面要多用户 |
| P3 | 双大脑合并（archive/reports-history/LANGBOT-CUTOVER.md） | 影响记忆/审计归属，建议等记忆方案定了再做 |

## 6. 需要你（和 bot）回答的问题

1. 记忆：要「轻但能用的 LTM」还是「重的 Graphiti 世界状态」？还是两者都要？
2. 语音：QQ 端要的是**语音条**还是文字+音频文件？
3. 生图：AIImagePlugin 是否直接停用？
4. 群聊：近期要支持群聊吗（影响 prompt/权限/记忆隔离设计）？
5. 双大脑：QQ 和 Console/Web 是否必须共享记忆与审计？
