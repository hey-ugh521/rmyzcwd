# Möbius Console —— 本地自测工具

这些文件只服务于开发/验收，不参与生产运行。Console 本身无构建步骤，
`index.html + app.js + style.css` 直接由 Core 在 `/console` 托管。

## 快速开始（mock 后端）

```powershell
# 终端 1：mock 静态服务 + 假 /api（默认 8911）
python mobius\core\console\_mock\server.py --port 8911

# 浏览器打开
http://127.0.0.1:8911/console/
```

mock 的接口字段严格按 `mobius/docs/ARCHITECTURE.md` §4 构造，并在此基础上补充了
错误态/空态需要的场景。状态只保存在内存里，重启即恢复。

可选参数：

| 参数 | 作用 |
|---|---|
| `--latency 0.12` | 每个 `/api` 响应的人为延迟，便于观察骨架屏（默认 0.12s） |
| `--fail-api` | 所有 `/api/*` 返回 500，验证错误 toast / 内联错误框 / 重试 |
| `--users-api` | 额外提供契约未定义的 `GET /api/memory/users`（默认不提供，用于验证前端聚合降级） |

## 自动化冒烟（真实 Chrome + CDP，无 npm 依赖）

```powershell
# 终端 1：正常 mock；终端 2：错误态 mock（可选）
python mobius\core\console\_mock\server.py --port 8911
python mobius\core\console\_mock\server.py --port 8912 --fail-api

# 跑六视图 + 交互断言，输出截图与 smoke-report.json
node mobius\core\console\_mock\smoke.mjs --base http://127.0.0.1:8911 --fail-base http://127.0.0.1:8912
```

覆盖：概览统计/服务灯/媒体三档/唤醒/休眠/off 拒绝、任务列表过滤与详情抽屉/取消、
角色 LoRA 增删与 PUT 保存回读、宪法展示、候选审批、遗忘确认、审计展开与 kind 过滤、
配置保存回读、后端不可用时的可见错误，以及「无未捕获 JS 异常 / 无 console.error」断言。

## Pipe 测试

```powershell
# 自带假 Core（SSE）测试，不依赖真实 Core / Open WebUI
python mobius\core\console\_mock\test_pipe.py

# 对真实 Core 做一次端到端探测（会真实调用一次 /v1/chat）
python mobius\core\console\_mock\probe_core_pipe.py
```

`test_pipe.py` 覆盖：正常 SSE 逐段 yield、请求头/请求体、SSE `error` 事件、
HTTP 500、连接拒绝、空消息、多模态 content 数组、模型显示名。

## 真实 Core 只读集成

```powershell
# Core 已启动并托管 /console 的前提下
node mobius\core\console\_mock\integration_core.mjs --base http://127.0.0.1:8900
```

只读：不保存角色、不审批候选、不改配置、不取消任务。验证 Core 托管静态资源、
六视图对真实响应形态（counters 按状态拆分、constitution active/versions/fallback 信封）
的兼容性，输出 `core-integration-report.json` 与 `screenshots/core-*.png`。

## 产物

- `screenshots/`：mock 场景 01–16、真实 Core `core-01..06`、根路径托管回退 `17-root-mount-fallback.png`
- `smoke-report.json`：mock 冒烟逐项结果 + 异常列表
- `core-integration-report.json`：真实 Core 只读集成逐项结果
- `evidence-http.txt`：真实 Core / mock 关键 HTTP 响应抓取（状态码、Content-Type、片段）
