# Qoder 实施任务单 — Möbius 架构评审整改（2026-11-10）

> 来源：Qoder 架构评审（2026-11-09），经 Lead 逐条核对。
> 仓库：`C:\Users\lenovo\Desktop\_说明文档\mobius`（分支 main）
> 说明：原审查部分行号有漂移；**以本任务单给出的位置/验收为准**。

## 0. 前置约束（所有任务适用）

- 每个任务独立提交；提交前跑通该任务列出的测试。
- 禁止：`git push --force`、历史重写、删除真实数据、明文打印密钥、把 `secrets.json`/`config.json` 加入 git。
- 测试用独立临时目录/临时端口/临时数据库；不要动 `D:\AI\mobius-runtime` 生产实例。
- Python：`D:\AI\qqbot-mcp\Scripts\python.exe`（主）；`D:\Anaconda3\envs\open-webui\python.exe`（兼容性复跑）。
- 测试风格：仓库现有独立脚本式（PASS/FAIL、可直接 python 跑、无 pytest）。
- 每完成一批更新 `docs/ARCHITECTURE-QUALITY.md` 对应状态，并向 Lead 汇报证据。

## 1. 需要用户先拍板的 3 件事（Qoder 不要自行动手）

| ID | 事项 | 决定（2026-11-10） |
|---|---|---|
| D1 | 任务状态 owner（评审 #6） | **已定：B) Core 为唯一 owner，Gateway 不自存任务状态**（Gateway 仅保留执行队列/outbox 传输，不得作为状态查询源） |
| D2 | legacy 体系退役（#7） | **已定：删除 `scripts/boot-mobius.ps1`、`ops/watchdog/`，安装器不再打包**；Core Supervisor 唯一监管 |
| D3 | `config/account_profiles.json` 处置（#4，仓库公开） | **已定：保留但移出仓库（本地留存）**——从 git 跟踪中移除、保留本机副本、不改写历史、不 force push；历史中仍可查到该文件（残余风险由用户接受） |

> D1/D2 已拍板，T7/T8 可以启动；D3 按“移出仓库/本地留存”执行，禁止历史重写。

## 2. 第一批（无前置决策，优先做）

### T1 修复 Core 任务状态词汇断裂（P0-#1）

文件：
- `core/store.py`：新增 `map_gateway_status(st: str) -> str | None`（`completed→succeeded`、`cancelled/canceled→canceled`、`failed→failed`；`submission_unknown` 保留为独立状态 `submission_unknown`，**不得**映射为 failed）。
- `core/services.py` `wait_task()`：终止集合改用映射后的状态。
- `core/server.py` `_sync_tasks()`：同上。
- `core/agent.py` `_do_image()`：`status == "succeeded"` 前先映射；超时/映射失败才标 failed。

验收：
- 新增 `tests/test_task_status_mapping.py`：纯函数全映射 + 三处调用静态检查。
- mock 网关返回 `completed` 时：`wait_task` 立即返回、`agent` 标 succeeded 且把 image url 放入 `ctx["images"]`、`_sync_tasks` 更新本地任务。

### T2 修复 secrets SSOT 绕写（P0-#3）

文件：
- `ops/credentials.py`：补 `import sys`；实测 `_ssot("pg","password")` 非空。
- `core/services.py` `update_config()`：**禁止写回展开后的 CFG**。改为只更新原始配置文件中的 `core.idle_sleep_sec` / `core.prewarm_on_start` 两个键，保持 `${...}` 占位符原样、不写入任何密钥；建议在 `core/store.py` 提供 `save_raw_config(updates: dict)`（读原始 JSON → 局部更新 → 原子写）。
- `launcher/mobius_launcher.py` `configure_llm()`：`api_key` 写入 `secrets.json`（`deepseek.api_key`），`config.json` 只写 `base_url/model`；随后调用 `ops/secrets/rotate.py sync` 或等价逻辑。

验收：
- 新增 `tests/test_config_writeback.py`：临时 config（含 `${AI}` 占位符）→ 调用 update_config → 文件仍含占位符、不含明文密钥/绝对展开路径。
- `credentials._ssot` 返回真实 secrets 值；`config.json` 写入后 `git diff` 无明文。

### T3 修复 outbox 静默丢事件（P1-#5）

文件：
- `media_gateway/server.py` `_send_core_event()`：仅当 `status_code < 300` **且** 响应 JSON `ok is True` 才算投递成功；否则保留重试。
- `core/server.py` `/v1/tasks/events`：异常返回 `JSONResponse(status_code=500, {"ok": False, ...})`；非法 JSON 返回 400；正常返回 200。
- 提取纯函数 `event_delivery_ok(status: int, body: dict) -> bool` 便于测试。

验收：
- 新增 `tests/test_outbox_contract.py`：2xx+ok:false → False；500/非法 body → False；2xx+ok:true → True。
- 静态检查 Core 路由异常分支不再返回 200。

### T4 修复 PG 冷启动 500 + 幂等竞态（P1-#8）

文件：
- `media_gateway/server.py` `PgGatewayStore.__init__`：初始化 `self.active_id = None`（以及 TaskStore 基类所需属性，逐项对照 `TaskStore.__init__`）。
- `media_gateway/pg_taskstore.py` `submit()`：捕获 `psycopg.errors.UniqueViolation`（幂等键唯一索引冲突）→ rollback → 按 key 重查：payload_hash 相同返回 `(task, True)`，不同抛 `TaskConflict`；不得冒泡 500。

验收：
- 新增 `tests/test_pg_taskstore_idempotency.py`（fake cursor/连接即可）：并发同 key 场景模拟唯一冲突 → 幂等命中；异 payload → TaskConflict；`active_id` 在 `__init__` 后存在。
- 条件允许时用临时 PG 库验证：`/api/status` 在首个任务前不 500。

### T5 网关最小安全闭环（P0-#2）

文件：
- `media_gateway/server.py`：
  - 默认 `host` 改为 `127.0.0.1`（配置可覆盖）。
  - 入站鉴权：非回环请求必须带 `Authorization: Bearer ${MOBIUS_GATEWAY_TOKEN}`；回环可按 Core 同款放行；token 未配置时拒绝非回环请求。
  - `reference_image`：默认只接受 upload_id 或 `uploads/` 下文件名；**拒绝** http(s) URL 和绝对路径（除非显式开启 `MOBIUS_ALLOW_REMOTE_REFERENCE=1`）。
- `config.example.json`：gateway host 回环；补 token 配置说明（占位符，不写真实值）。
- `ops/secrets/rotate.py`：增加 gateway token（可选任务，保持 schema 兼容）。

验收：
- 新增 `tests/test_gateway_security.py`：纯函数/依赖注入测试（回环放行、非回环无 token 拒绝、有 token 通过；reference 允许 upload_id、拒绝 URL/绝对路径）。
- 启动后 `Get-NetTCPConnection -LocalPort 8899` 显示 127.0.0.1，`curl` 非回环地址无 token 401/403。

### T6 测试与契约对齐（P2）

文件：
- `media_gateway/test_gateway.py`：断言改用网关真实词汇 `completed/cancelled`；或通过新契约映射断言。
- `tests/verify.py`：同步修正 `succeeded/canceled` 断言。
- 新增 `tests/test_status_contract.py`：固定 Core↔Gateway 状态映射表，防再漂移。

验收：两套脚本可运行路径全 PASS；不再出现旧词断言。

## 3. 第二批（依赖 D1/D2/D3 决策）

### T7 任务状态单一 owner = Core（#6，D1=B）——✅ 设计完成（ADR-007 Accepted）

**设计已定稿：`docs/adr/ADR-007-task-ownership.md`（Accepted，2026-11-10）**；实现任务见 `docs/reports/QODER-M1-20261110.md`。

设计约束：
- Core 是任务状态的唯一权威：创建、状态流转、终态、查询都以 Core 为准（`core/store.py` tasks 表 + API）。
- Gateway 不得再对外提供任务状态查询（`/api/tasks` 等改为代理 Core 或移除）；不得把 `task_snapshot` 当第二事实源。
- Gateway 可保留**执行队列/outbox**用于重试与崩溃恢复，但只作为传输/执行中间态，不构成业务状态。
- 事件方向：Gateway 执行时向 Core 上报；任务身份由 Core 分配/确认（具体方案在设计文档里拍板）。
- 收敛文档：`ARCHITECTURE-v1.1.md`、`ARCHITECTURE-QUALITY.md`、`docs/STATUS.md` 同一口径；删除“双写”。
- 兼容期：旧 Gateway 任务查询接口可临时代理 Core，标 deprecated。

验收：设计文档经 Lead 确认；实现后任务状态只有一处可写；新增契约测试覆盖创建/流转/终态/查询全部走 Core。

### T8 legacy 退役（#7，D2=删）——✅ 已完成（2026-11-10）

步骤：
1. 全仓搜索 `scripts/boot-mobius.ps1`、`ops/watchdog/`、`watchdog.py`、`Write-BootVerdict` 的引用（文档/脚本/安装器/计划任务），把清单交 Lead。
2. `git rm` 删除 `scripts/boot-mobius.ps1` 与 `ops/watchdog/`；若存在仍在运行的计划任务，先停止并记录。
3. `installer/mobius.iss` 移除 `scripts\*` 打包与 legacy 相关项；确认 Core Supervisor 是唯一保活者。
4. 清理文档中的 boot/watchdog 启动说明，改为 launcher + Core Supervisor。
- 卸载项补：`secrets.json`、`config.json`、`data/`、`logs/` 的清理策略（删除前提示/保留选项）。
- 版本号统一（安装器 0.2.0 / 构建产物一致），build 闸门接 `ops/l3/secret_scan.py`。

### T9 隐私文件处置（#4，D3=移出仓库/本地留存）——✅ 已完成（2026-11-10）

步骤：
1. 把 `config/account_profiles.json` 复制到仓库外安全位置（如 `D:\AI\backups\private\account_profiles.json`），确认副本可读。
2. `git rm --cached config/account_profiles.json`（保留工作区文件或移走），在 `.gitignore` 增加 `config/account_profiles.json`。
3. 普通提交并 push，**禁止** `filter-repo`/force push/历史重写。
4. 检查仓库中是否还有其他被跟踪的 `config/*.json` 含真实账号/隐私文本，列清单给 Lead；必要时一并移出跟踪。
5. 更新 `docs/SECURITY-INCIDENT-2026-11-08.md`：记录已从 HEAD 移除、历史仍可访问、残余风险由用户接受。
6. 验收：`git ls-files config/account_profiles.json` 为空；工作区备份存在；`git log --oneline -1` 为普通提交。
- 更新 `docs/SECURITY-INCIDENT-2026-11-08.md` 增补记录。

### T10 P2 收敛
- cancel/恢复语义：实现 `cancelled_but_completed` 或明确不实现并改契约；恢复探测失败重试上限；bootstrap 失败后 outbox 循环仍启动。
- prewarm：按 ADR-005 把代码兜底与 `config.example.json` 统一为 prewarm（需用户确认示例默认值）。
- Console 会话页改读 PG/LangBot API，停止读 legacy SQLite（后续单列任务）。

## 4. 交付要求

- 每批结束给 Lead 一份：改动文件清单、测试命令与输出、未解决项、是否触碰生产配置。
- 第一批完成后由 Lead 复核并跑回归（含现有 14 个测试脚本）。
- 不要把真实密钥/QQ 号/隐私文本写进代码或提交信息。
