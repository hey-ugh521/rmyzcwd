# 安装包构建（P6）

> 工具：Inno Setup 6.7.4（per-user 安装于 `D:\AI\InnoSetup`，无需管理员即可编译）

## 构建

```powershell
cd mobius
.\installer\build.ps1
# 或
D:\AI\InnoSetup\ISCC.exe installer\mobius.iss
```

产物：`installer\dist\MobiusSetup-0.1.0.exe`（约 27MB）

## 安装

```powershell
# 图形安装
installer\dist\MobiusSetup-0.1.0.exe

# 静默安装（默认目录 D:\AI\Mobius）
installer\dist\MobiusSetup-0.1.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

安装内容：
- `Mobius.exe`（GUI 启动器）、`MobiusCLI.exe`（命令行）
- `core/`、`media_gateway/`、`ops/`、`scripts/`、`data/characters/`、`config.json`
- `preflight.ps1`（环境检查）
- 开始菜单：启动器 / 控制台 / 环境检查 / 卸载；可选桌面快捷方式

**不包含**（体积原因，需机器上已有）：Python 环境、uv/LangBot 源码、便携 PostgreSQL、原生 NapCat、ComfyUI/GPT-SoVITS 与模型（约 50GB）。
安装后运行「环境检查」会逐项列出缺失项。

## 卸载

开始菜单「卸载 Möbius」或设置 → 应用；卸载会清理 `{app}\logs`，不删除本地模型/数据目录。

## 已知限制

- 启动器运行需要管理员权限（NapCat 注入 QQ）
- 不自动添加开机自启（避免与旧 `Mobius Boot` 计划任务冲突）；需要时把启动器快捷方式放进启动目录
- 依赖仍是本机绝对路径（`D:\AI\...`），换机器需重新放置依赖并在 `config.json → launcher` 中调整
