# Core / Gateway 端口收口报告

> 执行时间：2026-11-02 00:00–00:02
> 结果：**全部验收通过**

---

## 1. 变更

| 项 | 变更前 | 变更后 |
|---|---|---|
| Core 监听 | `0.0.0.0:8900` | **`127.0.0.1:8900`** |
| Gateway 监听 | `0.0.0.0:8899` | **`127.0.0.1:8899`** |
| 配置位置 | — | `config.json → core.host / gateway.host`（本地，已 gitignore） |

## 2. 关键前提验证（先验证再改）

用仅绑定 `127.0.0.1` 的临时服务做测试：

```
宿主机 → 127.0.0.1:18899              : OK
LangBot 容器 → host.docker.internal:18899 : loopback-ok  ✅
```

→ Docker Desktop 的 `host.docker.internal` 能访问宿主机 loopback，因此 LangBot（容器）访问 127.0.0.1 上的 Gateway 不受影响。

## 3. 验收

| # | 标准 | 结果 |
|---|---|---|
| 1 | Core 仅本机监听 | ✅ `127.0.0.1:8900` |
| 2 | Gateway 仅本机监听 | ✅ `127.0.0.1:8899` |
| 3 | 宿主机访问 Core/Gateway | ✅ `/health`、`/api/pulse` 正常 |
| 4 | **容器 → Gateway**（LangBot 真实路径） | ✅ `pulse: 200` |
| 5 | 容器内 MCP 握手 | ✅ 14 个工具，`comfyui_task_submit` 存在 |
| 6 | LangBot 与 NapCat WS | ✅ ESTABLISHED（/proc 计数 1） |
| 7 | watchdog | ✅ 各项检查正常 |

## 4. 当前完整端口绑定

```
8900 Core      127.0.0.1  ✅
8899 Gateway   127.0.0.1  ✅
8188 ComfyUI   127.0.0.1  ✅
5300 LangBot   127.0.0.1  ✅
5401 PluginRT  127.0.0.1  ✅
13000/13001    OneBot    127.0.0.1  ✅
6099 WebUI     127.0.0.1  ✅
```

## 5. 影响与注意

- **局域网/其他设备不能再访问 Console（8900）与 Gateway（8899）**；本机浏览器不受影响。
- LangBot 容器通过 `host.docker.internal` 访问网关，已验证可用。
- 网关重启后 LangBot 的 MCP 会话失效，watchdog 会自动重启 LangBot（本次日志可见），约 30–60 秒恢复。
