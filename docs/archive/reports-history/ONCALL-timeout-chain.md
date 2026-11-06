# 冷启动生图超时链（2026-10-31 事故）

## 现象

开机自启后第一次让 bot 生图：ComfyUI 冷启动 → 生图成功 → **但返回路径失败**，
bot 报错、会话抱死（之后的消息全无响应）。

## 根因：一条链路上有 5 层超时

冷启动生图实测 **230~305 秒**（ComfyUI 冷启动约 110~193s + 出图 90~145s），
而下面每一层都比它短，任意一层触发都会把工具调用掐断：

| # | 位置 | 常量 | 原值 | 现值 |
|---|---|---|---|---|
| 1 | LangBot 宿主 `pkg/plugin/handler.py` | `CALL_TOOL` 的 `timeout` | 180 | **600** |
| 2 | LangBot 宿主 `pkg/agent/runner/context_builder.py` | `DEFAULT_RUNNER_TIMEOUT_SECONDS`（run 总期限） | 300 | **900** |
| 3 | 插件运行时 `langbot_plugin/api/proxies/langbot_api.py` | `call_tool` 的 `timeout` | 180 | **600** |
| 4 | 插件运行时 `runtime/io/handlers/plugin.py` | `LONG_RUNNING_OPERATION_TIMEOUT` | 180 | **600** |
| 5 | 插件运行时 `api/proxies/runner/resources.py` | `tools.call_tool` 的 `_bounded_timeout(default=...)` | 180 | **600** |
| 6 | 流水线 `ai.runner_config.*.timeout` | 插件自身的本地 wall-clock 上限 | 缺省 300 | **0**（= 交给宿主期限） |

注意第 5 层的 `_bounded_timeout` 还会做 `min(工具超时, 剩余 run 期限)`，
所以**第 2 层必须一起放开**，否则工具超时设多大都会被 run 期限夹住。

## 补丁与自愈

- 补丁脚本：`ops/patches/*.py`（幂等，可重复执行）
- 补丁写在**容器文件系统**里，容器被重建（`compose up --force-recreate`、镜像更新）会丢失
- `qqbot/watchdog.py` 每轮检查 5 处标记，缺失就自动重打并重启对应容器（`ensure_langbot_timeout_patches`）

## 验证

杀 ComfyUI 制造冷启动 → 发一次生图请求：

```
run:  completed  21:10:49 → 21:15:51（302 秒）
tool: comfyui_generate success 292.8 秒
网关: task queued 21:13:20 → succeeded 21:15:46（145.4s）
```

修复前同样场景在 **190 秒**必死（`Action call_tool call timed out`）。

## 遗留 / 更彻底的做法

超时放开只是让"长工具调用"能活下来；更稳的是**不要让一次工具调用跨过冷启动**：

1. `core.prewarm_on_start = true`：开机就把 ComfyUI 拉起来（已提供，未默认开启）
2. Agent 改用 `comfyui_task_submit` + `comfyui_task_status` 轮询：提交立即返回，单次调用都在秒级
3. 关掉 `main.py --preview-method taesd`、减少模型扫描目录，缩短冷启动本身
