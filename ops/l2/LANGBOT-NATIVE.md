# LangBot 原生化（P2）操作记录

> 目标：Windows 上不依赖 Docker 运行 LangBot 4.11.2，由 Core 托管。
> 状态：2026-11-07 功能验证通过（私聊消息进→回复出）。

## 环境

| 项 | 值 |
|---|---|
| 源码目录 | `D:\AI\langbot-native`（从镜像 `/app` 提取，54308 文件，1.4GB） |
| 运行方式 | `uv run --no-sync main.py`（uv 0.12.5，Python 3.12） |
| venv | `D:\AI\langbot-native\.venv`（`uv sync --frozen --no-dev --python 3.12`） |
| 数据目录 | `D:\AI\langbot-native\data`（内含 config.yaml + 插件/日志） |
| 数据库 | 容器 PG `127.0.0.1:55432/langbot`（P4 换便携 PG） |
| 端口 | 5300（WebUI/API），OneBot 客户端连 `ws://127.0.0.1:13001`（NapCat 容器 3001 映射） |

## 关键步骤

1. `docker cp langbot:/app D:\AI\langbot-native`
2. 删除镜像带来的 Linux `.venv`，`uv sync --frozen --no-dev --python 3.12`
   - 若报 `Failed to update Windows PE resources`（杀软拦截），去掉 dev 依赖即可
3. `data/config.yaml`：`database.use=postgresql`，`postgresql.host=127.0.0.1`、`port=55432`
4. DB 内 `host.docker.internal` → `127.0.0.1`：
   - `mcp_servers.extra_args.url`（comfyui/memory）
   - `plugin_settings.config`（PowerContext 的 server_url）
5. `bots.adapter_config.ws_url`：`ws://napcat:3001` → `ws://127.0.0.1:13001`
6. 禁用未配置的「QQ 官方 API」bot（否则启动时适配器构造报错）
7. 重启后清理旧插件运行时孤儿进程（5400/5401 端口 401 的根因）

## Core 托管

`config.json → services.langbot`：

```json
{
  "enabled": true, "autostart": true,
  "exe": "C:\\Users\\lenovo\\.local\\bin\\uv.exe",
  "cwd": "D:\\AI\\langbot-native",
  "args": ["run", "--no-sync", "main.py"],
  "port": 5300, "health_url": "http://127.0.0.1:5300/healthz",
  "start_timeout_sec": 240,
  "kill_pattern": "langbot_plugin\\.cli|langbot-native\\.venv|langbot-native.*main\\.py"
}
```

## 已知问题

1. **插件运行时 401**：LangBot 重启时旧 runtime 未退出，新 runtime 端口被占，连旧 runtime 鉴权失败。
   - 处理：Core 停服务时按 `kill_pattern` 清理 `langbot_plugin.cli` 进程
2. **LangRAG 依赖装不上**（`dependency_prepare_failed`）：已禁用该插件
3. **人格漂移**：复用旧 PG 库时，历史上下文/记忆会让回复混入旧人设（"主人/喵"）。
   - 处理：P3 后按"全新配置"重建空库（`langbot_native`）+ 重建流水线绑定
4. **沙箱工具不可用**（`Native sandbox tools NOT available`）：Box runtime 关闭，属预期，不影响消息/生图/语音

## 验收证据（2026-11-07 23:46）

- LangBot 日志：`Processing request from person_2337834160 (0): 迷迭香，在吗` → `Response: assistant: 在的...`
- `monitoring_messages`：user 15:46:27 → assistant 15:46:58
- OneBot WS：`127.0.0.1:58823 -> 127.0.0.1:13001 Established`
