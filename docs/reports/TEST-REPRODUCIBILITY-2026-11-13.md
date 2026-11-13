# 测试可复现性修复与 3.13 复跑报告

- 日期：2026-11-13
- 触发：干净 Python 3.13 上 `tests/test_identity.py` 3 个并发用例失败，报
  `sqlite3.OperationalError: attempt to write a readonly database`
- 结论：本机 3.13/3.12 未复现；已修复环境依赖和可移植性问题，并加入 CI 入口。
  G-10 **未完全关闭**（E2E/契约/安装仍未接入 CI）。

## 1. 复现结果

| 环境 | `test_identity.py` | 全套 unit |
|---|---|---|
| Core venv Python 3.12 | 25/25 | 91 unittest + 5 script |
| Anaconda Python 3.13.9 | 25/25 | 91 unittest + 5 script |
| Python 3.14 | 25/25 | 未跑 |

因此报告中的 25/25 来自实际运行，但用户环境仍出现 3 个并发失败，说明测试仍依赖本机环境。

## 2. 发现并修复的环境问题

### 2.1 SQLite 连接未统一关闭

`store.conn()` 的 `with conn() as c:` 只提交，不关闭连接；SQLite 的上下文管理器不会自动关闭。这会造成：

- WAL/文件锁泄漏
- 临时数据库清理失败
- 并发测试偶发异常

修复：

- `core/store.py` 增加 `_ClosingConnection`，`__exit__` 提交/回滚后关闭。
- `conn()` 统一 `busy_timeout=30000`、`foreign_keys=ON`、WAL。

### 2.2 FastAPI 0.139 的 `_IncludedRouter`

在新版 FastAPI 中，`app.routes` 不再总是扁平化，`include_router` 会保留 `_IncludedRouter` 包装；旧测试直接扫描 `app.routes` 会误判路由不存在。

修复：

- `test_registry_core_auth.py` 递归扫描 `_IncludedRouter.original_router.routes`。

### 2.3 缺少可移植入口和 CI

新增：

- `tests/run_unit.py`
  - 强制 `CORE_DB_BACKEND=sqlite`
  - 串行执行 10 组 unit 测试
  - QQ webhook 作为独立脚本执行
- `requirements-test.txt`
- `.github/workflows/unit-tests.yml`
  - Windows + Python 3.13
  - 安装测试依赖后运行 `python tests/run_unit.py`
- `tests/README.md` 说明哪些测试仍必须手动/真实环境

## 3. 复跑命令

```powershell
cd C:\Users\lenovo\Desktop\_说明文档\mobius
python tests\run_unit.py
```

本地结果：

```text
Ran 91 tests ... OK
test_qq_webhook.py: 5/5 通过
```

## 4. 仍未解决

- 用户环境中的只读数据库错误未在本机复现；需要完整 traceback、Python 版本、`TEMP` 路径和测试运行方式。
- **最可能的环境差异之一**：并行测试 runner。测试类仍共享进程级 `store.DB_PATH`；`pytest-xdist` 或并行测试方法会让一个 worker 看到另一测试的临时 DB 路径/已清理文件。`tests/run_unit.py` 是串行入口，不应并行运行。
- CI workflow 已提交，但尚未在 GitHub Actions 实际跑绿。
- E2E、PG contract、安装/故障注入仍未进入 CI。
- G-10 仍保持 P1，不能宣称关闭。

## 5. 后续

- 用 CI 实际跑一次，记录 run URL。
- 若 3.13 只读错误仍复现：
  - 打印 `store.DB_PATH`、父目录权限、SQLite 连接参数
  - 确认是否使用并行测试 runner 导致全局 `store.DB_PATH` 竞争
  - 考虑将测试 DB 路径改为 context-local / 每进程独立
