# 回归 + 边际测试报告：图片送达链路

> 时间：2026-11-03 00:16–00:21
> 结论：**两项测试全部通过**（图片桥接 12/12，网关回归 10/10），且今晚真实生图已命中桥接

## 1. 边际测试：图片 URL 桥接（容器内真实函数）

测试方式：从运行中的 `/app/src/.../respback.py` 提取真实 `_mobius_bridge_media_urls` 函数，
在 LangBot 容器内用真实 `platform_message` 类型 + 真实网关图片 URL 执行。
脚本：`ops/tests/test_image_bridge.py`

| # | 输入形态 | 期望 | 结果 |
|---|---|---|---|
| 1 | 裸 URL | 1 图 | ✓ |
| 2 | `**URL**` 加粗 | 1 图 | ✓ |
| 3 | `` `URL` `` 反引号 | 1 图 | ✓ |
| 4 | `![画](URL)` Markdown 图片 | 1 图 | ✓ |
| 5 | `[点我](URL)` Markdown 链接 | 1 图 | ✓ |
| 6 | URL + 中文句号结尾 | 1 图 | ✓ |
| 7 | 两个不同 URL | 2 图 | ✓ |
| 8 | 同一 URL 重复两次 | 1 图（去重） | ✓ |
| 9 | 非图片 URL（/api/pulse） | 0 图、保留文本 | ✓ |
| 10 | 404 图片 URL | 0 图、保留文本 + 警告 | ✓ |
| 11 | 纯文本 | 0 图 | ✓ |
| 12 | URL 夹在正文中间 | 1 图、剥离 URL | ✓ |

**RESULT: 12/12 PASS**

## 2. 回归测试：网关主链路（MCP :8899）

脚本：`ops/tests/test_gateway_regression.py`

| # | 项 | 结果 |
|---|---|---|
| 1 | 网关 pulse | ✓ |
| 2 | 提交生图（qwen） | ✓ workflow=qwen |
| 3 | 生图完成 | ✓ 62s |
| 4 | 产物 URL 可下载 | ✓ image/png |
| 5 | 取消任务 | ✓ cancelled |
| 6 | 非法模型报错 | ✓ unknown_model |
| 7 | 轮询节流 | ✓ poll_too_fast |
| 8 | 未知任务报错 | ✓ unknown_task |
| 9 | 并发两个任务 | ✓ 两个 task_id |
| 10 | TTS 可用 | ✓ kokoro |

**RESULT: 10/10 PASS**

## 3. 真实链路证据（不是模拟）

LangBot 日志今晚命中图片桥接：

```
[11-03 00:13:50.962] respback.py (147) - [INFO] : 已把 1 张图片 URL 转为图片消息
```

## 4. 测试中发现并修复的问题

| 问题 | 处理 |
|---|---|
| outbox 积压 16 条未上报 Core | 原因是当前网关实例没有 `MOBIUS_CORE_TASK_EVENT_URL` 环境变量（多实例/子进程导致）；用完整环境重启网关后 → **0 积压** |
| boot 每 2 分钟误报"watchdog 掉线" | watchdog 单实例锁端口 38999 实际不监听（socket 引用问题）；boot 脚本改用**进程存在性**判断 |

## 5. 遗留问题（不影响使用，已记录）

1. **网关仍会出现父子两个进程**：父进程（qqbot-mcp python）派生一个子进程（DSH runtime python），
   原因未查明；当前父进程持有端口、功能正常。后续在 watchdog 里加去重或找派生点。
2. **D2 会对手动网关重启升级诊断**：今晚手动重启网关触发了一次 DSH incident 报告
   （`logs/incidents/incident-20261103-001951-*.md`），属预期行为但略显噪音，可加"人工操作"窗口。
3. 重启网关会击穿 LangBot 的 MCP 会话，watchdog 会自动重启 LangBot 恢复（本次约 60s）。

## 6. 复跑方式

```powershell
# 边际：在容器里跑真实桥接函数
docker cp .\ops\tests\test_image_bridge.py langbot:/tmp/
docker exec langbot /app/.venv/bin/python /tmp/test_image_bridge.py

# 回归：网关 MCP 全链路
D:\AI\qqbot-mcp\Scripts\python.exe .\ops\tests\test_gateway_regression.py
```
