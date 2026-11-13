# Controlled Live Hot Update: Registry + Studio

- 日期：2026-11-13
- 操作：把 Registry Phase 0/0.1/1/2A/2B 与最新 Studio dist 部署到 `D:\AI\Mobius`
- 状态：live Studio 可用；Runtime 仍未读取 Registry
- 代码提交：`0347f09`（nodes-dict workflow 支持）
- 备份：`D:\AI\backups\registry-live-20261113-114702`、`D:\AI\backups\launcher-auth-20261113-115315`、`D:\AI\backups\workflow-nodes-20261113-115505`、`D:\AI\backups\studio-dist-20261113-120114`

## 1. 部署内容

Core：

- `core/server.py`：Registry 路由、Studio 静态托管、M3a 本机认证/cookie、actor
- `core/registry.py`
- `core/registry_api.py`
- `core/registry_index.py`
- `core/registry_index_api.py`
- `core/registry_manage.py`
- `core/registry_manage_api.py`

Studio：

- 构建后的 `web/studio/dist` 已复制到 `D:\AI\Mobius\web\studio\dist`
- 包含「能力库」与「资源管理」页面

Launcher：

- 新版 `Mobius.exe` / `MobiusCLI.exe` 已覆盖
- 修复因 Core 认证升级导致的 `/api/services/start-all` 401：新 launcher 会带 Bearer API key

## 2. 启动结果

- PostgreSQL：ready
- Core：8900
- Gateway：8899
- NapCat：ready
- LangBot：已停止（保持 Core OneBot B1 单消费者）
- Studio：`GET /studio/` 200

## 3. Live 验证

- `/api/registry/index`：13 角色 / 4 音色 / 9 工作流 / 6 图像模型
- `/api/registry/character`：13 个 draft resources
- `/api/registry/voice_profile`：4 个 draft resources
- `/api/registry/workflow`：9 个 draft resources
- 未认证 `POST /api/registry/manage/import/legacy`：401
- Studio「资源管理」页面：本机已连接、可列出资源
- 点击「导入旧资源」：成功导入为 Registry draft
- 页面显示 13 个角色资源，workflow/voice 也已导入

## 4. 数据状态

- Registry 表已在生产 `mobius_core` 创建
- 导入资源均为 **draft**，`published_revision = null`
- 未发布任何资源
- Agent/Gateway/TTS/ComfyUI 仍未读取 Registry

## 5. 回滚

- Core 文件备份：`D:\AI\backups\registry-live-20261113-114702`
- Launcher 备份：`D:\AI\backups\launcher-auth-20261113-115315`
- Workflow 格式修正前模块：`D:\AI\backups\workflow-nodes-20261113-115505`
- Studio dist 备份：`D:\AI\backups\studio-dist-20261113-120114`

回滚步骤：

1. 停止 Core
2. 从对应备份恢复文件
3. 启动 Core
4. 如需要，删除/保留 Registry 表（当前仅 draft，不影响旧运行时）

## 6. 已知限制

- ComfyUI 当前 down，workflow 依赖检查会显示 unchecked
- 没有 workflow 试跑
- 没有 VoiceAsset
- Registry 未接入 Runtime
- M3 干净安装仍未收口
- 生产 DB 已有 draft 资源；这是本次验收写入，不是 Runtime 行为变化
