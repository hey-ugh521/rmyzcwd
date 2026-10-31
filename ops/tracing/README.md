# 本地追踪（Arize Phoenix）

## 是什么

把 Agent 的一轮运行拆成可看的 trace：模型请求、工具调用、耗时、父子关系。
解决"它为什么这么答 / 为什么工具没被调用"这类盲调问题。

## 启动

```powershell
docker compose -f ops/tracing/docker-compose.yml up -d
# UI: http://127.0.0.1:6006
```

只绑 127.0.0.1，局域网不可达。

## 验证链路

```powershell
D:\AI\tools\otel-venv\Scripts\python.exe ops/tracing/smoke.py
```

成功的标志（在容器内查库）：

```powershell
docker cp ops/tracing/_phoenix_check.py phoenix:/tmp/chk.py
docker exec phoenix python /tmp/chk.py
# spans / traces 应大于 0
```

## 关键参数

| 项 | 值 |
|---|---|
| OTLP HTTP 端点 | `http://127.0.0.1:6006/v1/traces`（**不是 4318**） |
| OTLP gRPC | `127.0.0.1:4317` |
| 认证 | 已关闭（仅本机绑定，不要对外） |
| 数据 | docker volume `phoenix_data` |

## 下一步：接真实服务

要给 Core / Gateway 加追踪，需要：

1. 在**实际运行它们的 Python 环境**里安装
   `opentelemetry-sdk`、`opentelemetry-exporter-otlp-proto-http`、
   `opentelemetry-instrumentation-fastapi`、`opentelemetry-instrumentation-httpx`
2. 在服务入口加一段由环境变量 `MOBIUS_OTEL=1` 守卫的初始化（默认关闭，不影响现有行为）
3. 重启服务，跑一次真实请求，在 Phoenix 里看到 `mcp.tool.*` 与 HTTP span

建议顺序：**先接 Gateway**（MCP 工具调用是主要盲区），再接 Core。
