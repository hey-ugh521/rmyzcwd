# G-01/M3 生产 `store.py` 收敛复核报告

- 日期：2026-11-14
- 状态：**只读分析与方案复核完成；未部署、未重启、未运行测试、未开 Shadow**
- 复核对象：[G-01/M3 生产 store.py 收敛方案](../aegis/plans/2026-11-14-g01-m3-store-convergence.md)
- 复核性质：对方案中的事实、差异分类、路径选择与测试矩阵做独立可复核性检查
- 复核输入：仓库 HEAD `f1f57c5`；生产 `D:\AI\Mobius`（只读）；已有
  [4F-2C 策略](../aegis/plans/2026-11-14-phase4f2c-store-deploy-strategy.md)、
  [最小补丁](../aegis/patches/2026-11-14-phase4f2c-store-minimal.patch)、
  [4F-2C 报告](PHASE4F2C-IDENTITY-API-REPORT.md)、
  [M3c 干净 VM 发现](M3C-CLEAN-VM-FINDINGS.md)

## 1. 结论摘要

1. **事实成立**：生产 `store.py` = 4F-2C 最小补丁版（`3A20A70B…`），仓库 HEAD
   = 完整版（`CD0842C0…`）；差异 **+171/−19**。生产旧版备份
   （`EA5EF74F…`）与补丁头 `index 50c6f02..ba1d733` 通过 Git blob 哈希
   一一对应，补丁在临时副本上可逆。
2. **差异分类成立**：SQLite WAL/连接生命周期是唯一功能差异；PG timeout
   两版功能等价（仅注释/导入顺序）；其余为格式/注释。
3. **路径选择认可**：本次采用 **B：现有生产升级（单文件替换）** 是合理的
   低风险收敛动作；**A：干净安装 M3** 仍是 G-01/M3 关闭门禁，且当前被 M3d
   `initdb` 阻塞、现有安装器/启动器产物落后 HEAD，不能代替或立即执行。
4. **G-01 状态**：B 之后 G-01 **仍为 open**；生产安装副本仍非 HEAD 全量
   （另有 `secrets.py` 差异、`shared/lifecycle.py` 缺失等未收敛项）。
5. **验证状态**：本轮没有运行任何测试、没有部署、没有重启；所有“等价/
   低风险”结论均为只读静态分析 + 哈希/补丁证据 + 一次 `-B` 只读导入，
   不构成运行时验证。执行前须按方案 §8 测试矩阵完成验证。

## 2. 复核方法

- 只读文件检查、SHA256/字节数/行数、`git diff --no-index`、`git hash-object`。
- 在 `%TEMP%` 副本上做补丁正向/反向应用，验证“生产当前版 = 生产旧版 + 补丁”。
- 用生产 venv Python 执行 `-B`（禁止写 pyc）仅导入 `core.store`，验证
  当前加载文件路径、DB 后端符号与默认值；不连数据库、不调用业务接口。
- 只读进程/端口/config 字段检查（不打印 DSN、密钥、token）。
- **未运行**单元测试、CI、安装器、干净 VM 演练、PG 查询、故障注入、回滚演练。

## 3. 证据复核

### 3.1 文件与哈希

| 对象 | SHA256 | 字节/行数 | 复核结果 |
|---|---|---|---|
| 仓库 `core/store.py`（HEAD 内容，工作树 CRLF 字节） | `CD0842C062B9AAF94F5CB1D36DCAB617C7E1D65F473E35FC0969F59EF557393D` | 50186 / 1302 | 与方案一致 |
| 生产 `core/store.py` | `3A20A70BFA52A4864C3A08E0796829DC0BA0E79E0F6B8D29D9CD5041EAF403C1` | 45374 / 1150 | 与方案一致 |
| 生产备份 `store.py.bak-4f2c-20261114-155544` | `EA5EF74FE23D8C04476320354CF30142B06E6366225C550D362EA9DA15A26B93` | 43360 | 与策略文档记录前缀一致 |
| 最小补丁 | `D27C9A12F47FA99C02734E654E20FEDD94BAB4945AB8DADBDEC1C319EAC31E2B` | 91 行 diff | 与策略文档“91 行 diff”一致 |

Git blob 链（`git hash-object` / `git rev-parse`）：

| 对象 | blob |
|---|---|
| 生产备份（补丁前） | `50c6f02fe1919775a677e4d1cc8f6b66c08f1ca4` |
| 生产当前（补丁后） | `ba1d7330e4513c4958d051fbca4c163ab7718f06` |
| 仓库 HEAD `core/store.py` | `04d17f2d1bf99804cc0aa7fc7cbcf777f765d5f6` |

补丁头 `index 50c6f02..ba1d733` 与备份/当前文件的 blob **完全吻合**。

### 3.2 补丁可逆性（临时目录，未写生产）

| 操作 | 结果 |
|---|---|
| 生产当前版 `git apply -R` 最小补丁 | 成功；SHA256 → `EA5EF74F…`（= 备份文件） |
| 备份文件 `git apply` 最小补丁 | 成功；SHA256 → `3A20A70B…`（= 生产当前版） |
| 对生产当前版 `git apply -R --check` | rc=0 |

结论：生产当前版确由“生产旧版 + 4F-2C 最小补丁”构成，没有未记录的第三方改动。

### 3.3 生产只读快照

- Core PID `29784`（`python -m core.server`），启动时间 2026-11-14 15:56:07；
  `127.0.0.1:8900` 监听。父进程为 `D:\AI\mobius-runtime\python-core\Scripts\python.exe`。
- 生产 config：`db_backend=pg`；`db_connect_timeout`、`db_statement_timeout_ms`
  均不存在（默认 5s / 0）；config SHA256 `80F934C4933E…`，本轮未修改。
- `-B` 只读导入：
  - `core.store.__file__` = `D:\AI\Mobius\core\store.py`；
  - SHA256 = `3A20A70B…`；`DB_BACKEND=pg`；
  - `hasattr(pg_timeouts)=True`、`hasattr(close_anchor)=False`、
    `hasattr(_conn_lock)=False`；`PG_CONNECT_TIMEOUT=5`、`PG_STATEMENT_TIMEOUT_MS=0`。
  - 导入前后生产 `store.py`/pyc 的 mtime 与哈希未变化（确认 `-B` 未写 pyc）。
- 生产 pycache：`store.cpython-312.pyc` 头部记录源大小 43319（2026-11-10 旧版），
  属于陈旧缓存；`-B` 导入证明运行期按源文件重编译。审计时不应以 pyc 判断版本。

### 3.4 已同源/未同源抽查

| 文件 | 生产 vs 仓库 HEAD | 备注 |
|---|---|---|
| `core/identity_api.py` | 相同（`20620DA8E05A…`） | 4F-2C 部署一致 |
| `core/server.py` | 相同（`7F77BB2B77BE…`） | — |
| `core/paths.py` | 相同（`57A2D9817FB8…`） | 此前已收敛 |
| `shared/paths.py` | 相同（`A446CD530173…`） | — |
| `core/secrets.py` | **不同** | 生产 `DACCF897…`，仓库 `151135AB…`；不在本方案范围 |
| `shared/lifecycle.py` | **生产缺失** | M3b 工具未进入该安装副本；备份需从仓库侧运行 |
| launcher | 生产 exe 与仓库 `launcher\dist` 同哈希（2026-11-12 构建） | 源文件已更新，exe 落后 HEAD |

### 3.5 构建产物落后

- `installer\dist\MobiusSetup-0.2.0.exe`：2026-11-11 18:55；
- `launcher\dist\Mobius.exe/MobiusCLI.exe`：2026-11-12 21:43；
- 仓库 HEAD 的 `core/store.py` 最后修改于 2026-11-14（`e646e4b`）。
- 因此**当前安装器不能用于“干净安装 = 当前代码”的 G-01 验收**；路径 A 必须
  先重建 launcher 与安装器。

## 4. 差异分类复核

| 类别 | 复核结论 | 依据 |
|---|---|---|
| SQLite WAL / 生命周期 | **功能差异（仅 SQLite）**：`_conn_lock`、anchor、`_ClosingConnection`、跨进程 `.init.lock`、WAL 仅在初始化设置、`timeout=30`/`busy_timeout`、`atexit` | 4 个 hunk 中的 hunk 3/4；仓库 `:86,182-310,421-456`；生产 `:169-178,290-304` |
| PG timeout | **无功能差异**：仅注释/docstring；默认值、`_cfg_int`、`pg_timeouts`、`_PgConnection` kwargs 等价 | hunk 2 仅注释；生产 `:51-73,83-102,122-130` vs 仓库 `:52-76,95-115,135-143` |
| 其他 | **无语义差异**：`import atexit`、`contextlib/pathlib` 顺序、注释/空行 | hunk 1 |
| 统计 | +171/−19（4 hunks）；1150 → 1302 行 | `git diff --no-index --numstat` |

独立判断：不存在第 5 个功能 hunk；不存在 schema 变更；不存在 config 新键；
不存在 PG 连接参数变化。方案中“PG 路径功能等价、SQLite 代码在 PG 下休眠”的
分类成立（**仅静态**）。

## 5. 路径评估复核

### 5.1 对推荐 B 的复核

认可方案推荐 **B 本次、A 作为 G-01 门禁**，理由复核如下：

- B 的 blast radius 只有一个文件；无 config/数据/安装器变更；回滚为文件恢复。
- PG timeout 部分已经过 4F-2C 生产只读验证（上下文内 5s、退出恢复 0），
  identity 依赖的 `pg_timeouts` 在回滚目标版中同样存在，故回滚不需要回滚
  `identity_api.py`。
- SQLite 差异虽多，但当前 `db_backend=pg`，新增代码不进入执行路径；导入级
  新增（`atexit`、`sqlite3.Connection` 子类）风险低。
- A 当前不可作为本任务的执行路径：M3c 记录的 `initdb 0xC0000135` 未闭环；
  没有 Win10/11 干净机证据；launcher/installer 产物落后 HEAD；替换现有生产
  需要数据迁移与 lifecycle `restore --force`（未开放）。

### 5.2 对推荐的风险/反方意见

- 若组织要求“只通过安装器收敛生产、禁止手改安装副本”，则 B 不适用，应先做 A；
  这是唯一可能改变推荐的治理条件。
- B 完成后 **G-01 不能关闭**：安装副本仍不是 HEAD 全量（`secrets.py`、
  `shared/lifecycle.py`、launcher/安装器产物等）；方案已明确这一点，复核认可。
- 若 `store.py` 下一次变更涉及 PG 路径或 worker 回收方案 B，必须在本次收敛后
  以仓库 HEAD 为基线实现；否则会再次产生双版本漂移。

## 6. 测试矩阵状态

本轮**未执行**任何测试；以下为执行 B/A 前必须完成的矩阵（与方案 §8 对齐）。
每项执行时须保存命令、版本、输出与时间戳；不能用“可导入/可编译”代替。

| ID | 项目 | 计划命令/方法 | 本轮状态 |
|---|---|---|---|
| T1 | SQLite 并发初始化 | `python <repo>\tests\run_unit.py`（含 `test_store_concurrency`），≥3 次 | 未执行 |
| T2 | 跨进程 | `python <repo>\tests\test_store_multiprocess.py`；`python <repo>\tests\check_probe_negative.py` | 未执行 |
| T3 | 重启恢复 | SQLite 临时库重启读回；生产 §6.5 重启后健康/身份/任务/历史 | 未执行 |
| T4 | PG 连接 | `test_store_pg_timeout.py`；scratch PG `test_registry_pg_contract.py`；生产 `SHOW statement_timeout` | 未执行 |
| T5 | 回滚 | staging 副本按方案 §7.3 演练两次冒烟 | 未执行 |
| T6 | 安装副本一致性 | `git hash-object`/SHA256 对比；`-B` 导入；安装副本运行 `run_unit.py`（A 路径） | 未执行 |
| T7 | PG schema/回归 | `schema_version=2`、capabilities hash、Shadow 事件数不变 | 未执行 |
| T8 | 生命周期 | 生产维护窗口 stop→start，服务/进程/日志检查 | 未执行 |

注意：干净安装默认 `config.example.json` 的 `db_backend=pg`，默认安装不会执行
SQLite 修复路径；T1/T2 必须在 sqlite 模式下显式跑，不能以“安装成功”替代。

## 7. 未闭合项与风险

| # | 项 | 状态/建议 |
|---|---|---|
| 1 | 生产 `store.py` 收敛（B） | 待授权执行；方案 §6 |
| 2 | G-01 关闭 | **未闭合**；需 A（干净安装 M3）证据 |
| 3 | M3d `initdb 0xC0000135` | 未闭环（M3c 文档）；A 的硬前置 |
| 4 | 干净 Win10/11 x64 证据 | 缺失；Server 2022 只是预验收 |
| 5 | launcher/安装器产物 | 落后 HEAD；A 需重建；B 不需要 |
| 6 | 生产 `secrets.py` drift | 不在本方案范围；G-01 其它 drift 需单独登记/收敛 |
| 7 | 生产 `shared/lifecycle.py` 缺失 | 备份需从仓库运行 `lifecycle.py --root`；G-01 收敛项 |
| 8 | worker 回收方案 B | 未实现；若获批必须基于仓库 HEAD，不得再对最小补丁版打补丁 |
| 9 | PG 路径等价性的动态验证 | 仅静态 + 历史 4F-2C 只读证据；需 T4/T7 + 冒烟 |
| 10 | DB 级回滚 | `restore --force` 未开放；本变更无 DDL，正常走文件回滚 |
| 11 | 测试/CI 状态 | 本轮未跑；HEAD 为文档提交；历史 CI 证据见 4F-2C 报告 §6 |
| 12 | 生产 pycache 陈旧/可能不可写 | 删除旧 pyc；以 `store.__file__` + SHA256 判定加载版本 |

## 8. 复核限制与声明

- 本报告是**只读分析复核**，不是部署记录、不是验收报告、不是运行时验证。
- `-B` 只读导入只证明“生产 venv 从安装路径加载了当前 `store.py` 及其符号/默认值”，
  不证明 PG 查询、identity 接口、重启恢复、并发/跨进程、回滚等行为。
- 未修改 `D:\AI\Mobius` 任何文件；未重启/未停服；未开 Shadow；未改 config；
  未执行安装器；未运行测试。
- 分析期间工作区曾出现未跟踪文件
  `docs/aegis/plans/2026-11-14-worker-recovery-plan.md`（非本任务创建/修改，仅作
  关联输入阅读）；本任务未触碰它。
- 本任务只新增两份文档：本报告与
  [收敛方案](../aegis/plans/2026-11-14-g01-m3-store-convergence.md)。

## 9. 下一步与所需授权

1. **决策授权**：确认 B 本次执行、A 作为 G-01/M3 门禁；或改为 A 优先。
2. **B 执行授权**：生产 `store.py` 写入、`backups` 目录创建、维护窗口 stop/start
   （服务/Core/PG）、部署后只读验证。
3. **A 后续授权**：M3d 排期、干净 Win10/11 VM、launcher/安装器重建、数据迁移
   与生命周期演练。
4. **文档授权**：执行后按实际结果更新 GAP 证据与部署记录；在 A 完成前不得将
   G-01 标记关闭。
5. **冻结项**：4F-1/2A/2B 保持冻结；Shadow 保持关闭；不改 config；不在本轮
   引入 worker 回收/store 新功能。

## 附录 A：证据哈希一览

```text
repo HEAD                          f1f57c5000c9d44cf875d8b4f0fed14ec69fe51b
repo core/store.py                 CD0842C062B9AAF94F5CB1D36DCAB617C7E1D65F473E35FC0969F59EF557393D
repo core/store.py blob            04d17f2d1bf99804cc0aa7fc7cbcf777f765d5f6
prod core/store.py                 3A20A70BFA52A4864C3A08E0796829DC0BA0E79E0F6B8D29D9CD5041EAF403C1
prod core/store.py blob            ba1d7330e4513c4958d051fbca4c163ab7718f06
prod backup store.py               EA5EF74FE23D8C04476320354CF30142B06E6366225C550D362EA9DA15A26B93
prod backup store.py blob          50c6f02fe1919775a677e4d1cc8f6b66c08f1ca4
minimal patch                      D27C9A12F47FA99C02734E654E20FEDD94BAB4945AB8DADBDEC1C319EAC31E2B
prod config.json                   80F934C4933E0557370587839F48877181B6D10475F0E1919AC450001590E519
prod identity_api.py               20620DA8E05A9EC7B908800E843BF8D9F1B0642FFD0A0B8196D480BA8FA6342A
prod server.py                     7F77BB2B77BEBA4FE5D1F20D4F0966D6CFC3745FE24D4FD3901939AF20D56EDC
prod/core paths.py                 57A2D9817FB86A4508398BEA5C0BC26C40DF2E4996814DE001EFE7B82601C613
shared/paths.py                    A446CD5301733627EBA2D3D37DF329693431BB732FE32C4B7812C64614354418
```

## 附录 B：复核用只读命令

```powershell
$repo = "C:\Users\lenovo\Desktop\_说明文档\mobius"
$app  = "D:\AI\Mobius"

Get-FileHash "$repo\core\store.py" -Algorithm SHA256
Get-FileHash "$app\core\store.py" -Algorithm SHA256
git -C $repo rev-parse HEAD:core/store.py
git -C $repo -c core.autocrlf=true hash-object "$app\core\store.py"
git -C $repo -c core.autocrlf=true hash-object "$app\core\store.py.bak-4f2c-20261114-155544"
git -C $repo diff --no-index --stat -- "$app\core\store.py" "$repo\core\store.py"

# 补丁可逆性：仅 %TEMP% 副本
$tmp = Join-Path $env:TEMP "g01-review"
New-Item -ItemType Directory -Force "$tmp\work\core" | Out-Null
Copy-Item "$app\core\store.py" "$tmp\work\core\store.py"
git -C "$tmp\work" apply -R "$repo\docs\aegis\patches\2026-11-14-phase4f2c-store-minimal.patch"
Get-FileHash "$tmp\work\core\store.py" -Algorithm SHA256
git -C "$tmp\work" apply "$repo\docs\aegis\patches\2026-11-14-phase4f2c-store-minimal.patch"
Get-FileHash "$tmp\work\core\store.py" -Algorithm SHA256

# 生产只读导入（-B；不连数据库）
Push-Location $app
& "$app\runtime\python-core\Scripts\python.exe" -B -c "import core.store,hashlib;print(core.store.__file__, hashlib.sha256(open(core.store.__file__,'rb').read()).hexdigest(), core.store.DB_BACKEND, hasattr(core.store,'pg_timeouts'), hasattr(core.store,'close_anchor'))"
Pop-Location
```
