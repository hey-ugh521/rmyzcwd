# LangBot 切到 Möbius Core（可选步骤，需产品经理确认后执行）

> **状态：未执行、未验收。** 这是给 PM 拍板后的操作手册，不是已完成的能力。
> 不切也不影响现有 QQ 功能——只是 QQ 与 Web 各自用一套大脑，记忆不共享。

## 为什么要切

不切时：

```
QQ → LangBot(LocalAgent + 八重樱 system prompt + DeepSeek) → MCP 工具
Web → Möbius Pipe → Core → DeepSeek
```

两套大脑、两套记忆。切了之后：

```
QQ → LangBot(只做 QQ 收发) → Core(人格/记忆/工具/任务) → DeepSeek + Media Gateway
Web → Möbius Pipe ────────► Core(同一套)
```

## 前置条件

1. Core 已在 8900 稳定运行，`python tests\verify.py --quick` 全绿。
2. Core 已纳入开机自启（`watchdog.py` 会拉起它）。
3. 已备份 `qqbot\langbot\data\langbot.db` 与 `qqbot\langbot\data\config.yaml`。

## 步骤

### 1. 让 LangBot 把会话 id 传给 Core

编辑 `qqbot\langbot\data\config.yaml`：

```yaml
llm_request_policy:
    conversation_header: x-session-id
```

LangBot 会把 `x-session-id: lb-<hash>` 附在每个 LLM 请求上
（hash 的输入是 workspace + bot + pipeline + session，所以同一个 QQ 私聊/群稳定不变）。
Core 用它做 session 归属，记忆不会串台。

改完 `docker restart langbot`。

### 2. 在 LangBot 里加一个「模型提供方」

WebUI → 模型 → 添加提供方：

| 字段 | 值 |
|---|---|
| 名称 | Möbius Core |
| 请求器 | OpenAI 兼容（openai-chat-completions） |
| Base URL | `http://host.docker.internal:8900/v1` |
| API Key | `<REDACTED>`（= `mobius/config.json` 的 `core.api_key`） |

### 3. 加一个模型

| 字段 | 值 |
|---|---|
| 名称 | `mobius-core` |
| 提供方 | Möbius Core |
| 能力 | 留空（纯文本；工具由 Core 内部完成） |
| 上下文长度 | 64k |

点「测试」应能看到 `GET /v1/models` 返回 `mobius-core`。

### 4. 把流水线的模型换成它

流水线 → `QQ 官方 API 流水线` → AI → 模型 → 主模型选 `mobius-core`。

### 5. 验收

- QQ 里发一句话 → 回复正常，且 `mobius\data\mobius.db` 出现该会话的 turn；
- 控制台「任务」页出现 `source=qq` 的任务；
- 说「画张布洛妮娅」→ QQ 收到图（图经 8899 代理，走 LangBot 现有 respback 补丁）；
- 说「以后叫我博士」→ 控制台「记忆 → 待确认」出现候选。

## 切换后必须处理的副作用

| 副作用 | 处理 |
|---|---|
| LocalAgent 的 MCP 工具调用失效（Core 不返回 tool_calls） | 正常。生图/语音改由 Core 内部工具完成，能力更强（有角色档案与 LoRA） |
| `LongTermMemory` 插件仍会写知识库，与 Core 记忆双写 | **建议在流水线插件里取消勾选 LongTermMemory**，否则两套记忆会互相干扰 |
| `AIImagePlugin` 也会自己调生图 | 建议同样取消勾选，避免与 Core 的 generate_image 重复 |
| Core 挂了 QQ 就只会报错 | watchdog 会拉 Core；接受这个依赖，或保留一键回滚 |
| Core 收到 `tools` 参数会忽略 | 无影响，Core 自己决定何时调工具 |

## 回滚（30 秒）

流水线 → AI → 模型 → 主模型改回 `deepseek-flash`（或原来的 `af171c6c-…`）。
`conversation_header` 可以留着（不影响 DeepSeek）。

## 风险评级

中。动的是 QQ 主链路，但完全可逆，且不改任何 LangBot 源码。
建议：先在自己单聊里试，不要直接在群里切。
