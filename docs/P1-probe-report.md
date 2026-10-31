# P1 信息探针 + 默认模型切换 · 交付报告

> 授权：探针（只读旁路）+ 默认模型改千问 image。
> 未做：watchdog 重启策略（P2）、其他未授权项。

---

## 1. 信息探针

### 1.1 设计

| 项 | 内容 |
|---|---|
| 脚本 | `ops/probe/probe_state.py` |
| 数据源 | LangBot `monitoring_messages` / `agent_run` / `monitoring_tool_calls`；Gateway `task_snapshot`；watchdog 运行日志 |
| 访问方式 | **全部只读**（`mode=ro` + `busy_timeout=30s`），不写任何生产库 |
| 时间戳 | 统一本地时区 `+08:00`（ISO 字符串）+ `epoch` 双格式；与日志同源可对齐 |
| 存储 | `ops/probe/state.db`（独立库，与生产库隔离） |
| 调度 | watchdog 每 **5 分钟** 采集一次（`check_state_probe`），探针自身故障不影响主链路 |
| 节点 | `received → run_created → run_started → tool… → task_created → task_running → task_completed/failed/cancelled → run_completed/cancelled → reply_sent`；异常时附 `maintenance`（重启/延迟）节点 |

### 1.2 查询方式

```powershell
python ops/probe/probe_state.py --once            # 采集一轮
python ops/probe/probe_state.py --recent 10       # 最近 10 条消息轨迹（含阶段与时间戳）
python ops/probe/probe_state.py --tree user:<msg> # 单条消息过程树
python ops/probe/probe_state.py --tree run:<run_id>
python ops/probe/probe_state.py --tree task:<task_id>
```

### 1.3 实测：复现 02:42 那次中断（过程树）

```
[02:42:21] received        session=person_2337834160
[02:42:21] run_started     run_id=a8fc55c0-…  status=cancelled
[02:42:29] tool            comfyui_list_characters (37ms)
[02:42:29] tool            comfyui_list_models     (74ms)
[02:42:36] tool            comfyui_task_submit     (425ms)
[02:42:36] task_created    g_c3e7d5e2
[02:42:36] task_running    g_c3e7d5e2
[02:42:39 … 02:44:28]      comfyui_task_status / comfyui_queue_status 交替
[02:45:58] maintenance     重启 langbot：MCP 工具调用失败（BlockingWorkCapacityError）
[02:45:58] run_cancelled   consumer_cancelled
[02:48:46] task_completed  g_c3e7d5e2 → mobius_portrait_00061_.png
```

**一眼能看出**：run 在 02:45:58 被 watchdog 重启打断，而任务在 02:48:46 才完成 → 图生成了但没人送达。

### 1.4 附带发现（排错价值）

同一棵树里可见 agent 反复调用 `comfyui_queue_status`，单次耗时 **5.2~10.4 秒**——
这类慢调用会长期占用 LangBot 阻塞线程，很可能是 `BlockingWorkCapacityError` 的诱因之一。
（此前只看监控表是发现不了的，因为它是"成功"的调用。）

---

## 2. 默认模型切换为 qwen_image_2_1

### 2.1 改动

| 位置 | 变更 |
|---|---|
| `configs/workflows/models.json` | `qwen_image_2_1` 增加 `"default": true`（另同步 `D:\AI\comic\workflows\models.json`） |
| `media_gateway/server.py` `resolve_spec` | 无 model / 无 workflow / 无角色时，取注册表 `default` 档案 |
| `_tool_body` + 两个生图工具签名 | `workflow` 默认值从 `"portrait"` 改为 `""`（否则永远命中旧默认） |
| MCP instructions / 工具描述 | 明确：默认 qwen；flux3 最慢仅明确要求电影感时用；anime 仅二次元/角色用 |

### 2.2 验证

```
submit（不传 model）→ workflow=qwen  ✅
```

（对比：改动前返回 workflow=portrait）

---

## 3. 未做 / 待办

| 项 | 状态 |
|---|---|
| **P2 优先级调度**（把重启降级为 P2，按探针判忙闲） | 待授权（此前三个阈值问题仍待你确认） |
| 探针工具化（`mobius_message_state` 给 Agent 查） | 未做（P3） |
| watchdog 重启策略 | 未动（你之前明确未授权） |
| 探针采集频率/保留期 | 当前 5 分钟/不清理，长期需加保留策略 |
