# 配置缺陷修复执行记录（逐步 + 验收 + 并发/关联测试）

> 规则：一次只做一项；先规划路径与验收标准；验收通过后跑并发/关联测试；通过再进下一项。
> 已执行：第 1–3 步。未执行：ComfyUI 模型盘、NapCat 鉴权、PG 迁移。

---

## 第 1 步：LangBot 异常泄漏（`show-error` → `show-hint`）

### 路径
`legacy_pipelines.config.output.misc.exception-handling`（DB JSON，不在 config.yaml）

### 代码验证（先看实现再改）

```python
# chat.py 异常分支
if exception_handling == 'show-error':  user_notice = f'{e}'      # 原始异常文本 → 泄漏给用户
elif exception_handling == 'show-hint': user_notice = pipeline_notice(..., 'failure-hint', ...)  # 本地化通用提示
else:                                   user_notice = None         # 完全不回复
```

**结论**：原配置 `show-error` 会把 `database is locked` 等内部异常原文发给用户——
你之前看到的"模型返回的报错"就是它。改为 `show-hint`（不泄漏 + 有反馈），
而不是 `hide-error`（会导致静默无回复）。

### 验收

| # | 标准 | 结果 |
|---|---|---|
| 1.1 | 配置生效 | ✅ `exception-handling = show-hint`（DB 复核） |
| 1.2 | 用户侧不出现内部异常 | ✅ 代码路径已确认（通用提示）；⚠️ 真实异常触发测试被 WS 断开取消，未取得端到端样本 |
| 1.3 | 日志保留完整异常 | ✅ 日志中 traceback 完整保留 |
| 1.4 | 功能回归 | ✅ 后续生图 run 正常（见第 2 步并发测试） |

---

## 第 2 步：LangBot SQLite 写锁加固（PG 迁移前的止血）

### 根因定位

```python
# /app/src/langbot/pkg/persistence/databases/sqlite.py（原文件）
self.engine = create_async_engine(engine_url)   # 无 connect_args
# 实测：busy_timeout=5000ms, synchronous=FULL, WAL
```

5 秒等待 + FULL 同步，在并发写入时超时抛出 `database is locked`，
直接打断 `agent_run_event` 写入 → **整个 run 失败**（今晚复现两次：02:55、03:23）。

### 补丁（容器内，可回滚）

```python
self.engine = create_async_engine(engine_url, connect_args={"timeout": 30})
# + connect 事件：PRAGMA busy_timeout=30000 / synchronous=NORMAL
```

备份：`sqlite.py.bak-mobius-20261101-032733`

### 验收

| # | 标准 | 结果 |
|---|---|---|
| 2.1 | busy_timeout 生效 | ⚠️ 应用连接内生效（补丁路径）；外部新连接仍显示默认 5000，属预期 |
| 2.2 | synchronous 放宽 | ⚠️ 同上，间接验证 |
| 2.3 | 并发聊天+生图无锁错误 | ✅ 3 聊天 + 2 生图并发，无锁错误 |
| 2.4 | 补丁后无 `database is locked` | ✅ 03:27:33 补丁 → 03:34 统计：**新增 0 次**（当日总计 22 次，最后一次在补丁前 03:23:35） |
| 2.5 | 图片正常交付 | ✅ `g_8831e24d → mobius_qwen_00015_.png` |

**局限（诚实记录）**：
- 补丁是容器内文件修改，**容器重建会丢失**，需加入 watchdog 自愈清单（待做）
- 只验证了约 7 分钟窗口，样本不足以宣称"锁问题已根治"
- 真根治仍是迁 PostgreSQL（内存/写放大问题没变）

---

## 第 3 步：下架 `comfyui_queue_status`

### 动机
探针发现该工具单次调用 **5.0–10.4 秒**，且对 Agent 无必要，是 LangBot 阻塞线程被占满的强嫌疑。

### 路径
注释掉 MCP 注册（函数体保留，便于回滚）。

### 验收

| # | 标准 | 结果 |
|---|---|---|
| 3.1 | 工具列表移除 | ✅ 15 → 14 个工具 |
| 3.2 | 生图链路不受影响 | ✅ submit（workflow=qwen）→ status → cancel 正常 |
| 3.3 | 不再出现该调用 | ✅ 重启后 0 次 |

---

## 并发/关联测试汇总

| 测试 | 结果 |
|---|---|
| 并发 3 聊天 + 2 生图 | ✅ 3 条聊天全部完成；1 张图完成交付；另 1 个 run 因测试端 WS 关闭被取消（非系统问题） |
| 锁错误 | ✅ 补丁后 0 次（7 分钟窗口） |
| 生图默认模型 | ✅ 不传 model → `workflow=qwen` |
| 异常提示 | ✅ 配置与代码路径确认（端到端样本缺失，见 1.2） |

---

## 下一步（待执行，按顺序）

| 步骤 | 内容 | 验收要点 |
|---|---|---|
| 4 | ComfyUI 热模型迁 SSD（USB junction 是冷启动 112–190s 的根源） | 迁移前后冷启动/单图耗时对比 |
| 5 | NapCat token + 端口收 127.0.0.1 | QQ 在线、收消息、LangBot 连接正常；并发 5 条消息 |
| 6 | LangBot 迁 PostgreSQL | 数据迁移校验；并发 5 条；无锁错误 |
| 7 | Kokoro 预热 + 保活；SQLite 补丁加入 watchdog 自愈 | 语音可用；容器重建后补丁仍在 |
