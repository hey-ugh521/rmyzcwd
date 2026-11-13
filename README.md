# Möbius

> 本机 AI 媒体助手：Core 掌握控制面，ComfyUI / TTS / QQ 协议作为可替换执行面。

当前事实：

- Core：`http://127.0.0.1:8900`
- Media Gateway：`http://127.0.0.1:8899`
- Studio：`http://127.0.0.1:8900/studio`
- Console：`http://127.0.0.1:8900/console`
- QQ：Core OneBot B1（NapCat HTTP webhook）；LangBot 仅保留为历史回退参考
- Registry：Phase 0.1 基础设施已完成，尚未接入 Agent/Gateway 运行时

## 架构

```text
Studio / Console / QQ / OpenAI-compatible clients
                     │
                     ▼
              Möbius Core (8900)
        ┌────────────┼─────────────┐
        │            │             │
   Agent Runtime  Registry      TaskStore
   角色/记忆/工具   资源/发布/权限   执行生命周期
        │
        ▼
   Media Gateway (8899)
        │
        ├── ComfyUI
        ├── Kokoro / TTS
        └── 其他 Provider
```

原则见 [ADR-011](docs/adr/ADR-011-control-execution-boundary.md)：

- Core 自主
- 接口统一
- 执行解耦
- 工具复用
- 部署渐进

## 快速开始

```powershell
# 启动（安装版；开发版用 launcher\dist\Mobius.exe）
.\Mobius.exe

# 或 CLI
.\MobiusCLI.exe --headless start

# Studio
start http://127.0.0.1:8900/studio

# 旧 Console（运维/诊断）
start http://127.0.0.1:8900/console

# 停止
.\MobiusCLI.exe --headless stop
```

## 目录

| 路径 | 说明 |
|---|---|
| `core/` | Core：HTTP、Agent、Registry、存储、服务监督 |
| `core/registry.py` | Registry 资源/Revision 生命周期 |
| `core/registry_api.py` | `/api/registry/*` |
| `media_gateway/` | MCP/REST 媒体执行面、ComfyUI 调用、任务代理 |
| `web/studio/` | React Studio（产品界面） |
| `core/console/` | 内嵌旧版控制台（运维） |
| `launcher/` | Windows 启动器（WebView2 Studio 壳 + TUI 回退） |
| `installer/` | Inno Setup 打包 |
| `configs/workflows/` | ComfyUI workflow 模板与模型注册表 |
| `data/characters/` | 角色卡 JSON |
| `docs/` | 当前文档、ADR、计划、报告、归档，入口见 [docs/README.md](docs/README.md) |
| `ops/` | 组件、发布、探针、补丁、许可证、语音评估 |
| `tests/` | 自动化验收与回归 |

## 当前状态

- Studio 已包含角色卡/系统提示词/导入导出。
- QQ 私聊、群聊 @、限流、聚合、图片/语音段已通过 B1/B2/B3 验证。
- Registry Phase 0.1 已完成资源生命周期、发布、引用、认证边界。
- Phase 1 只读索引已接入 Studio「能力库」：角色、音色、ComfyUI 工作流只读展示。
- Agent/Gateway 尚未读取 Registry；资源编辑/导入/发布仍属于 Phase 2。
- M3 干净安装仍在收口；生产安装与开发仓库分开。

## 验证

```powershell
python tests\verify.py
python tests\verify.py --quick

# Registry Phase 0.1
python tests\test_registry_lifecycle.py
python tests\test_registry_api.py
python tests\test_registry_core_auth.py
```

PostgreSQL 契约测试：

```powershell
$env:MOBIUS_TEST_PG_DSN = "postgresql://.../mobius_registry_test"
python tests\test_registry_pg_contract.py
```

## 本地运行产物

以下内容不提交，规则见 `.gitignore`：

- `config.json`, `secrets.json`
- `data/*.db`, `logs/`
- `backups/`, `release/`
- `launcher/dist/`, `launcher/build/`, `installer/dist/`
- `web/studio/{node_modules,dist}/`
- `media_gateway/{data,logs,tts_output,uploads}/`

## 历史说明

LangBot 曾承担 QQ 大脑与平台适配；当前 QQ 路径已切到 Core OneBot adapter。LangBot 的配置、cutover 记录和历史报告保留在 `docs/archive/`，不再作为默认运行依赖。
