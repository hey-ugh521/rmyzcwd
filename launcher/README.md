# Möbius 启动器（P5）

单文件 Windows 启动器（GUI 版 `Mobius.exe` + 命令行版 `MobiusCLI.exe`），负责按顺序拉起 Möbius：

```
Mobius.exe（需管理员权限，UAC 一次）
  └─ PostgreSQL（便携版，55432）
      └─ Core 8900（总控）
          └─ Core supervisor 拉起：Gateway / Kokoro / GSV / 记忆 / 嵌入 / PowerContext / 原生 LangBot / 原生 NapCat
```

## 构建

```powershell
cd mobius
.\launcher\build.ps1
# 产物：launcher\dist\Mobius.exe
```

构建依赖（已装）：`D:\AI\mobius-launcher`（uv venv，PyInstaller 6.22.3 + Pillow）。

## 使用

| 方式 | 说明 |
|---|---|
| 双击 `Mobius.exe`（GUI） | 弹出 UAC（NapCat 注入必需）→ 图形界面，自动检查状态 |
| `MobiusCLI.exe --headless start` | 无界面启动（PostgreSQL → Core → 服务 → NapCat） |
| `MobiusCLI.exe --headless stop` | 无界面停止（服务 → Core → PostgreSQL） |
| `MobiusCLI.exe --headless status` | 打印各服务状态 |
| `--no-elevate` | 不提权（调试用；NapCat 可能起不来） |

## 配置

`config.json → launcher`：

```json
{
  "core_python": "D:\\Anaconda3\\envs\\open-webui\\python.exe",
  "core_args": ["-m", "core.server"],
  "pg_ctl": "D:\\AI\\pgsql-portable\\pgsql\\bin\\pg_ctl.exe",
  "pg_data": "D:\\AI\\pgsql-portable\\data",
  "pg_log": "D:\\AI\\pgsql-portable\\pg.log",
  "pg_port": 55432,
  "start_napcat": true,
  "elevate": true
}
```

## 日志

- `logs/launcher.log`：启动器自己的日志
- `logs/core.launcher.log`：被拉起的 Core 的标准输出
- Core 各服务日志：`logs/<service>.log`

## 已知限制（P6 处理）

- 仍依赖外部 Python 环境（Core 用 open-webui env、LangBot 用 uv venv、NapCat 用便携包）——P6 安装包统一目录布局
- 模型（约 50GB）不打进 exe，首次运行需检测/引导
