# 4F-2C `store.py` 部署策略

- 日期：2026-11-14
- 状态：**策略已定；未部署**
- 关联：[4F-2C 部署授权](../reports/PHASE4F2C-DEPLOY-AUTHORIZATION.md)
- 关联：[4F-2C 后续问题](2026-11-14-phase4f2c-followups.md)

## 1. 决策

采用 **最小补丁**，不把仓库完整 `store.py` 覆盖到生产。

需要同时部署：

1. `core/identity_api.py`（当前版本）
2. `core/store.py` 的 timeout 最小补丁

`server.py` 已在部署时包含 identity router，本次无需再改。

## 2. 理由

生产 `store.py` 与仓库 HEAD 差异约 240 行，包含：

- SQLite WAL 竞态修复
- 进程 anchor / `_ClosingConnection`
- 跨进程初始化锁
- PG connect/statement timeout 框架

生产使用 `db_backend=pg`，SQLite 路径不执行。把全部差异一次性带入，会把
未在生产验收的 SQLite 改动和 4F-2C 依赖绑在一起，扩大故障面。

因此：

- 本次只补 `identity_api` 必需的 PG timeout 框架
- 最小补丁约 50 行；已在生产旧 `store.py` 的临时副本上应用并通过
  `py_compile`，未修改生产文件
- SQLite WAL 修复继续走 G-01 / M3 干净安装，不混入本次
- 不修改 `server.py`、`identity.py`、`identity_shadow.py`、配置、Shadow

## 3. 最小补丁内容

### 3.1 导入

生产 `store.py` 当前没有 `contextmanager`，需补：

```python
from contextlib import contextmanager
```

### 3.2 PG timeout 常量与配置解析

放在 `PG_DSN` 之后：

```python
def _cfg_int(env_name: str, cfg_key: str, default: int) -> int:
    raw = os.environ.get(env_name)
    if raw is None:
        raw = CFG.get(cfg_key)
    if raw is None:
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


PG_CONNECT_TIMEOUT = _cfg_int("CORE_DB_CONNECT_TIMEOUT", "db_connect_timeout", 5)
PG_STATEMENT_TIMEOUT_MS = _cfg_int(
    "CORE_DB_STATEMENT_TIMEOUT_MS", "db_statement_timeout_ms", 0)
```

### 3.3 thread-local 上下文

放在 `_lock = threading.RLock()` 之后：

```python
_pg_tls = threading.local()


@contextmanager
def pg_timeouts(*, connect_timeout: int | None = None,
                statement_timeout_ms: int | None = None):
    old = {}
    for key, value in (("connect_timeout", connect_timeout),
                       ("statement_timeout_ms", statement_timeout_ms)):
        old[key] = getattr(_pg_tls, key, None)
        if value is not None:
            setattr(_pg_tls, key, int(value))
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                try:
                    delattr(_pg_tls, key)
                except AttributeError:
                    pass
            else:
                setattr(_pg_tls, key, value)
```

### 3.4 `_PgConnection` 连接参数

替换：

```python
self._c = psycopg.connect(PG_DSN, row_factory=dict_row, autocommit=False)
```

为：

```python
kwargs = {"row_factory": dict_row, "autocommit": False}
connect_timeout = getattr(_pg_tls, "connect_timeout", PG_CONNECT_TIMEOUT)
statement_ms = getattr(_pg_tls, "statement_timeout_ms",
                       PG_STATEMENT_TIMEOUT_MS)
if connect_timeout and int(connect_timeout) > 0:
    kwargs["connect_timeout"] = int(connect_timeout)
if statement_ms and int(statement_ms) > 0:
    kwargs["options"] = f"-c statement_timeout={int(statement_ms)}"
self._c = psycopg.connect(PG_DSN, **kwargs)
```

## 4. 部署步骤

1. 备份：
   - `identity_api.py`
   - `store.py`
2. 先写 `store.py` 最小补丁，再写 `identity_api.py`。
3. 重启 Core。
4. 验证：
   - `import core.store; hasattr(store, "pg_timeouts")`
   - `GET /api/identity/me` 正确 → 200
   - 错误/缺失 Bearer → 401 + `WWW-Authenticate` + `no-store`
   - `/api/capabilities/permissions` hash 不变
   - Shadow `enabled=False`，事件数不增加
   - 生产 PG 只读连接 `SHOW statement_timeout` 为 `5s`
   - `stats_snapshot()` 可读（内部）
5. 失败回滚：
   - 恢复两个文件
   - 重启 Core
   - 再验 Legacy 响应与 Shadow 事件数

## 5. 验收边界

- 不把 SQLite WAL 修复算作本次已验收
- 不把 `statement_timeout` 验证当作故障注入
- 不开启 Shadow
- 不修改生产配置
- 不改变 Legacy 放行

## 6. 待授权

最小补丁部署需要 **单独部署授权**；本文件只定策略，不执行生产修改。
