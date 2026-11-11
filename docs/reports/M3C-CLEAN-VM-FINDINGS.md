# M3c 干净 Windows 首启演练发现（预验收）

- 日期：2026-11-11
- 环境：VMware Workstation，单台 **Windows Server 2022 Standard Evaluation（Desktop Experience）** 干净 VM，60GB 虚拟盘，无 Python/7z/PostgreSQL、无项目目录
- 性质：**预验收**。Server 2022 不是目标 Win10/11 客户端，正式退出仍需 Win10/11 x64 镜像
- 方式：ISO 手动安装 → VNC 远程操作 → 安装器静默安装 → `MobiusCLI --headless bootstrap --source-dir` 组件安装 → 启动/健康检查 → 卸载

## 已确认并修复的缺陷（本轮代码）

1. **安装器载荷不含 bootstrap**
   - 现象：干净机 `MobiusCLI.exe --headless bootstrap` 报 `invalid choice: 'bootstrap'`。
   - 原因：安装器使用了旧 launcher 产物；且 parser choices/路由未接线。
   - 修复：parser 增加 `bootstrap`、路由到 `cmd_bootstrap()`，重建 launcher 与安装器。

2. **`component_states.json` 读取传了文件对象**
   - 现象：bootstrap 报 `the JSON object must be str, bytes or bytearray, not TextIOWrapper`。
   - 原因：`json.loads(open(...))` 缺少 `.read()`。
   - 修复：补 `.read()`；静态回归覆盖。

3. **GBK 控制台无法输出 `✓/✗`**
   - 现象：组件下载日志输出 `'gbk' codec can't encode character '\u2717'`，bootstrap 直接失败。
   - 修复：`downloader.log()` 捕获 `UnicodeEncodeError` 并用控制台编码替换输出。

4. **干净机无 7z，随后缺 `7z.dll`**
   - 现象：`7z 解压失败: Can't load module 7z.dll`。
   - 修复：安装器随包 `ops/l3/7z.exe` + `7z.dll`；bootstrap 通过 `MOBIUS_7Z` 指向随包 exe。

5. **PostgreSQL 组件路径与归档根不一致**
   - 现象：`找不到 pg_ctl: ...\runtime\postgres/pgsql/bin/pg_ctl.exe`；实际文件在 `runtime\postgres\bin\pg_ctl.exe`。
   - 修复：`config.example.json` 的 PG exe/stop_exe/launcher.pg_ctl 改为 `${RUNTIME}/postgres/bin/pg_ctl.exe`。

6. **重复演练时旧 app/config 未清理**
   - 现象：卸载后重装仍读到旧 `config.json`，PG 路径继续错误。
   - 修复：干净演练脚本在安装前删除 app 与外部 data；这才是“干净机器”语义。

## 仍未通过（阻塞 M3 退出）

- **python-langbot 组件解压 `[WinError 5] 拒绝访问`**：从 `.mobius-extract-*` 向 `runtime/python-langbot` 移动时失败；其他组件（postgres/python-core/python-gateway/python-memory）可正常安装。
- 因为组件未齐，bootstrap 后续 PostgreSQL 初始化/启动、健康检查、重启历史、升级回滚、卸载保留/删除数据等步骤**尚未完整跑通**。
- 下一步优先：确认是长路径、残留目录锁还是 Defender 实时扫描；必要时改为可续装/原子目录替换，再做完整生命周期演练。
- 正式验收仍缺 **Windows 10/11 x64** 干净 VM；当前 Server 2022 只用于提前暴露安装器问题。

## 证据

- VM 内日志：`C:\m3test.log`、`C:\m3fix.log`（若已执行）
- 本机截图：`%TEMP%\vnc*.png`（安装、bootstrap、失败日志）
- 代码回归：`tests/test_m3c_bootstrap.py` 12/12；`tests/test_m3_lifecycle.py` 23/23；`tests/test_m3_security.py` 19/19
- 本轮未部署生产；生产仍为 M2 + 事故热修。

## M3d 续跑进展（2026-11-11 晚）

### 已修复

7. **旧组件残树导致 WinError 5**
   - 现象：`os.replace(临时目录 → runtime/python-langbot)` 报拒绝访问；目标目录是上次失败留下的 `Lib/` 残树，`shutil.rmtree(ignore_errors=True)` 未删干净。
   - 修复：`_replace_tree()` 先把旧目录改名让位，再移动新树；失败回滚；不再用 ignore_errors 掩盖。
   - 验证：新增 ZIP 提取残留目标回归，`tests/test_m3c_bootstrap.py` 17/17。

8. **干净系统缺少 MSVC 运行库 + Universal CRT**
   - 现象：`initdb` 退出码 `0xC0000135`（DLL 未找到）；干净 Server 2022 无 `vcruntime140/msvcp140/ucrtbase`，也没有 25 个 UCRT forwarder。
   - 修复：安装器携带 `vendor/msvc-runtime`（VC runtime + ucrtbase + api-ms-win-crt-*），bootstrap 在 `initdb` 前补进 PostgreSQL `bin`。
   - 验证：`initdb --version` 可在干净 VM 运行；完整 `initdb` 仍见下方阻塞。

9. **PyInstaller 动态导入缺 uuid**
   - 现象：`No module named uuid`；downloader 是动态加载，PyInstaller 未收集新增 import。
   - 修复：改用已导入的 `os.urandom` 生成后缀，避免新增模块依赖。

### 仍未通过

- 完整 `initdb` 仍以 `0xC0000135` 退出；Windows 报“找不到 libpq.dll”，但 `libpq.dll` 实际存在且 SHA256 与已知正常包一致，`libintl-9`/`libiconv-2`/`ssl`/`crypto` 也在 bin。
- Dependencies 工具解析未给出明确缺失项；下一步需用 Procmon/加载器日志定位真实失败模块，或改为系统级 VC++ Redistributable/UCRT 安装路径。
- 因此 PostgreSQL 初始化、Core 启动、健康检查、重启、升级回滚、卸载仍未完整验收。

### 当前代码/产物

- 提交前工作树：`ops/l3/downloader.py`、`launcher/mobius_launcher.py`、`installer/mobius.iss`、`vendor/msvc-runtime/`、`tests/test_m3c_bootstrap.py`。
- 本地安装器已重建；未上传、未部署生产。
