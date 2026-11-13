# 测试可复现性修复与 3.13 复跑报告

- 日期：2026-11-13
- 触发：干净 Python 3.13 上 `tests/test_identity.py` 并发用例失败，报
  `sqlite3.OperationalError: attempt to write a readonly database`
- 根因：WAL 库最后一个连接关闭后清理 `-shm`，并发首次打开争抢重建 shm；
  `store.conn()` 没有串行化连接创建
- 追加修复：`_conn_lock` 串行化 SQLite `conn()` 的 connect + PRAGMA
- 状态：进程内已修复；并发回归 + 5 轮复跑通过
- G-10 **仍未完全关闭**（E2E/契约/安装/跨进程策略/CI 有效性仍缺）

## 1. 根因

WAL 数据库在最后一个连接关闭后，SQLite 会清理 `-wal` / `-shm`。随后多个线程
（或进程）同时首次打开数据库，争抢重建 shared-memory 文件；其中一个连接可能
被判定为只读，紧接着：

```python
BEGIN IMMEDIATE
```

报：

```text
sqlite3.OperationalError: attempt to write a readonly database
```

上一轮把 `PRAGMA journal_mode=WAL` 从 `conn()` 移到 `init_db()` 只削掉了
“重复设置 WAL”这个触发源，**没有消除 last-close → 并发首次 open 的机制**。
因此用户环境在 `fe9099e` 仍稳定复现，新增并发用例也失败。

## 2. 复现证据

用户环境（Python 3.13.14 / SQLite 3.53.1）：

- 原始代码连跑 5 轮：4 轮复现
- 加锁串行化 `store.conn()`：5/5 绿
- `conn()` 不执行 WAL PRAGMA：8/8 绿
- 换 TEMP 目录：仍复现
- SQLite 3.50.4 / 3.51.0 / 3.53.1：全部复现
- 外部 patch 后完整 `run_unit.py`：94 tests OK（当前 runner 计数）

结论：版本、TEMP、并行 test runner 都不是根因；单用例内部两线程同时 `conn()`
即可触发。

## 3. 修复

`core/store.py`：

- `conn()` 不再执行 `PRAGMA journal_mode=WAL`
- 只在 `init_db()`（已有 `_lock` 串行保护）执行一次 WAL
- 新增独立 `_conn_lock`：SQLite `conn()` 的 connect + PRAGMA 全部串行
  - 锁顺序固定 `_lock → _conn_lock`，没有反向路径
  - 覆盖 last-close 后并发首次打开重建 shm 的窗口
- `conn()` 保留 `foreign_keys=ON`、`busy_timeout=30000`、`synchronous=NORMAL`
- `_ClosingConnection` 保留：修复 `with conn()` 的连接泄漏（另一个真实问题）

新增并发回归：

`tests/test_store_concurrency.py`

- 6 轮 × 8 线程同时 `conn()` → `BEGIN IMMEDIATE` → INSERT → COMMIT
- **确定性串行化回归**：用 slow `sqlite3.connect` 让 8 线程同时进入，断言
  `peak == 1`；去掉 `_conn_lock` 时该测试稳定失败（peak=8）
- 验证 `init_db()` 后 journal mode 为 WAL

`tests/run_unit.py` 已纳入该回归。

## 4. 修复后复跑

Core venv Python 3.12：

```text
5 轮：Ran 94 tests ... OK
QQ webhook 5/5
```

Anaconda Python 3.13.9：

```text
5 轮：Ran 94 tests ... OK
QQ webhook 5/5
```

合计每轮 99 个测试通过。

## 4.1 CI 事实

GitHub Actions `unit-tests` 在 `c2c1236`、`683487e`、`fe9099e` 都是 success，
包括修复前版本。因此：

- CI 已跑绿，但**不能作为修复证据**
- runner 没撞上竞态窗口
- 新加的 `_conn_lock` 串行化回归会稳定抓住“没有锁”的实现，避免 CI 再次假绿

## 4.2 跨进程探测

`tests/probe_store_multiprocess.py`（不在 CI）：

- 4 个 spawn 进程 + barrier 同时打开同一 WAL 库
- 当前本机 3 轮 0 错误
- 用户环境报告每轮 3 个 readonly，说明跨进程机制在另一台/另一构建上存在
- 进程内 `_conn_lock` 管不到跨进程，不能宣称修复
- 这不是“未覆盖”，是已知未定策略

`tests/run_unit.py` 不包含该 probe；CI 不包含。

## 5. 诊断命令结果

Anaconda：

```text
core.store.__file__ = C:\Users\lenovo\Desktop\_说明文档\mobius\core\store.py
sqlite3.sqlite_version = 3.51.0
CORE_DB_BACKEND = None（默认 config）
CORE_DB_BACKEND=sqlite 时 = sqlite
```

- 代码副本路径正确
- 无 env 时默认走 PG；测试 runner 会强制 sqlite
- 之前 3.13 “通过”是单次运行的竞态窗口未命中，不是环境差异

## 6. 仍然存在的缺口

- 跨进程 WAL 策略未定：锚连接 / 跨进程文件锁 / 单写者 / 仅 PG。`_conn_lock`
  只保证单进程内创建连接串行。
- CI 已绿但曾放行 bug 版本；需要新的串行化回归实际跑绿后才可改变判断。
- G-10 仍不能关闭：E2E、PG contract、安装/故障注入、跨进程策略仍未进入 CI。
- 生产 Core 仍是直接进程；不影响本次测试修复。

## 7. 结论

根因是 WAL last-close 后并发首次打开争抢重建 shm，不是 SQLite 版本漂移。
`fe9099e` 的去重复 PRAGMA 修复不充分；`_conn_lock` 补齐单进程内窗口，并通过
确定性串行化回归。跨进程策略另立设计，不能在当前修复里宣称解决。
