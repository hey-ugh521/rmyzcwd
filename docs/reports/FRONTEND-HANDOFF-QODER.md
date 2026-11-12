# Möbius Studio 前端外包交接文档（Qoder）

- 版本：v1（2026-11-12）
- 产品：Möbius，Windows 本机运行的 AI 媒体助手
- 本次范围：新做 **Studio 产品前端**；现有 Console 保留为运营/运维台，不推倒重做
- 当前仓库：`C:\Users\lenovo\Desktop\_说明文档\mobius`（交接时 `main` = `0dc6170`）
- 重要前提：**M3 安装/首启收口暂缓**。前端先做，但本阶段不要求进安装包、不改生产、不改后端领域逻辑。

---

## 1. 一句话背景

Möbius 是本机单用户应用：Core 负责会话/任务/记忆/服务编排；Media Gateway 负责图片、TTS、ComfyUI；LangBot/NapCat 是可选的 QQ 入口；Console 是当前的内嵌运营台。

这次外包的目标不是“给 Console 换皮”，而是做 **Studio**：一个面向使用者的、精美的本机 AI 创作与对话主界面。Console 继续承担服务启停、任务审计、记忆审核等运维能力。

---

## 2. 当前基线

### 2.1 已有能力

- Core：`http://127.0.0.1:8900`
  - 服务状态/启停/日志
  - 任务列表/详情/取消
  - `/v1/chat` 流式聊天（SSE）
  - OpenAI 兼容 `/v1/chat/completions`（含 stream）
  - 媒体配置、Provider、资源、模型/LoRA/音色
  - 角色、记忆、上下文、审计
- Media Gateway：`http://127.0.0.1:8899`
  - `/api/generate`（图片异步/同步）
  - `/api/tts`、`/api/upload`
  - `/api/tasks`、`/api/tasks/{id}`、取消
  - `/img`、`/audio`、`/upload/{id}`
  - Provider/资源/模型/工作流查询
- 现有 Console：`core/console/`
  - 原生 JS SPA，约 124KB JS + 39KB CSS
  - 8 个视图：概览、任务、角色、记忆、对话、服务、审计、设置
  - 无构建步骤、无 CDN、同源 `/api/*`
  - 主要靠 8s/12s/60s 轮询；只有聊天是 SSE
  - **没有图片生成表单、没有作品库、没有产品级 Gallery**

### 2.2 当前未收口

- M3 干净机安装/首启仍卡在 PostgreSQL `initdb` 加载问题；不影响前端协议开发，但影响“装完即用”的交付。
- 生产 `D:\AI\Mobius` 仍是 M2 + 事故热修，不要把它当开发/写入环境。
- M3a 的本机 Cookie/Bearer 鉴权代码在仓库中，但**生产尚未部署**。Studio 按 M3a 目标设计，不能依赖“本机免鉴权”。

---

## 3. 本次交付目标

### 3.1 必须交付

1. `web/studio/` 下的前端源码（技术栈由 Qoder 提议，默认建议 React + Vite + TypeScript）。
2. 可离线构建的静态产物（`pnpm build` / `npm run build`），不依赖 CDN、不依赖外网字体。
3. 一套适配层：API client、任务状态归一、轮询/SSE、错误映射。
4. 设计系统与页面：至少覆盖 Studio 首页、创作、任务、作品、对话、服务/健康、设置。
5. 可运行的开发说明、构建说明、环境变量样例、Mock 数据说明、验收截图。

### 3.2 明确不做

- 不改 Core / Media Gateway / Launcher / Installer / M3 lifecycle。
- 不改现有 Console 的运维行为（可参考其 API 调用方式）。
- 不接数据库，不读 `secrets.json`，不读取用户文件系统。
- 不做远程访问、账号体系、云同步、遥测、外部 CDN。
- 不在本阶段做安装包集成；安装器接入由后端/发布侧在 M3 收口后完成。

---

## 4. 用户与核心场景

单用户、本机、离线优先。核心旅程：

1. **进入 Studio**：看到 Core/Gateway/媒体资源状态，而不是先看到一堆运维表格。
2. **创作图片**：提示词 + 尺寸/模型/LoRA 等参数 → 提交 → 看到排队/运行/完成 → 查看结果 → 复用参数再生成。
3. **查看作品**：按时间浏览图片/语音结果，打开详情、下载、重试。
4. **对话**：流式文本 + 可选的图片/语音结果。
5. **了解系统**：服务健康、GPU/资源状态、任务失败原因；不在主界面暴露密钥。
6. **设置**：媒体模式、模型/Provider/音色等可读配置；不可写的显示“需在 Launcher/安装器设置”。

### 4.1 页面信息架构（建议）

- `Studio`（首页）：最近作品 + 快捷创作 + 运行中任务 + 系统状态
- `Create`：图片创作 / 语音合成（两个 tab 或子路由）
- `Gallery`：作品网格 + 筛选（kind/status/date）+ 详情抽屉
- `Chat`：会话列表 + 流式对话
- `Tasks`：运行中/历史任务 + 失败原因 + 取消
- `System`：服务、Provider、资源、日志入口
- `Settings`：媒体模式、模型/LoRA/音色、只读配置
- 全局：命令面板/搜索、连接状态、错误 toast、空状态、首启引导

---

## 5. 设计方向

- 关键词：**本地 AI 工作台、克制、精密、暗色优先**；不是通用 SaaS 后台。
- 主界面应有“创作感”：大画布/结果区、参数侧栏、任务队列、作品流。
- 中文为主；组件文案可中英混排，但状态词必须准确。
- 桌面优先：1280×800 起；兼容 1440/1920；窄屏至少不破版。
- 动效节制：加载骨架、任务进度、结果渐入；不做无意义漂浮动画。
- 可访问性：键盘可达、焦点可见、对比度达标、图片 alt、aria-live 错误提示。
- 禁止：外部 CDN、在线字体、第三方统计、把 API key 写进 localStorage/URL。

---

## 6. 技术约束与建议

### 6.1 推荐栈

- React 18 + Vite + TypeScript
- Tailwind CSS + Radix/shadcn 风格组件（可自建，不强制）
- TanStack Query 管理服务端状态；Zustand 只放少量 UI 状态
- SSE 用原生 `EventSource`/fetch stream；轮询用 Query 的 refetchInterval
- 图标用本地 SVG；字体本地打包或使用系统字体

如果 Qoder 有更合适的既有栈，先在 kickoff 提方案，不要直接引入第二个框架。

### 6.2 目录与所有权

```text
web/studio/                  # Qoder 独占
  src/
    app/                     # 路由/布局
    api/                     # client + adapters + types
    features/                # studio/create/gallery/chat/system/settings
    components/              # 设计系统
    styles/
  public/
  package.json
  vite.config.ts
  README.md
```

- Qoder 只改 `web/studio/**`（以及经批准的文档）。
- 不改 `core/`、`media_gateway/`、`launcher/`、`installer/`、`shared/`。
- 分支建议 `feat/studio`；PR 到 `main`，由 Möbius 侧 review 后合并。
- 不在仓库提交任何密钥、真实用户图片、日志中的 token。

### 6.3 本地开发

- 用 Vite proxy 把 `/api`、`/v1`、`/img`、`/audio` 代理到本机 Core/Gateway，避免 CORS。
- 开发凭证通过未入库的 `.env.local` 注入 `VITE_CORE_TOKEN`，或先打开 `/console` 使用 HttpOnly cookie。
- 若没有本机后端，使用仓库提供的 mock fixtures（Qoder 先按本文契约造 mock，Möbius 侧可补真实样本）。
- 构建产物必须可被静态服务器直接托管，不依赖 Node 运行时。

---

## 7. API 契约

> 以仓库源码为准：`core/server.py`、`media_gateway/server.py`。FastAPI 的 `/openapi.json` 可作为辅助，但鉴权中间件可能影响访问。

### 7.1 Base 与鉴权

- Core base：`http://127.0.0.1:8900`
- Gateway base：`http://127.0.0.1:8899`
- Core 回环读接口通常可匿名；**变更类 `/api`、`/v1`** 按 M3a 目标需要：
  - `Authorization: Bearer <core.api_key>`，或
  - 同源 `/console` 下发的 HttpOnly cookie。
- 前端只能同源访问；开发用 Vite proxy，生产由 Core 静态托管。
- 收到 `401`：显示“本机凭证失效/需要打开 Console 刷新会话”，不要循环重试。

### 7.2 核心读接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | Core 存活 |
| GET | `/api/status` | 总状态、服务、媒体模式、计数 |
| GET | `/api/services` | 服务列表 |
| GET | `/api/services/{name}` | 单服务 |
| GET | `/api/services/{name}/logs` | 日志（运维视图） |
| GET | `/api/services/history` | 状态历史 |
| GET | `/api/tasks?limit=&status=&source=` | 任务列表 |
| GET | `/api/tasks/{id}` | 任务详情 |
| POST | `/api/tasks/{id}/cancel` | 取消（写 cancel_requested，终态由执行端确认） |
| GET | `/api/media/providers` | 媒体 Provider |
| GET | `/api/media/resources` | GPU/资源状态 |
| GET | `/api/media/config` | 媒体配置（只读展示） |
| PUT | `/api/media/config` | 改媒体配置（需确认/权限） |
| POST | `/api/media/wake` / `sleep` | 媒体唤醒/休眠 |
| GET | `/api/models` / `/api/loras` / `/api/voices` | 模型/LoRA/音色 |
| GET | `/api/characters` / `/api/characters/{id}` | 角色 |
| GET | `/api/memory/users` / `/api/memory/user/{user_id}` | 记忆 |
| GET | `/api/context/sessions` / `.../messages` | 会话 |
| GET | `/api/audit` | 审计（运维视图） |

任务对象关键字段：

```json
{
  "id": "t_...",
  "kind": "image | speech | voice",
  "status": "pending | running | completed | failed | cancelled | submission_unknown",
  "source": "console | qq | web",
  "user_id": "...",
  "session_id": "...",
  "character": null,
  "prompt": "...",
  "params": {},
  "result": null,
  "error": null,
  "remote_id": null,
  "created_at": "...",
  "started_at": null,
  "finished_at": null,
  "cancel_requested": false,
  "attempts": 0,
  "version": 0
}
```

前端状态归一：把历史词 `queued/succeeded/canceled` 映射为 `pending/completed/cancelled`；UI 文案用“排队/运行中/成功/失败/已取消/提交未知”。

`result` 形态由 kind 决定：

- 图片：常见 `result.url`、`result.filename`，URL 由 Gateway 提供；前端做 base 解析，不要拼接未定义字段。
- 语音：常见 `result.url` / 音频路径；用 `<audio>`，不要自动播放。

### 7.3 生成与 TTS

- **生成**：`POST http://127.0.0.1:8899/api/generate`
  - 请求体包含 `prompt`、图片参数、`async`、`source/user_id/session_id` 等。
  - 返回任务标识/状态，后续用 `/api/tasks/{id}` 或 `/api/tasks` 查询。
- **TTS**：`POST http://127.0.0.1:8899/api/tts`
  - 同步返回音频结果；长文本/大文件按实际响应处理。
- **上传**：`POST http://127.0.0.1:8899/api/upload`（multipart `file`）。
- **媒体**：`GET /img?path=...`、`GET /audio?path=...`、`GET /upload/{upload_id}`。
- Core 的 `/v1/tasks` 是任务 owner 内部 API；**Studio 不直接用它创建任务**，统一走 Gateway 生成/TTS 入口。若后端后续改为 Core 代理，由 adapter 切换，不改页面。

> 注意：当前 Gateway `/api/generate` 是唯一可用的生成入口；现有 Console 没有创作表单。Qoder 的 Create 页面需要等 Möbius 侧确认最终入口签名后再做真实联调，但可以先按 mock 完成 UI。

### 7.4 聊天 SSE

`POST /v1/chat`（原生 SSE）：

```json
{"source":"web","user_id":"web:...","session_id":"session:...","text":"你好","character":null}
```

事件类型：

- `meta`：`{reply_id, source}`
- `text`：`{delta}`
- `pause`：心跳/停顿，前端忽略
- `done`：`{reply, task_ids, ...}`
- `error`：`{error, message}`

`POST /v1/chat/completions` 为 OpenAI 兼容 SSE；Studio 优先用 `/v1/chat`，少一层适配。

### 7.5 已知后端缺口（前端必须用 adapter 兜住）

| 缺口 | 影响 | 本阶段策略 |
|---|---|---|
| 任务无统一 SSE | 生图/语音只能轮询 | running 3–5s、idle 15–30s；显示“进行中”，不伪造百分比 |
| 无媒体库聚合 API | Gallery 需要从 tasks 推导 | 用任务 kind/result 派生作品；缺字段时降级为列表 |
| 无统一设置 API | 部分配置只在 Launcher/安装器 | Settings 只做只读 + 明确“需在 Launcher 修改” |
| 生成入口在 Gateway | 与 Core 状态不在同一 base | API adapter 统一封装，不散落 base URL |
| M3 未部署鉴权 | 生产 M2 可能免鉴权 | 按 401 流程开发，不依赖免鉴权 |
| 任务快照清理/语音 fail-open 边界 | 失败态可能不完美 | UI 明确失败原因和重试入口，不假装成功 |

---

## 8. 安全与数据边界

- 不读取/提交 `secrets.json`、API key、数据库密码。
- 不上传用户图片/音频到任何外部服务；所有媒体只在本机显示。
- 不引入 analytics、sentry、CDN、远程字体、远程图标。
- 变更操作（启停服务、取消任务、改媒体配置、删除）必须有二次确认。
- 错误提示可以带后端 `error` 码，但不能把完整 token/路径/密钥打到 UI 或日志。
- 开发数据用 mock/脱敏样本；不要提交真实 QQ 号、用户记忆、聊天内容。

---

## 9. 里程碑与验收

### M0（2–3 天）方向确认

- 技术栈、信息架构、视觉方向各一页
- 设计 token（色彩/字体/间距/圆角/阴影/状态色）
- Mock 数据与页面骨架
- **验收：Möbius 侧确认后才进入 M1**

### M1（约 1 周）可演示壳

- 全局布局、导航、连接状态、错误/空状态
- Studio 首页：最近任务/作品、系统状态、快捷入口
- 服务/健康页
- **验收：本地 `pnpm build` 成功；无 CDN；1280×800 截图**

### M2（约 1–2 周）创作与任务

- Create：图片参数面板、提交、排队/运行/完成/失败、取消
- Tasks：列表/详情/重试
- Gallery 雏形（从任务派生）
- **验收：真实 Gateway 联调或完整 mock 流程；错误路径可复现**

### M3（约 1 周）对话与打磨

- Chat SSE、会话切换、停止生成
- 动效、可访问性、键盘、窄屏
- 性能：首屏、图片懒加载、长列表虚拟化
- **验收：Lighthouse/人工走查；无 console error；关键路径 e2e**

### 最终验收标准

- `pnpm install && pnpm build` 在干净 Node 环境可重复通过。
- 产物静态托管可运行；无外部网络依赖（除本机 API）。
- 所有页面有 loading/empty/error/retry。
- 任务状态严格按状态词显示，不出现“假进度”。
- 取消/启停/删除均有确认和结果反馈。
- 源码无密钥、无真实用户数据。
- 交付 README、构建说明、API adapter 说明、截图、已知限制。

---

## 10. 协作方式

- Qoder 负责 `web/studio/**`；Möbius 侧负责 API/契约/后端联调环境。
- 每个里程碑一个 PR；PR 描述包含：范围、截图/GIF、如何运行、未覆盖项。
- 每周一次 30 分钟 demo；阻塞问题当天在 issue 里写清楚，不靠口头。
- 需要新接口时先提交“接口需求单”（路径、请求/响应、状态、错误码），不要前端自己造 mock 后当成契约。
- 不直接 push `main`；不碰生产 `D:\AI\Mobius`；不在用户机器上做写操作。

---

## 11. 待确认问题（kickoff 必须回答）

1. Studio 的视觉方向：暗色工作台 / 明亮创作工具 / 双主题？
2. 首屏主入口：先创作，还是先作品/对话？
3. 生成入口是否确定为 Gateway `/api/generate`、`/api/tts`，还是由 Core 增加 `/api/generate` 代理？
4. 是否本期做语音克隆/音色管理，还是只做 TTS 播放？
5. Gallery 是否接受“从 tasks 派生”的 MVP，还是等媒体库 API？
6. 前端技术栈是否批准 React + Vite + TS？
7. Qoder 是否需要访问本机 API，还是只基于 mock 开发、由 Möbius 侧做联调？
8. 交付是否需要 Playwright/e2e，还是截图 + 手工验收即可？

---

## 12. 附：现有 Console 可参考的点

- `core/console/app.js`：API 调用、任务状态归一、媒体 URL 处理、错误 toast、轮询节奏。
- `core/console/style.css`：现有暗色 token 和布局，可作为“不要做成什么样”的反面/正面参考。
- 现有 Console 不覆盖：创作表单、Gallery、产品级空状态、首启引导、任务实时进度。

---

**交接原则：前端先做，M3 后接；不碰生产、不碰安装器、不碰后端领域逻辑。**
