# P4 首启向导（L3）

> 状态：2026-11-08 完成。启动器内置 4 步向导；首次运行自动进入，之后正常 GUI。

## 1. 向导步骤

| 步骤 | 内容 | 动作 |
|---|---|---|
| 1 / 4 运行时组件 | 读取 manifest，列出必需/可选组件与安装状态 | 「安装必需组件」→ 调用下载器安装（后台线程） |
| 2 / 4 大模型配置 | Base URL / API Key / Model | 「测试连接」→ 真实 `/chat/completions`；保存到 `config.json → llm` |
| 3 / 4 模型目录 | 模型根目录（可外接盘） | 「选择目录」「从旧目录迁移」→ robocopy `/E /XJ` |
| 4 / 4 QQ 登录 | 启动 NapCat + 打开 WebUI | 「启动 NapCat 并打开 WebUI」→ Core 服务接口 |

结束：写 `launcher.first_run_done=true`，下次直接进入正常控制面板。

## 2. 命令行（可脚本化）

```powershell
MobiusCLI.exe --headless components                 # 组件清单 + 安装状态
MobiusCLI.exe --headless install-components --source-dir D:\AI\components
MobiusCLI.exe --headless test-llm                   # 读 config.llm 测试连通
MobiusCLI.exe --headless configure-llm --base-url ... --api-key ... --model ...
MobiusCLI.exe --headless migrate-models --from-dir D:\old\models --to-dir D:\new\models
Mobius.exe --wizard                                 # 强制进入向导
```

## 3. 验收证据

| 测试 | 结果 |
|---|---|
| `components`（CLI/exe） | ✅ 8 组件，required 标记正确 |
| `test-llm` | ✅ 连接成功（DeepSeek） |
| `configure-llm` | ✅ 写入并测试通过 |
| `migrate-models` | ✅ 13 个文件 robocopy 迁移（退出码 1 = 有文件复制，正常） |
| GUI 向导 | ✅ `--wizard` 进程存活（4 步窗口） |
| 正常 GUI | ✅ 进程存活（first_run_done 后不再弹向导） |
| 首次运行判定 | ✅ `launcher.first_run_done` 控制 |

修复的坑：
- 单实例互斥的 `GetLastError` 残留 → 调用前 `SetLastError(0)`
- 向导标题 Label 误用 `.set()` → 改用 `StringVar`

## 4. 已知限制

| 项 | 说明 |
|---|---|
| `install-components` 安装根 | 以应用根（`MOBIUS_ROOT`）为准，写入 `<root>/runtime/*`；本机已有外部 runtime（`D:\AI\mobius-runtime`）不受影响 |
| 模型下载 | 向导当前提供「迁移/指定目录」；模型下载清单在 P3 的 model 组件里（byo/migration 优先） |
| QQ WebUI 地址 | 固定 `http://127.0.0.1:6099/webui`，若改了 NapCat 配置需同步 |
| 交互点击 | GUI 已验证可启动与代码路径；未做自动化点击回放（人工验收项） |
