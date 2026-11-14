# G-01 / M3：生产 `store.py` 与仓库收敛方案

- 日期：2026-11-14
- 结论状态：**方案评审稿（未部署、未改生产、未重启服务、未开 Shadow、未改配置）**
- 分析方式：只读代码/哈希/进程检查；补丁可逆性在临时目录验证；未运行测试、未执行安装器
- 关联文档：
  - [4F-2C `store.py` 部署策略](2026-11-14-phase4f2c-store-deploy-strategy.md)
  - [4F-2C 最小补丁](../patches/2026-11-14-phase4f2c-store-minimal.patch)
  - [4F-2C 部署授权](../../reports/PHASE4F2C-DEPLOY-AUTHORIZATION.md)
  - [4F-2C 实施报告](../../reports/PHASE4F2C-IDENTITY-API-REPORT.md)
  - [4F-2C 后续问题](2026-11-14-phase4f2c-followups.md)
  - [强制 worker 回收方案（未实现）](2026-11-14-worker-recovery-plan.md)
  - [ARCHITECTURE-GAP：G-01 / G-10](../../ARCHITECTURE-GAP.md)
  - [M3 实施计划](../../reports/M3-PLAN-20261111.md)
  - [M3c 干净 Windows 首启演练发现](../../reports/M3C-CLEAN-VM-FINDINGS.md)
  - [ADR-009 生命周期与安全](../../adr/ADR-009-lifecycle-security.md)

> 本文只讨论 `core/store.py` 一个文件的收敛，不代表 G-01 关闭，也不改变 4F-1/2A/2B
> 冻结内容、Shadow 开关或生产配置。

## 1. 决策摘要

1. **事实**：生产 `D:\AI\Mobius\core\store.py` 是 4F-2C 的 **PG timeout 最小补丁版**
   （SHA256 `3A20A70B…`）；仓库 HEAD `f1f57c5` 的 `core/store.py` 是**完整版**
   （SHA256 `CD0842C0…`）。两者差异为 **+171 / −19** 行（4 个 diff hunk）。
2. **差异性质**：
   - **SQLite WAL / 连接生命周期**：生产版完全没有 `_conn_lock`、进程 anchor、
     `_ClosingConnection`、跨进程初始化文件锁；仓库版有。这是本次真正需要收敛的
     功能差异。
   - **PG timeout**：两版**功能等价**，只差注释与导入顺序；默认值均为
     `connect_timeout=5s`、`statement_timeout_ms=0`，生产配置未覆盖这两项。
   - **其他**：仅导入顺序、空行、注释/文档字符串变化，无语义影响。
3. **推荐**：本次动作走 **路径 B：现有生产升级（单文件替换，受控维护窗口）**；
   **路径 A：干净安装 M3 是 G-01/M3 关闭的必经门禁**，必须在 M3d 阻塞解除后
   单独执行。B **不关闭** G-01，也不产生“干净安装=当前代码”的证据。
4. 本方案不执行任何写操作。需要单独授权后才可进入 §6 的部署步骤。

## 2. 目标、范围与非目标

### 2.1 目标

- 让生产 `store.py` 与仓库 HEAD 的 `core/store.py` 同源，消除 4F-2C 期间产生的
  单文件 drift。
- 给出可独立复核的差异分类、哈希证据、备份/迁移/回滚步骤与测试矩阵。
- 明确本动作与 G-01、M3、4F-2C followups、worker 回收方案之间的关系和先后顺序。

### 2.2 范围

- 只读检查仓库与生产；只新增两份文档
  （本文件与 [复核报告](../../reports/G01-M3-STORE-CONVERGENCE-REVIEW.md)）。
- 不修改 `core/`、`tests/`、配置、已有文档；不部署、不重启、不执行安装器、不启 Shadow。

### 2.3 非目标

- 不解决 G-01 的其余 drift（例如生产 `core/secrets.py` 与仓库不同、生产
  `shared/lifecycle.py` 缺失、安装器/启动器产物落后 HEAD）。
- 不处理 4F-2C followups 的强制 worker 回收、外部指标、生产长稳。
- 不设计 G-10 测试分层或 M5 发布流程。

## 3. 事实基线（2026-11-14 只读）

### 3.1 仓库与生产

| 项 | 值 |
|---|---|
| 仓库路径 | `C:\Users\lenovo\Desktop\_说明文档\mobius` |
| HEAD | `f1f57c5`（`docs: correct CI evidence stats logging and diff counts`，仅文档改动） |
| `store.py` 最后一次代码提交 | `e646e4b`（2026-11-14，`fix: harden PG timeout config parsing`） |
| 生产路径 | `D:\AI\Mobius` |
| 生产 `db_backend` | `pg` |
| 生产配置 `db_connect_timeout` / `db_statement_timeout_ms` | 两键均**不存在**，使用代码默认值 5s / 0 |
| 生产配置 SHA256 | `80F934C4933E0557370587839F48877181B6D10475F0E1919AC450001590E519`（未修改，未打印内容） |
| 生产 Core | PID `29784`（`python -m core.server`），启动于 2026-11-14 15:56:07；`127.0.0.1:8900` 处于监听 |
| 生产 Core 解释器 | 父进程 `D:\AI\mobius-runtime\python-core\Scripts\python.exe`（venv），实际子进程为 uv 管理的 CPython 3.12 |

### 3.2 文件清单与 SHA256

| 对象 | 路径 | 字节 | 行数 | SHA256 |
|---|---|---|---|---|
| 仓库完整版 | `<repo>\core\store.py`（工作树 HEAD 内容；CRLF 字节） | 50186 | 1302 | `CD0842C062B9AAF94F5CB1D36DCAB617C7E1D65F473E35FC0969F59EF557393D` |
| 生产当前版 | `D:\AI\Mobius\core\store.py` | 45374 | 1150 | `3A20A70BFA52A4864C3A08E0796829DC0BA0E79E0F6B8D29D9CD5041EAF403C1` |
| 生产部署前备份 | `D:\AI\Mobius\core\store.py.bak-4f2c-20261114-155544` | 43360 | — | `EA5EF74FE23D8C04476320354CF30142B06E6366225C550D362EA9DA15A26B93` |
| 4F-2C 最小补丁 | `<repo>\docs\aegis\patches\2026-11-14-phase4f2c-store-minimal.patch` | — | 91 | `D27C9A12F47FA99C02734E654E20FEDD94BAB4945AB8DADBDEC1C319EAC31E2B` |

Git 归一化 blob 证据（与补丁头 `index 50c6f02..ba1d733` 完全吻合）：

| 对象 | blob |
|---|---|
| 生产旧版（备份文件） | `50c6f02fe1919775a677e4d1cc8f6b66c08f1ca4` |
| 生产当前版 | `ba1d7330e4513c4958d051fbca4c163ab7718f06` |
| 仓库 HEAD `core/store.py` | `04d17f2d1bf99804cc0aa7fc7cbcf777f765d5f6` |

### 3.3 只读检查结果

- `git diff --no-index`：生产当前版 → 仓库 HEAD，**4 个 hunk，+171/−19**。
- 补丁可逆性（在 `%TEMP%` 副本上，未写生产）：
  - 对生产当前版执行 `git apply -R` 后，SHA256 精确变为 `EA5EF74F…`（= 备份文件）；
  - 对备份文件执行正向后，SHA256 精确变为 `3A20A70B…`（= 生产当前版）。
  - 结论：**生产当前版 = 生产旧版 + 4F-2C 最小补丁**，无未记录的第三方改动。
- 生产只读导入（`-B`，禁止写 pyc；仅 import，不连数据库、不调用业务接口）：
  - `core.store.__file__` = `D:\AI\Mobius\core\store.py`，`DB_BACKEND=pg`；
  - SHA256 = `3A20A70B…`，`hasattr(pg_timeouts)=True`，
    `hasattr(close_anchor)=False`，`hasattr(_conn_lock)=False`；
  - 默认值 `PG_CONNECT_TIMEOUT=5`、`PG_STATEMENT_TIMEOUT_MS=0`；
  - 导入前后生产 `store.py` 与 pyc 的 mtime/哈希未变化。
- 已同源抽查（生产 vs 仓库 HEAD）：

| 文件 | 结果 |
|---|---|
| `core/identity_api.py` | 相同（`20620DA8E05A…`） |
| `core/server.py` | 相同（`7F77BB2B77BE…`） |
| `core/paths.py` | 相同（`57A2D9817FB8…`） |
| `shared/paths.py` | 相同（`A446CD530173…`） |
| `core/secrets.py` | **不同**（生产 `DACCF897…`，仓库 `151135AB…`；不属本方案范围） |
| `shared/lifecycle.py` | 生产**缺失**（M3b 产物未进入此安装副本） |
| `launcher` 源文件 | 生产为 exe，无 `.py`；生产 `Mobius.exe/MobiusCLI.exe` 与仓库 `launcher\dist` 同哈希（2026-11-12 构建） |

- 生产 `core/__pycache__\store.cpython-312.pyc` 为陈旧缓存：头部记录源大小
  43319（对应 2026-11-10 的旧 `store.py`），mtime 2026-11-10；`-B` 导入已证明
  运行期按源文件重新编译。收尾时应删除该 pyc，避免审计歧义。
- 现有构建产物落后 HEAD：`installer\dist\MobiusSetup-0.2.0.exe` 构建于
  2026-11-11 18:55；`launcher\dist\Mobius.exe/MobiusCLI.exe` 构建于
  2026-11-12 21:43。**不能直接用这些产物做“干净安装=当前代码”的验收。**

## 4. 差异分类（精确）

### 4.1 方法

- 以 `git diff --no-index -U8` 对比生产文件与仓库 HEAD 文件，并逐 hunk 归类；
- 用补丁 blob 头与备份/当前文件的 `git hash-object` 交叉验证补丁确实是从生产
  旧版生成、且生产当前版就是补丁结果；
- 分类只回答“是否改变行为”，不把注释/顺序变化算作功能差异。

### 4.2 分类总表

| 类别 | 生产最小补丁版 | 仓库完整版 | 功能差异 | 影响后端 |
|---|---|---|---|---|
| **SQLite WAL / 生命周期** | `conn()` 每次连接执行 `PRAGMA journal_mode=WAL`；`timeout=15`；无进程 anchor、无 `_conn_lock`、无跨进程初始化锁；`with conn()` 只提交不关闭 | 新增 `_conn_lock`、`_anchor/_anchor_key`、`_ClosingConnection`、`_open_sqlite_connection`、`_db_process_lock`、`_ensure_anchor`、`close_anchor`、`atexit`；WAL 只在初始化/anchor 建立时设置一次；`timeout=30`、`busy_timeout=30000`；`with conn()` 退出即关闭 | **有**（仅 SQLite） | SQLite |
| **PG timeout** | `_cfg_int`、`PG_CONNECT_TIMEOUT=5`、`PG_STATEMENT_TIMEOUT_MS=0`、`pg_timeouts` 线程局部覆盖、`_PgConnection` 传 `connect_timeout` / `options=statement_timeout` 均已具备 | 同左，仅多 2 处注释与 `pg_timeouts` docstring | **无** | PG |
| **其他** | 无 `import atexit`；`pathlib` 在 `contextlib` 前 | 增加 `import atexit`；`contextlib` 在 `pathlib` 前；新增注释/空行 | **无** | 全部 |
| **统计** | 1150 行 / 45374 字节 | 1302 行 / 50186 字节 | +171/−19（4 hunks） | — |

### 4.3 SQLite 侧逐项差异（仓库行为）

以仓库 `core/store.py` 行号为准，生产对应行号见括号：

1. **连接创建串行化**：`_conn_lock`（仓库 `:86`；生产无）包住
   `_open_sqlite_connection()`，消除“最后一个连接关闭 → 并发首次打开重建
   `-shm`”窗口（`conn()` 仓库 `:304-310`；生产 `:169-178`）。
2. **进程级 anchor**：`_anchor/_anchor_key`（仓库 `:89-90`）、`_ensure_anchor()`
   （`:266-286`）在 `init_db()`/首次 SQLite `conn()` 时保持一个连接，
   防止 `-wal/-shm` 被最后一个普通连接关闭时清理。
3. **`_ClosingConnection`**（仓库 `:182-196`）：`with conn()` 退出时显式
   `close()`，修复 sqlite3 默认上下文管理器“只 commit/rollback、不 close”
   导致的连接/文件锁泄漏；同时 `conn()` 使用 `timeout=30` 和
   `PRAGMA busy_timeout=30000`（仓库 `:199-206`；生产 `:173-177`）。
4. **跨进程初始化锁**：`_db_process_lock()`（仓库 `:209-263`）对
   `DB_PATH + ".init.lock"` 加文件锁（Windows `msvcrt.locking` / POSIX
   `fcntl.flock`，等待上限 30s），使多进程同时初始化 WAL 库串行；PG 后端直接
   no-op。
5. **初始化路径重构**：仓库 `init_db()`（`:421-456`）拆成 PG 分支与 SQLite
   分支；SQLite 分支在 `_lock` + `_db_process_lock` 内设置 WAL、建表、迁移并
   保留 anchor。生产 `init_db()`（`:290-304`）是旧的一体化路径。
6. **进程退出**：仓库 `atexit.register(close_anchor)`（`:301`）；生产无。

这些差异对当前生产（`db_backend=pg`）**不执行**，但模块导入时会定义
`_ClosingConnection`（sqlite3.Connection 子类）与 `atexit` 注册；不影响 PG
连接路径。

### 4.4 PG timeout 等价性逐项核对

| 检查点 | 生产行号 | 仓库行号 | 结论 |
|---|---|---|---|
| `_cfg_int` 解析（环境变量 → 配置 → 默认；拒绝负数/小数/inf/非法值） | `:51-68` | `:52-69` | 逻辑相同 |
| `PG_CONNECT_TIMEOUT` 默认 5s | `:71` | `:73` | 相同 |
| `PG_STATEMENT_TIMEOUT_MS` 默认 0 | `:72-73` | `:75-76` | 相同 |
| `pg_timeouts` 线程局部覆盖/恢复 | `:83-102` | `:95-115` | 逻辑相同（仓库多 docstring） |
| `_PgConnection` 传递 `connect_timeout` / `options=-c statement_timeout=…` | `:122-130` | `:135-143` | 逻辑相同 |
| 生产配置是否覆盖 | 未设置 | — | 均使用默认 5s / 0 |

因此，“PG timeout 差异”实际上是 0 功能差异；4F-2C followups 中未关闭的
**强制 worker 回收、外部指标、长稳**是独立问题，本收敛不改变它们。

### 4.5 为什么不能把“最小补丁版”当作仓库完整版

1. **最小补丁是临时战术**：[4F-2C 部署策略](2026-11-14-phase4f2c-store-deploy-strategy.md)
   §2/§6 明确“只补 PG timeout、SQLite WAL 修复走 G-01/M3”，不是收敛决定。
2. **丢功能**：SQLite 侧 4 个已合并修复（提交 `0384e18`、`3c19e26`、`91e7891`）
   与回归测试会脱离生产代码；`tests/test_store_concurrency.py` 明确依赖
   `store.close_anchor()` 和 `_conn_lock` 串行化断言（`peak == 1`），
   `probe_store_multiprocess.py` 也调用 `close_anchor()`。最小补丁版没有这些
   符号，**无法用同一套测试验证生产副本**。
3. **双源维护**：若把最小补丁版当“完整版”，要么从仓库回退已合并的修复与回归测试
   （不可接受），
   要么维护两个 `store.py` 分支；后续任何 `store.py` 变更（包括尚未批准的
   worker 回收方案 B）都要双份移植，drift 会扩大。
4. **G-01 定义不允许**：G-01 的退出条件是“安装器产物与仓库 main 同源；干净机器
   安装=当前代码”。保留最小补丁版即安装副本 ≠ main，G-01 无法关闭。
5. **回退风险**：如果未来 `db_backend` 切回 sqlite（配置/回滚/测试场景），
   生产副本仍带已知 WAL `readonly database` 竞态，而仓库已修复。

## 5. 两条路径比较

### 5.1 路径 A：干净安装 M3

**做法**：从仓库 HEAD 重建 launcher 与安装器 → 在干净 Win10/11 x64 VM / Windows
Sandbox 上安装 → bootstrap 组件与 PG → 配置/首启 → 运行测试矩阵 → 若替换现有
生产则迁移 PG 数据、config、secrets、路径。

**必须满足的前置**：

- 解决 [M3c 发现](../../reports/M3C-CLEAN-VM-FINDINGS.md) 记录的 PG `initdb`
  `0xC0000135`（libpq/运行库加载）阻塞；当前文档显示未闭环。
- 具备 Win10/11 x64 干净 VM 证据（Server 2022 只是预验收，不是目标环境）。
- 重建 `launcher\dist\Mobius.exe/MobiusCLI.exe`（当前 2026-11-12）与
  `installer\dist\MobiusSetup-*.exe`（当前 2026-11-11），否则安装器仍装旧代码。
- 若替换现有生产：准备数据迁移/停机/回滚方案（lifecycle `restore --force`
  尚未开放），并接受更长停机与更大 blast radius。
- 安装器默认目录仍是 `D:\AI\Mobius`（G-06 未收口）；干净机无 `D:\AI` 时需
  PM/交付决策。

**产出**：唯一同时覆盖“安装器同源 + 干净机器 = 当前代码”的 G-01/M3 证据。

### 5.2 路径 B：现有生产升级（单文件）

**做法**：在受控维护窗口内，对现有 `D:\AI\Mobius` 做：备份 → 停服 → 用仓库
HEAD 的 `core/store.py` 替换生产文件 → 清旧 pyc → 启动 → 验证 → 记录；失败按
§7 回滚。

**特点**：

- 只改 **1 个文件**；不执行安装器，不改配置/secrets/数据库，不做数据迁移。
- PG 路径静态等价（§4.4），SQLite 新增代码在 PG 下休眠；风险面主要是
  import 级副作用与重启窗口。
- 回滚 = 恢复 1 个 `.bak` 文件 + 重启；identity_api 无需回滚（其依赖的
  `pg_timeouts` 在两版都存在）。
- **不产生**安装器/干净机器证据，**不关闭** G-01。

### 5.3 对比

| 维度 | A：干净安装 M3 | B：现有生产升级 |
|---|---|---|
| 收敛范围 | 安装器全量 + 干净机 | 仅 `core/store.py` |
| 是否关闭 G-01 | **是**（G-01/M3 正门） | **否** |
| 停机 | 长（安装/bootstrap/数据迁移） | 短（停服+重启，约分钟级） |
| 数据迁移 | 需要（若替换现有生产） | 不需要 |
| 前置阻塞 | M3d `initdb` 未闭环、干净 VM 证据缺失、构建产物需重建 | 生产写权限 + 维护窗口 |
| 回滚 | 安装器/数据/版本回滚，复杂 | 单文件恢复 + 重启，简单 |
| 证据强度 | 强（干净环境实测） | 中（单文件静态等价+冒烟+回滚） |
| 风险 | 高（安装/数据/环境） | 低-中（代码路径等价、窗口可控） |
| 本次是否可执行 | **否**（前置未满足） | 是（需授权） |

### 5.4 推荐

**推荐 B 作为本次 `store.py` 收敛动作；A 继续作为 G-01/M3 关闭门禁，另行排期。**
理由：

1. 本任务范围是**单文件 drift**；B 的改动面、停机、回滚都最小，且不触碰生产
   配置/数据库/安装器。
2. 差异经 diff、补丁可逆性、blob 哈希三方交叉验证：PG 路径功能等价，
   SQLite 改动在当前 `db_backend=pg` 下不执行；4F-2C 已证明生产可通过
   `pg_timeouts` 只读验证，回滚目标版也包含该符号。
3. A 目前**不可执行**：M3d 的 `initdb` 阻塞未闭环、干净 Win10/11 证据缺失、
   现有 launcher/安装器产物落后 HEAD；把单文件收敛与这些未决问题耦合，
   会扩大故障面并拖延收敛。
4. B 的结论必须明确标注：**G-01 保持 open**；M3 干净安装验收仍是后续必做项。

**会改变推荐的条件**：

- 若治理要求“禁止手改生产，只能通过安装器收敛”，则改走 A；B 仅作应急。
- 若 `store.py` 下一次变更包含 **PG 路径功能变化**，或 worker 回收方案 B
  （store 连接注册/取消）获批，应先完成本收敛并把新变更基于仓库 HEAD 实现。
- 若生产计划切回/启用 sqlite，则必须先补 SQLite 在生产环境的行为验证。

## 6. 路径 B：迁移/部署步骤（待授权执行）

> 以下为**执行清单**，本次分析未执行。所有路径写操作、停服、重启都需单独授权。

### 6.1 前置条件与授权

- [ ] Lead/用户批准维护窗口；期间不上线新功能、不并行改 `store.py`。
- [ ] 确认无待合并的 `store.py` 变更（worker 回收方案仍为 Proposal，未实现）。
- [ ] 4F-1/2A/2B 冻结保持；Shadow 保持关闭；不改 config/secrets。
- [ ] 生产写入/停服/重启权限（安装目录 ACL：当前用户对 `store.py` 有 FullControl）。
- [ ] 备份目录可写（`D:\AI\Mobius\backups` 当前不存在，lifecycle 会创建）。

### 6.2 基线取证（只读）

```powershell
$repo = "C:\Users\lenovo\Desktop\_说明文档\mobius"
$app  = "D:\AI\Mobius"
git -C $repo rev-parse HEAD
git -C $repo status --short
Get-FileHash "$app\core\store.py" -Algorithm SHA256          # 期望 3A20A70B…
git -C $repo -c core.autocrlf=true hash-object "$app\core\store.py"  # 期望 ba1d733…
Push-Location $app
& "$app\runtime\python-core\Scripts\python.exe" -B -c "import core.store,hashlib;print(core.store.__file__, hashlib.sha256(open(core.store.__file__,'rb').read()).hexdigest(), hasattr(core.store,'pg_timeouts'))"
Pop-Location
```

另记录：`/health`、`/api/identity/me` 200/401、`/api/capabilities/permissions`
body hash、Shadow 事件数、`schema_meta.schema_version`、Core PID/启动时间，作为
部署后对比基线。不得打印 DSN/密钥。

### 6.3 备份

1. 单文件备份：
   `Copy-Item "$app\core\store.py" "$app\core\store.py.bak-g01m3-<ts>"`，
   校验 SHA256 必须为 `3A20A70B…`。
2. 应用级备份（config/secrets/三库 PG dump/manifest/SHA256SUMS）：
   ```powershell
   python "$repo\shared\lifecycle.py" --root "$app" backup
   python "$repo\shared\lifecycle.py" --root "$app" restore --from <backup> --scratch
   ```
   第二条用于校验备份可恢复（scratch，不碰生产库）；记录 backup 路径与
   `SHA256SUMS`。PG 必须处于运行状态；备份目录应收紧 ACL，文档中不出现口令。
3. 所有备份哈希写入部署记录。

### 6.4 部署文件清单

| 动作 | 文件 | 目标 SHA256 |
|---|---|---|
| 替换 | `D:\AI\Mobius\core\store.py` | `CD0842C062B9AAF94F5CB1D36DCAB617C7E1D65F473E35FC0969F59EF557393D`（仓库工作树字节；安装器打包同一文件） |
| 备份 | `D:\AI\Mobius\core\store.py.bak-g01m3-<ts>` | `3A20A70B…`（回滚源） |
| 清理 | `D:\AI\Mobius\core\__pycache__\store.cpython-312.pyc` | 旧缓存，删除（导入会按源重建/或按权限跳过写缓存） |
| 不改 | `config.json`、`secrets.json`、其它 `core/*`、`shared/*`、安装器 | — |

> 说明：仓库 `core/store.py` 工作树是 CRLF（50186 字节，SHA256 `CD0842…`），
> 与安装器 `core\*` 打包源一致；Git blob `04d17f2…` 是 LF 归一化后的审计 blob，
> 不作为安装副本的字节哈希。两种哈希都要登记。

### 6.5 停服 → 替换 → 启动

1. 停止：`& "$app\MobiusCLI.exe" --headless stop`（会停服务、Core、PG；
   等待进程/端口退出）。
2. 替换：从仓库工作树复制 `core/store.py`（`Copy-Item`，保留目标 ACL/编码）；
   删除旧 `store.cpython-312.pyc`；校验 SHA256 = `CD0842…`。
3. 预启动只读导入（`-B`，工作目录 `$app`，不连数据库）：确认 `__file__`、
   `DB_BACKEND=pg`、`hasattr(close_anchor/_conn_lock)=True`、`PG_CONNECT_TIMEOUT=5`。
4. 启动：`& "$app\MobiusCLI.exe" --headless start`；等待 `/health` 200 与服务就绪。

### 6.6 部署后验证（生产只读）

- [ ] `store.__file__` 指向 `D:\AI\Mobius\core\store.py`，SHA256 = `CD0842…`；
      `hasattr(close_anchor)=True`、`hasattr(_conn_lock)=True`、`DB_BACKEND=pg`。
- [ ] `/health` 200；Core 日志无 import/atexit/启动异常。
- [ ] `GET /api/identity/me`：正确 Bearer → 200；缺失/错误 → 401 +
      `WWW-Authenticate: Bearer` + `Cache-Control: no-store`。
- [ ] `/api/capabilities/permissions` body hash 与基线一致；Shadow 仍关闭且事件数不变。
- [ ] `schema_meta.schema_version = 2`；关键表行数变化在正常范围（无 DDL 变更预期）。
- [ ] PG 只读：`pg_timeouts(statement_timeout_ms=5000)` 内新建连接
      `SHOW statement_timeout = 5s`；退出后普通连接恢复 `0`；不得打印 DSN。
- [ ] Legacy 放行行为不变；服务状态（gateway/comfyui/kokoro/napcat/postgres 等）
      与部署前基线一致。
- [ ] 记录部署时间、文件哈希、备份路径、验证输出；按授权另开文档更新
      `ARCHITECTURE-GAP.md` 中 G-01 证据（**不要**直接把 G-01 标记关闭）。

## 7. 回滚

### 7.1 触发条件

- Core 启动失败/健康检查超时；import 或 atexit 报错；
- identity 200/401 契约破坏、Legacy 行为变化；
- PG 连接/查询异常、schema 或数据异常；
- 任何与 §6.6 基线不符且无法快速定位的情况。

### 7.2 步骤

1. `MobiusCLI.exe --headless stop`。
2. 恢复 `store.py.bak-g01m3-<ts>` → `store.py`；SHA256 必须回到 `3A20A70B…`；
   删除新 pyc。
3. `MobiusCLI.exe --headless start`；重复 §6.2 的基线检查与 §6.6 的
   identity/Legacy/Shadow/PG timeout 检查。
4. 回滚后确认 `identity_api.py` 仍可用（旧版 `store.py` 含 `pg_timeouts`，
   与新版 `identity_api.py` 兼容），无需回滚 identity_api。
5. 数据库回滚：本变更**不涉及 schema 变更**，正常不需要；若出现异常且确认是
   数据问题，只能使用 §6.3 的 lifecycle 备份走独立的数据恢复流程。当前
   `restore --force` 未开放（M3c 边界），不能把“备份成功”当作“可自动恢复”。

### 7.3 回滚演练

生产回滚必须在 staging/副本先演练：复制 `{app}` 到临时目录 → 替换为 HEAD 版 →
启动冒烟 → 恢复 `.bak` → 再冒烟；记录两次哈希与结果。未经演练不执行生产变更。

## 8. 测试矩阵

> 本轮**未执行**任何测试；下表是执行路径 B 前后必须完成的矩阵。
> “本轮状态”全部为“未执行（只读分析）”。任何“可导入/可编译”都不等于已验证。

| ID | 测试项 | 环境 | 方法/命令 | 通过标准 | 证据 |
|---|---|---|---|---|---|
| T1 | **SQLite 并发初始化** | 仓库 checkout（临时 SQLite） | `python <repo>\tests\run_unit.py`（含 `test_store_concurrency`，6 轮×8 线程）；可重复 ≥3 次 | 无 `attempt to write a readonly database`；`peak == 1`；`init_db()` 后 `journal_mode = wal` | 测试输出、Python/SQLite 版本 |
| T2 | **跨进程启动** | 仓库 checkout | `python <repo>\tests\test_store_multiprocess.py`；`python <repo>\tests\check_probe_negative.py` | 4 进程×3 轮 barrier 启动 0 错误；负向探针在故障注入时非零退出且 ≤12s | 两命令输出 |
| T3 | **重启恢复** | 仓库 SQLite + 生产 PG 各一次 | SQLite：临时库建 `kv`/task → `close_anchor()` → 重新 `init_db()`/重启进程 → 读回；生产：§6.5 重启 → 健康/身份/任务/历史 | 数据可读、WAL 正常、无锁残留；生产健康且任务状态连续 | 输出、日志、行数 |
| T4 | **PG 连接** | 单测 mock + 独立 scratch PG + 生产只读 | `python <repo>\tests\test_store_pg_timeout.py`；`$env:MOBIUS_TEST_PG_DSN=…` 后 `python <repo>\tests\test_registry_pg_contract.py`；生产 §6.6 的 `SHOW statement_timeout` | 参数传递正确；上下文内 5s、退出恢复 0；PG 契约通过；identity/me 200/401 | 单测输出、只读查询输出 |
| T5 | **回滚** | staging 副本 | 按 §7.3 演练：HEAD 版 → 冒烟 → 恢复 `.bak` → 冒烟 | 两次冒烟均通过；恢复后哈希 = `3A20A70B…`；identity/Legacy/Shadow 不退化 | 演练记录 |
| T6 | **安装副本一致性** | 干净 VM / 安装副本 | 重建 launcher+installer（A 路径）或仅验证 B 后的安装副本：`git -C <repo> -c core.autocrlf=true hash-object <app>\core\store.py` == `git -C <repo> rev-parse HEAD:core/store.py`；SHA256 == `CD0842…`；`-B` 导入；若做 A，则在 `{app}` 下运行 `tests/run_unit.py`（tests 需复制/依赖需安装） | 哈希一致；`store.__file__` 在 `{app}`；无开发目录依赖；测试全部通过 | 哈希表、安装日志 |
| T7 | **PG schema/回归** | 生产只读 | `schema_meta.schema_version=2`；`/api/capabilities/permissions` hash；Shadow 事件数 | 与基线一致 | 查询/HTTP 输出 |
| T8 | **启动/停止生命周期** | 生产维护窗口 | stop → start 各一次；确认 PG/服务按预期恢复 | 无残留进程；服务就绪；错误日志为空 | 日志、`/api/services` |

补充：

- 干净安装 A 的默认 `config.example.json` 为 `db_backend=pg`，因此**默认干净安装
  不会执行 SQLite WAL 修复路径**；必须在干净副本上显式运行 T1/T2（或切 sqlite
  临时配置）才能验证 SQLite 修复，不能只看“安装成功”。
- 生产侧 T1/T2 不适用（生产是 PG）；生产侧只验证 T3/T4/T7/T8。

## 9. 未验证项与风险

### 9.1 本轮明确未验证

- 未运行任何单元/集成/E2E 测试；未跑 CI；未做 `py_compile`（不作为验证）；
- 未部署、未停服、未重启、未执行安装器；未开 Shadow；未改生产/config；
- 未验证 SQLite 并发/跨进程/重启行为；未做 PG 连接实测与故障注入；
- 未做回滚演练；未构建/安装新安装器；未做干净 Windows VM 验收；
- 未验证生产 Core 的 cwd/sys.path 与文档口径一致（只读导入已证明 venv Python
  从安装路径加载 `store.py`，但不等于完整运行时验证）；
- 未复核 G-01 的其它 drift（`secrets.py`、`shared/lifecycle.py`、launcher/安装器
  产物）；未复核 CI 在本 HEAD 的绿色状态（HEAD 为文档提交，历史 CI 证据见
  [4F-2C 报告](../../reports/PHASE4F2C-IDENTITY-API-REPORT.md) §6）。

### 9.2 风险清单

| 风险 | 等级 | 说明/缓解 |
|---|---|---|
| PG 路径“静态等价”被动态行为打破 | 中 | 差异仅注释/顺序，但仍需 §6.5 预启动导入 + §6.6 冒烟 + 回滚演练；不把静态分析当运行时保证 |
| `atexit.register(close_anchor)` 在 PG 进程退出时的行为 | 低 | PG 下 `_anchor=None`，函数为 no-op；关注 shutdown 日志 |
| 陈旧 pyc/只读 pycache 造成“加载是否生效”误判 | 低 | 删除旧 pyc；以 `store.__file__` + 文件 SHA256 判定，不依赖 pyc |
| 生产文件写入/停服权限与窗口 | 中 | 需授权；ACL 显示当前用户对 `store.py` 有 FullControl；按 §6 执行 |
| DB 级回滚不可自动化 | 中 | `restore --force` 未开放；本变更无 DDL，正常走文件回滚即可；数据恢复另行审批 |
| B 不关闭 G-01，安装副本仍非 HEAD 全量 | 中 | 明确记录 G-01 open；M3d 干净安装验收另行排期 |
| worker 回收方案 B 将改 `store.py` | 中 | 先完成本收敛；新方案必须基于仓库 HEAD 实现，禁止再对最小补丁版打补丁 |
| 干净安装默认 `db_backend=pg` 不覆盖 SQLite 修复 | 中 | A 的测试矩阵必须显式跑 T1/T2（sqlite）或临时 sqlite 配置 |
| 生产 `secrets.py`/`shared/lifecycle.py` 等其它 drift | 中 | 不在本方案范围；G-01 关闭前需单独收敛/登记 |

## 10. 下一步与所需授权

1. **决策**：确认采用 B 本次执行、A 作为 G-01/M3 门禁（或明确改为 A 优先）。
2. **授权（B）**：
   - 生产写 `D:\AI\Mobius\core\store.py`；
   - 创建 `D:\AI\Mobius\backups` 与应用级备份；
   - 维护窗口内停止/启动服务、Core、PG；
   - 部署后只读验证（identity、PG timeout、schema、Shadow 关闭）。
3. **授权（A，后续）**：M3d `initdb` 问题排期、干净 Win10/11 VM、launcher/安装器
   重建、若替换现有生产的数据迁移与生命周期演练。
4. **文档授权**：执行完成后按实际结果更新 `ARCHITECTURE-GAP.md` 的 G-01 证据
   （此前不得把 G-01 标为关闭），并新增部署/回滚证据报告。
5. **禁止项**：不把本文当部署授权；不把静态分析/可导入当运行时验证；不在本次
   开 Shadow 或修改 config。

## 附录 A：只读分析命令（可复现）

```powershell
$repo = "C:\Users\lenovo\Desktop\_说明文档\mobius"
$app  = "D:\AI\Mobius"

# 1) 哈希与 blob
Get-FileHash "$repo\core\store.py" -Algorithm SHA256
Get-FileHash "$app\core\store.py" -Algorithm SHA256
git -C $repo rev-parse HEAD:core/store.py
git -C $repo -c core.autocrlf=true hash-object "$app\core\store.py"
git -C $repo -c core.autocrlf=true hash-object "$app\core\store.py.bak-4f2c-20261114-155544"

# 2) 差异
git -C $repo diff --no-index --stat -- "$app\core\store.py" "$repo\core\store.py"
git -C $repo diff --no-index -U8 -- "$app\core\store.py" "$repo\core\store.py"

# 3) 补丁可逆性（只在 %TEMP% 副本上执行）
$tmp = Join-Path $env:TEMP "g01-verify"
New-Item -ItemType Directory -Force "$tmp\work\core" | Out-Null
Copy-Item "$app\core\store.py" "$tmp\work\core\store.py"
git -C "$tmp\work" apply -R "$repo\docs\aegis\patches\2026-11-14-phase4f2c-store-minimal.patch"
Get-FileHash "$tmp\work\core\store.py" -Algorithm SHA256   # 期望 EA5EF74F…
git -C "$tmp\work" apply "$repo\docs\aegis\patches\2026-11-14-phase4f2c-store-minimal.patch"
Get-FileHash "$tmp\work\core\store.py" -Algorithm SHA256   # 期望 3A20A70B…

# 4) 生产只读导入（-B，禁止写 pyc；不连数据库）
Push-Location $app
& "$app\runtime\python-core\Scripts\python.exe" -B -c "import core.store,hashlib;print(core.store.__file__, hashlib.sha256(open(core.store.__file__,'rb').read()).hexdigest(), core.store.DB_BACKEND, hasattr(core.store,'pg_timeouts'), hasattr(core.store,'close_anchor'))"
Pop-Location

# 5) 进程/端口只读检查
Get-CimInstance Win32_Process -Filter "ProcessId = 29784" | Select-Object ProcessId,ParentProcessId,CommandLine
Get-NetTCPConnection -LocalPort 8900 -State Listen
```

## 附录 B：diff hunk 与符号索引

| hunk | 范围（生产 → 仓库） | 内容 |
|---|---|---|
| 1 | `-1,23 +1,24` | `import atexit`、`contextlib/pathlib` 顺序 |
| 2 | `-63,31 +64,43` | PG timeout 注释、`_conn_lock`、`_anchor/_anchor_key` |
| 3 | `-161,28 +174,146` | `_ClosingConnection`、`_open_sqlite_connection`、`_db_process_lock`、`_ensure_anchor`、`close_anchor`、`atexit`、`conn()` |
| 4 | `-283,30 +414,51` | `init_db()` PG/SQLite 分支与 anchor 初始化 |

仓库符号：`_conn_lock`(86)、`_anchor/_anchor_key`(89-90)、`_ClosingConnection`(182-196)、
`_open_sqlite_connection`(199-206)、`_db_process_lock`(209-263)、`_ensure_anchor`(266-286)、
`close_anchor`(289-298)、`atexit.register`(301)、`conn()`(304-310)、`init_db()`(421-456)。

生产对应：`conn()`(169-178)、`init_db()`(290-304)；生产无 anchor/锁类符号。
