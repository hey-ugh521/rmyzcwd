# Möbius Studio

Möbius 的本机 AI 创作与对话主界面。React 18 + Vite + TypeScript + Tailwind；无 CDN、无遥测，默认允许离线演示。

## 快速开始

```bash
pnpm install
pnpm dev          # http://127.0.0.1:5173
```

开发服务器把 `/__core`、`/__gw` 代理到本机 Core(8900) 与 Gateway(8899)。

构建与预览：

```bash
pnpm build        # tsc --noEmit + vite build，产物在 dist/
pnpm preview      # 默认 http://127.0.0.1:4173
pnpm typecheck
```

## 数据源

- **演示数据（mock）**：内置 mock 引擎，任务会推进、预览图/音频由本机程序化生成，不连接后端。
- **本机服务（live）**：连接 Core/Gateway；变更接口需要 Bearer 或同源 `/console` Cookie。
- **自动（auto）**：启动时探活 Core，不可达则回退 mock；Core 恢复后可在设置/连接弹层重新探活切回。

复制 `.env.example` 为 `.env.local` 可按需覆盖：

```bash
VITE_API_MODE=auto
VITE_CORE_BASE=          # 留空 = 开发期 /__core 代理；生产同源
VITE_GATEWAY_BASE=
VITE_CORE_TOKEN=         # 仅本地开发注入，禁止提交
```

不要把 token 写入源码、localStorage 或 URL。

## 目录

```text
src/api/          API client、live/mock backend、类型与归一化
src/app/          路由、布局、导航、命令面板、连接状态、首启引导
src/components/   设计系统与通用组件
src/features/     studio/create/tasks/gallery/chat/system/settings
src/store/        UI 偏好、作品库视图、反馈/toast
src/styles/       tokens 与全局样式
```

## 当前边界（接手时状态）

- 已导入 Qoder 基线：页面完整、构建通过、mock 流程可演示。
- 已完成 **基础真实联调**：Core/Gateway 在线、任务列表/轮询、TTS 合成、图像生成、媒体 URL 加载。
- 仍需系统性验收：自动恢复、取消/失败路径、长任务、鉴权 401、安装包内静态托管。
- 后端暂无统一任务 SSE，任务/状态使用轮询（默认运行中 3.5s、空闲 18s）。
- 暂无媒体库聚合 API，Gallery 从任务 `kind/result` 派生。
- M3 安装/首启未收口，Studio 尚未接入安装包。
- 生产 `D:\AI\Mobius` 不用于前端开发/写入。

## 已验证

- `pnpm build`：通过（129 modules）。
- `pnpm typecheck`：通过。
- 首启引导深链：直接打开 `#/settings` 不再被全屏引导遮挡；回首页仍会出现引导。
- 自动模式回退：设置页会显示“未检测到本机 Core（127.0.0.1:8900），已回退演示数据。”
- Live 联调：`/api/tasks` 62 条真实任务、任务状态轮询正常；Kokoro TTS 返回 5.3s WAV；图像生成完成并显示 832×1216 结果；Gallery/Tasks 图片 0 broken。
- 媒体 URL：Gateway `/img` 在 ComfyUI 休眠/不可达时回退本地 output 文件（路径穿越有校验）。

## 相关文档

- 外包交接：`docs/reports/FRONTEND-HANDOFF-QODER.md`
- 产品/架构：`docs/ARCHITECTURE-v1.1.md`
- 任务状态词：`docs/adr/ADR-002-*`
