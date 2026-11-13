# 测试可复现性修复与 3.13 复跑报告

- 日期：2026-11-13
- 触发：干净 Python 3.13 上 `tests/test_identity.py` 并发用例失败，报
  `sqlite3.OperationalError: attempt to write a readonly database`
- 根因：WAL 库最后一个连接关闭后清理 `-shm`，并发首次打开争抢重建 shm
- 追加修复：`_conn_lock` + 进程级 anchor + 跨进程初始化文件锁
- 状态：单进程/跨进程均已实现保护；本机并发回归通过
- G-10 **仍未完全关闭**（E2E/契约/安装/CI 有效性仍缺）

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
- 外部 patch 后完整 `run_unit.py`：95 tests OK（当前 runner 计数）

结论：版本、TEMP、并行 test runner 都不是根因；单用例内部两线程同时 `conn()`
即可触发。

## 3. 修复

`core/store.py`：

- `conn()` 不再执行 `PRAGMA journal_mode=WAL`
- 只在 `init_db()`（已有 `_lock` 串行保护）执行一次 WAL
- 新增独立 `_conn_lock`：SQLite `conn()` 的 connect + PRAGMA 全部串行
  - 锁顺序固定 `_lock → _conn_lock`，没有反向路径
  - 覆盖单进程 last-close 后并发首次打开重建 shm 的窗口
- 新增进程级 anchor connection：
  - `init_db()` 保留一个连接到进程生命周期，`-wal/-shm` 不会因普通连接最后关闭而被清理
  - `atexit` 关闭 anchor
- 新增跨进程初始化文件锁：
  - SQLite 初始化时锁 `DB_PATH + ".init.lock"`
  - Windows 使用 `msvcrt.locking`，POSIX 使用 `fcntl.flock`
  - 锁顺序 `_lock → 进程文件锁`，进程内不会自锁
- `conn()` 保留 `foreign_keys=ON`、`busy_timeout=30000`、`synchronous=NORMAL`
- `_ClosingConnection` 保留：修复 `with conn()` 的连接泄漏（另一个真实问题）

新增并发回归：

`tests/test_store_concurrency.py`

- 6 轮 × 8 线程同时 `conn()` → `BEGIN IMMEDIATE` → INSERT → COMMIT
- **确定性串行化回归**：用 slow `sqlite3.connect` 让 8 线程同时进入，断言
  `peak == 1`；去掉 `_conn_lock` 时该测试稳定失败（peak=8）
- 验证 `init_db()` 后 journal mode 为 WAL

`tests/run_unit.py` 已纳入：

- `test_store_concurrency`：串行化 + 8 线程 WAL 压力
- `test_store_multiprocess`：4 个子进程 + barrier 同时启动，断言无 readonly

## 4. 修复后复跑

Core venv Python 3.12：

```text
5 轮：Ran 95 tests ... OK
QQ webhook 5/5
```

Anaconda Python 3.13.9：

```text
5 轮：Ran 95 tests ... OK
QQ webhook 5/5
```

合计每轮 100 个测试通过。

## 4.1 CI 事实

GitHub Actions `unit-tests` 在 `c2c1236`、`683487e`、`fe9099e` 都是 success，
包括修复前版本。因此：

- CI 已跑绿，但**不能作为修复证据**
- runner 没撞上竞态窗口
- 新加的 `_conn_lock` 串行化回归会稳定抓住“没有锁”的实现，避免 CI 再次假绿

## 4.2 跨进程探测

`tests/probe_store_multiprocess.py`：

- 4 个 spawn 进程 + barrier 同时打开同一 WAL 库
- 当前本机 3 轮 0 错误
- 4F-2A/4F-1 生产链路不受影响（生产是 PG）
- 已提升为 `tests/test_store_multiprocess.py`，进入 unit runner

`tests/run_unit.py` 包含该回归；CI 也会跑。

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

- anchor + 跨进程锁已实现并通过本机 probe；仍需在你的稳定失败环境复跑确认。
- CI 已绿但曾放行 bug 版本；新的串行化/多进程回归必须在 CI 实跑通过后才能改变判断。
- G-10 仍不能关闭：E2E、PG contract、安装/故障注入仍未进入 CI。
- 进程异常终止时 `.init.lock` 由 OS 释放，残留文件本身不阻塞（下次可重新加锁）。
- 生产 Core 仍是直接进程；不影响本次测试修复。

## 7. 结论

根因是 WAL last-close 后并发首次打开争抢重建 shm，不是 SQLite 版本漂移。
`fe9099e` 的去重复 PRAGMA 修复不充分；`_conn_lock` + 进程 anchor + 跨进程
初始化锁补齐单进程和跨进程窗口。跨进程是否真正消除竞态，需要你在稳定失败
环境复跑确认，不能只凭本机绿灯。
