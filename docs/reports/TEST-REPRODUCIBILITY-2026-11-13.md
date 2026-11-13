# 测试可复现性修复与 3.13 复跑报告

- 日期：2026-11-13
- 触发：干净 Python 3.13 上 `tests/test_identity.py` 并发用例失败，报
  `sqlite3.OperationalError: attempt to write a readonly database`
- 根因：`store.conn()` 每次连接都执行 `PRAGMA journal_mode=WAL`，并发切换 journal mode 竞态
- 状态：已修复；并发回归 + 5 轮复跑通过
- G-10 **仍未完全关闭**（E2E/契约/安装/CI 实跑仍缺）

## 1. 根因

`core/store.py` 旧实现：

```python
def conn():
    c = sqlite3.connect(...)
    c.execute("PRAGMA journal_mode=WAL")   # 每次连接都执行
```

WAL 是数据库文件级持久属性，不应每次连接切换。两个线程同时进入 `conn()` 时，
后到的连接可能拿到只读视图；紧接着 `identity._tx()` 的 `BEGIN IMMEDIATE` 报：

```text
sqlite3.OperationalError: attempt to write a readonly database
```

这是**代码竞态**，不是 Python/SQLite 版本差异，也不是 TEMP 权限问题。

## 2. 复现证据

用户环境（Python 3.13.14 / SQLite 3.53.1）：

- 原始代码连跑 5 轮：4 轮复现
- 加锁串行化 `store.conn()`：5/5 绿
- `conn()` 不执行 WAL PRAGMA：8/8 绿
- 换 TEMP 目录：仍复现
- SQLite 3.50.4 / 3.51.0 / 3.53.1：全部复现
- 外部 patch 后完整 `run_unit.py`：91 tests OK

结论：版本、TEMP、并行 test runner 都不是根因；单用例内部两线程同时 `conn()`
即可触发。

## 3. 修复

`core/store.py`：

- `conn()` 不再执行 `PRAGMA journal_mode=WAL`
- 只在 `init_db()`（已有 `_lock` 串行保护）执行一次 WAL
- `conn()` 保留 `foreign_keys=ON`、`busy_timeout=30000`、`synchronous=NORMAL`
- `_ClosingConnection` 保留：修复 `with conn()` 的连接泄漏（另一个真实问题）

新增并发回归：

`tests/test_store_concurrency.py`

- 6 轮 × 8 线程同时 `conn()` → `BEGIN IMMEDIATE` → INSERT → COMMIT
- 断言无异常
- 验证 `init_db()` 后 journal mode 为 WAL

`tests/run_unit.py` 已纳入该回归。

## 4. 修复后复跑

Core venv Python 3.12：

```text
5 轮：Ran 93 tests ... OK
QQ webhook 5/5
```

Anaconda Python 3.13.9：

```text
5 轮：Ran 93 tests ... OK
QQ webhook 5/5
```

合计每轮 98 个测试通过。

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

- CI workflow 已加入，但尚未在 GitHub Actions 实际跑绿
- G-10 仍不能关闭：E2E、PG contract、安装/故障注入仍未进入 CI
- 并发回归已补，但仍需 CI 实际运行观察
- 生产 Core 仍是直接进程；不影响本次测试修复

## 7. 结论

这次不是“环境漂移”，而是 SQLite 初始化竞态。单次本地绿灯不能证明测试可靠；
需要并发回归 + 多轮复跑 + CI。当前修复已通过 5 轮双解释器复跑，但不能替代
CI 实跑。
