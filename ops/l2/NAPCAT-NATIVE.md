# NapCat 原生化（P3）操作记录

> 状态：2026-11-08 功能验证通过（原生 NapCat 4.18.42 登录并转发消息到原生 LangBot）。

## 安装

| 项 | 值 |
|---|---|
| 安装目录 | `D:\AI\napcat-native\NapCat.52230.Shell` |
| QQ | 9.9.33-52230（OneKey 便携安装，`versions\9.9.33-52230`） |
| NapCat | 4.18.42（注入在 `versions\9.9.33-52230\resources\app\napcat`） |
| 安装器 | `D:\AI\napcat-native\NapCatInstaller.exe`（自动下载 QQ 300MB + NapCat 34MB） |
| 配置 | 从容器 `qqbot\napcat\napcat\config` 复制：HTTP 3000（token `LMd1…`）、WS 3001（token `<REDACTED>`）、WebUI 6099 |
| 登录 | QQ 3947356403「苍玄之书」，**自动快速登录成功**（复用已安装 QQ 的登录态） |

## 关键点

1. **必须管理员权限**：非提权启动时 worker 反复崩溃（`error code v2:-8`），
   `launcher.bat` 会检测 `net session` 并要求提权。
2. 启动方式：`launcher.bat`（提权）→ `NapCatWinBootMain.exe` 注入 QQ → NapCat 在 3000/3001/6099 监听。
3. LangBot 的 OneBot 适配器 `ws_url` 指向 `ws://127.0.0.1:3001`（原生直连，不经过容器端口映射）。
4. 容器 NapCat 已停止，Docker 回退保留。

## Core 登记

`config.json → services.napcat`：`enabled=true`、`autostart=false`（因为启动需要 UAC 提权），
`exe` 指向 `launcher.bat`，`ports=[3000,3001]`。

→ P5 启动器需要用"以管理员身份运行"或计划任务（最高权限）来拉起它。

## 验收证据（2026-11-08 00:31）

- `get_login_info`：`苍玄之书 (3947356403)`，`get_status.online=true`
- `get_version_info`：`NapCat.Onebot v11 / 4.18.42`
- LangBot OneBot 连接：`127.0.0.1:xxxxx -> 127.0.0.1:3001 Established`
- `monitoring_messages` 在切换后继续新增群消息（用户 16:31Z / 00:31+08）

## 遗留

- 原生 NapCat 进程由提权启动，Core 非提权进程无法直接拉起 → P5 解决
- 容器 `napcat` 已停但镜像/配置保留作回退
