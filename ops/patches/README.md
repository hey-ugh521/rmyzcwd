# 本地补丁（vendored 源码）

记录对第三方/外部源码（主要是 `D:\AI\langbot-native`）的本地补丁，保证可复现、可审计、升级时可重放。

## 约束

- 补丁只针对**本地 vendored 源码**，不提交第三方仓库
- 每个补丁一个 `.patch`（unified diff，路径相对 LangBot 根）＋ 一段说明
- LangBot 升级后需重新应用；应用前先备份原文件
- 补丁行为必须可通过环境变量/配置关闭（便于排障）

## 补丁列表

### langbot-onebot-dedupe.patch

**问题**：NapCat/OneBot 在 WS 重连、重试或重放时可能把同一条消息事件投递多次，导致重复回复/重复出图。

**实现**：在 `AiocqhttpAdapter._handle_native_event()` 入口按 `message_id` 做进程内去重：

- 新增文件 `src/langbot/pkg/platform/adapters/aiocqhttp/dedupe.py`（`MessageDedupe`：TTL + LRU）
- 适配器在 meta/self 过滤之后、事件转换之前丢弃重复 `message_id`
- 连接建立时记录一行配置日志
- 环境变量：
  - `MOBIUS_ONEBOT_DEDUPE_TTL`（默认 600 秒；0 = 关闭）
  - `MOBIUS_ONEBOT_DEDUPE_MAX`（默认 2048 条）

**应用**：

```powershell
cd D:\AI\langbot-native
# 建议先 git status 确认工作区（LangBot 源码目录若为 git 仓库）
git apply --whitespace=nowarn C:\Users\lenovo\Desktop\_说明文档\mobius\ops\patches\langbot-onebot-dedupe.patch
```

或按 patch 内容手工修改（补丁很小）。

**验证**：

```powershell
D:\AI\qqbot-mcp\Scripts\python.exe tests\test_onebot_dedupe.py   # 单元
# 启动 LangBot 后日志应出现：OneBot message dedupe enabled: ttl=600s max=2048
```

**验证记录（2026-11-08）**：`tests/test_onebot_dedupe.py` → 10/10；用真实 `AiocqhttpAdapter` 对同一条事件投递两次 → `_dispatch_native_event` 仅调用 1 次。

**局限**：进程内缓存，LangBot 重启后清空；覆盖的是短时间重放窗口（默认 10 分钟）。跨重启去重需要持久化存储（可作为后续增强）。
