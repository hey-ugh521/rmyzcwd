# ADR-009：安装、升级、备份、恢复、卸载与安全边界（M3）

- 状态：**Proposed（M3a/M3b 已实现；M3c 安装器可移植化+首次 bootstrap 已实现，干净机器演练待 M3d）**
- 日期：2026-11-11
- 关联：`PRODUCT-BOUNDARY.md`（PB-03/04/06）、`ARCHITECTURE-GAP.md`（G-06/G-11，M3）、`ADR-006`（网关鉴权）、`ADR-007`（任务 owner）、`ADR-008`（Provider）
- 目标：让 Möbius 从"开发者机器可运行"变成"**干净 Windows 可安装、可升级、可备份、可卸载**"；安全边界可验收。

## 1. 背景与现状审计（证据）

| 项 | 现状 | 风险 |
|---|---|---|
| 安装目录 | `DefaultDirName=D:\AI\Mobius` | 干净机器无 D:\AI；违反 G-06 |
| 运行时 | 安装器只复制源码；preflight 硬编码 `D:\Anaconda3\...`、`D:\AI\qqbot-mcp\...`、`D:\AI\pgsql-portable`、LangBot/NapCat 路径 | 干净 Windows 无 Python/PG，装完不能启动 |
| 升级 | 覆盖安装；无自动备份、无迁移 journal、无健康失败回滚 | 升级失败可能留下半迁移状态 |
| 备份 | `ops/pg_backup.py` 仍是 Docker/`D:\AI` 口径 | 当前原生 PG 不适用；无恢复演练入口 |
| 卸载 | `[UninstallDelete]` 只删 logs；无数据/密钥策略；autostart 计划任务不随卸载清理 | 残留敏感文件或残留自启 |
| 自启 | `scripts/install-autostart.ps1` 独立脚本，安装器未接线 | 用户不知道/卸载不清理 |
| 安全 | Core 回环完全免鉴权；Gateway `/api/*` token 未配置则开放；`/mcp` 按 ADR-006 开放 | 本机任意进程/浏览器 CSRF 可调管理接口（G-11） |
| 身份 | 任务/聊天 body 可自报 `user_id` | 审计/归属不可信（单用户本机风险较低，但仍是边界漏洞） |
| 版本矩阵 | 安装器无第三方组件/工作流/模型版本清单 | 无法复现/支持（G-12） |
| 干净机器 | 未演练；开发副本与安装副本历史不同源 | M3 退出条件不成立 |

## 2. 决策

### 2.1 安装布局（建议）

- **默认按用户安装**：`{localappdata}\Programs\Mobius`（无需管理员；桌面应用主入口）。
- **可变数据目录**：`{localappdata}\Mobius\data`（PG 数据、任务库、日志、模型缓存、secrets）。
- 安装目录只放程序/运行时/文档；升级替换安装目录，不动数据目录。
- 允许向导覆盖安装/数据路径；所有路径写入 `config.json`，不再依赖 `D:\AI`。
- 需要管理员的可选集成（NapCat 等）由 Integration Manager 单独请求，不拖累主安装。

### 2.2 运行时打包（建议：自包含）

- 安装器打包：便携 Python 运行时（Core/Gateway 所需依赖）+ 便携 PostgreSQL + launcher/CLI；目标"安装完成即可启动"。
- ComfyUI/模型/LangBot/NapCat 等大件走 **Integration Manager**（可选组件包），不阻塞首次文本体验；PB-05：QQ 不是首次成功必要条件。
- 备选：bootstrap 下载运行时（安装包小、但干净机器需网络且更易失败）——待 PM 决策。

### 2.3 生命周期命令

统一入口（CLI 与安装器共用）：

```text
mobius lifecycle status
mobius lifecycle backup [--out DIR]
mobius lifecycle restore --from FILE [--force]
mobius lifecycle upgrade --installer FILE
mobius lifecycle uninstall [--remove-data]
```

- 所有命令走同一 `LifecycleManager`，不复制逻辑。
- `backup`：PG 全库 dump + `config.json` + `secrets.json` + 版本 manifest；输出校验和；保留策略。
- 升级流程：停服务 → 备份 → 安装/迁移 → 启动 → 健康检查（Core /health + Gateway /api/pulse + M1 任务 API）→ 失败自动回滚到备份/旧安装器。
- `restore`：先校验 dump、恢复到临时库验证，再替换；默认拒绝覆盖运行中实例。
- `uninstall`：默认保留数据（用户可显式 `--remove-data`）；清理 autostart/开始菜单/安装目录；敏感文件只在显式 remove-data 时删除。

### 2.4 升级与回滚

- 安装器写入 `install-manifest.json`（版本、组件、schema_version、构建 hash）。
- 升级前强制备份；记录 `upgrade-journal.json`（阶段/时间/结果）。
- 回滚目标：上一个安装器 + 升级前备份；不依赖手工恢复文档。
- 不允许跨 schema 降级直接启动；降级需 `restore`。

### 2.5 安全边界（G-11）

- **默认只监听 127.0.0.1**（现状保持）。
- **本机管理 API 引入 token**：Core `/api/*` 与 `/v1/*` 中**变更类**请求需要本机 token（来自 `secrets.json`，由 launcher 注入 Console/桌面应用）；只读健康/心跳豁免。
- Gateway `/api/*`：默认生成 token 并强制（`/api/pulse` 豁免）；`/mcp` 按 ADR-006 保持开放，边界靠回环 + DNS-rebinding 防护。
- 非回环访问继续要求 Bearer API key；无 key 时拒绝启动/明确报错。
- `user_id` 不再由外部 body 决定；由本机身份/会话映射；QQ/LangBot 使用受信网关身份。
- `secrets.json` 唯一 SSOT 在数据目录，ACL 仅当前用户；删除/复制副本需审计；日志禁止打印 token。
- 安全验收：非回环 401、回环无 token 的变更请求 401、带 token 成功、`/mcp` 行为符合 ADR-006、secrets ACL、卸载数据策略。

### 2.6 版本矩阵

- Release 附 `version-matrix.json`：Möbius、Core/Gateway schema、LangBot/NapCat、ComfyUI、工作流、模型、PG。
- 安装/升级时校验组件版本范围；不满足给出可操作错误。

## 3. 被否决/不采用

| 方案 | 理由 |
|---|---|
| 继续用 `D:\AI\Mobius` | 干净机器无此盘；约束不可移植 |
| 安装器继续只复制源码 | 干净 Windows 无 Python/PG，装完不可用 |
| 卸载默认删数据 | 误删用户会话/训练资产不可接受 |
| 回环全免鉴权 | 浏览器/本机进程可调用管理接口；G-11 明确要求边界 |
| 只做文档不做干净机器演练 | M3 退出条件要求实测 |

## 4. 验收标准

- 干净 Windows Sandbox/VM：安装 → 启动 → 文本聊天 → （可选 GPU 包）生图 → 关闭/重启 → 历史仍在 → 升级 → 回滚 → 卸载（保留/删除数据各一次）。
- 备份/恢复：备份文件可恢复到 scratch DB 并通过 schema/行数校验；升级失败自动回滚演练通过。
- 安全清单实测：未授权/越权/CSRF 路径、secrets ACL、日志脱敏、卸载残留检查。
- 所有路径来自 config/向导；`grep D:\AI` 在安装产物中无运行依赖（文档可保留示例并标注）。
- 版本矩阵与安装 manifest 可读、可校验。

## 4a. M3a 实现状态（2026-11-11）

- Core：回环只读保持开放；变更类 `/api`、`/v1` 需 `Authorization: Bearer <core.api_key>` 或 `/console` 同源 HttpOnly+SameSite=Strict cookie；非回环仍必须 Bearer；非回环 `/console` 直接 401（避免 cookie 泄露 key）。
- Console：首屏下发 cookie，同源 fetch 自动携带；无需把 token 写进前端代码。
- Launcher：`http_json()` 自动附加 Core API key（GUI/CLI 共用）。
- Gateway：未单独配 `gateway.token` 时回退 `core.api_key`；Core `GatewayClient` 同源回退，保证内部 `/api/*` 调用带 token；`/api/pulse` 豁免，`/mcp` 按 ADR-006 开放。
- secrets：`harden_acl()` + `ops/secrets/harden_acl.py`（Windows icacls / 其它 0600）；写 secrets 时自动收紧。
- 测试：`tests/test_m3_security.py` **19/19**；Core/Gateway/M1/M2 回归全过。
- 身份边界：变更请求已由 token 保护，`user_id` 仅受信本机客户端可传；完整身份模型留给 M4/后续（记录残余）。

## 4b. M3b 实现状态（2026-11-11）

- `shared/lifecycle.py`：status / backup / restore --scratch / upgrade plan+journal / uninstall dry-run。
- 原生便携 PG（pg_dump/pg_restore/createdb/dropdb/psql），不再依赖 Docker；PGPASSWORD 仅传子进程。
- 备份目录含 config/secrets、各 DB dump、manifest、SHA256SUMS；目录 ACL 收紧。
- `restore --scratch` 创建临时库、pg_restore、校验表数后 drop；真实覆盖留到 M3c 停服/回滚接通。
- 实测：生产备份 → scratch 恢复 `mobius_core 10 表 / mobius 6 表 / langbot 52 表`，临时库已清理；`tests/test_m3_lifecycle.py` 19/19。
- 原 Docker 口径 `ops/pg_backup.py` 不再作为 M3 入口；M3c 删除/标注。

## 4c. M3c 实现状态（2026-11-11，部分）

- 安装器默认目录改为 `{localappdata}\Programs\Mobius`（per-user，免管理员）；升级/卸载语义待 M3d。
- 安装后 `MobiusCLI.exe --headless bootstrap`：
  1) 安装必需组件包（python-core/gateway/memory/langbot、postgres、napcat；从 Release 下载，可选本地 `--source-dir`）；
  2) 生成 secrets（PG 口令、Core API key）；
  3) `initdb` 便携 PG + 创建 `mobius_core`/`mobius`/`langbot`；
  4) 启动 PG，留给 launcher 启动 Core。
- `preflight.ps1` 改为配置驱动，无 `D:\AI`/Anaconda 硬编码；检查 launcher/core_python、gateway、pg_ctl、可选媒体组件。
- `config.example.json` 对齐 runtime 组件路径（langbot/memory）。
- 实测：便携 PG `initdb`→建库在临时端口 55433 通过；未做干净 Windows Sandbox/VM 演练（M3d）。

## 5. 待 PM 确认

1. 安装目录：`{localappdata}\Programs\Mobius`（推荐）还是 `C:\Program Files\Mobius`？
2. 运行时：自包含打包 Python+PG（推荐，安装包 ~150MB+）还是 bootstrap 下载？
3. 卸载默认：保留数据（推荐）还是询问后在删除？
4. 本机管理 API token：对变更类 `/api`/`/v1` 强制（推荐）还是维持回环免鉴权？
