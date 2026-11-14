# 4F-2C `store.py` 部署策略

- 日期：2026-11-14
- 状态：**历史最小补丁已部署；对含 cancel/stats 的新版 identity_api 已不充分**
- 新依赖：`pg_timeouts(cancel_key=...)` / `cancel_pg()` 不在最小补丁内
- 含 cancel/stats 的版本必须走 G-01 路径 B 或重新裁决补丁，不能直接部署
- 关联：[4F-2C 部署授权](../reports/PHASE4F2C-DEPLOY-AUTHORIZATION.md)
- 关联：[4F-2C 后续问题](2026-11-14-phase4f2c-followups.md)

## 1. 决策

采用 **最小补丁**，不把仓库完整 `store.py` 覆盖到生产。

需要同时部署：

1. `core/identity_api.py`（当前版本）
2. `core/store.py` 的 timeout 最小补丁

`server.py` 已在部署时包含 identity router，本次无需再改。

## 2. 理由

生产旧 `store.py` 与仓库 HEAD 的差异当时约 240 行（当前 patched 版与
仓库 HEAD 为约 `+171/-19`；两个数不能混用），包含：

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

补丁文件：

[2026-11-14-phase4f2c-store-minimal.patch](../patches/2026-11-14-phase4f2c-store-minimal.patch)

- 91 行 diff
- 基于生产旧 `store.py` 生成
- 已用 `git apply --check` + 实际 apply 在临时副本验证
- 应用后 SHA256 前缀 `3A20A70BFA52`，`py_compile` 通过

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
    if raw is None or isinstance(raw, bool):
        return default
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        try:
            value = int(raw.strip(), 10)
        except (TypeError, ValueError, OverflowError):
            return default
    else:
        return default
    if value < 0:
        return default
    return value


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

## 4. 补丁验证证据

在生产旧 `store.py` 的临时副本上应用最小补丁，并只读验证完整路径：

```text
pg_timeouts(statement_timeout_ms=5000)
  -> store.conn()
  -> _PgConnection
  -> psycopg.connect
  -> SHOW statement_timeout
```

结果：

```text
inside  {'statement_timeout': '5s'}
after_context_default None
outside {'statement_timeout': '0'}
```

说明：

- 请求级上下文确实生效
- 上下文退出后 thread-local 恢复
- 普通独立连接回到默认 0
- 验证使用生产 PG 只读连接，事务/连接已关闭，没有写入
- 没有打印 DSN 或凭证

哈希：

| 对象 | SHA256 前缀 |
|---|---|
| 生产旧 `store.py` | `EA5EF74FE23D` |
| 临时 patched `store.py` | `3A20A70BFA52` |
| 本地 `identity_api.py` | `20620DA8E05A` |
| 生产旧 `identity_api.py` | `E3FD63AD5139` |
| 生产 `server.py` | `7F77BB2B77BE` |

临时 patched 文件位于会话临时目录，未提交、未覆盖生产。

## 5. 部署步骤

目标路径：

```text
D:\AI\Mobius\core\identity_api.py
D:\AI\Mobius\core\store.py
```

1. 备份（同一时间戳）：
   - `D:\AI\Mobius\core\identity_api.py.bak-4f2c-<ts>`
   - `D:\AI\Mobius\core\store.py.bak-4f2c-<ts>`
2. 先写 `store.py` 最小补丁，再写 `identity_api.py`。
3. 校验目标文件 SHA256 与部署清单一致。
4. 重启 Core。
5. 验证：
   - `import core.store; hasattr(store, "pg_timeouts")`
   - `GET /api/identity/me` 正确 → 200
   - 错误/缺失 Bearer → 401 + `WWW-Authenticate` + `no-store`
   - `/api/capabilities/permissions` hash 不变
   - Shadow `enabled=False`，事件数不增加
   - 在 `pg_timeouts(statement_timeout_ms=5000)` 上下文内新建连接，
     `SHOW statement_timeout` 为 `5s`
   - 上下文退出后普通连接 `SHOW statement_timeout` 回到默认 `0`
   - `stats_snapshot()` 可读（内部）
6. 失败回滚：
   - 恢复两个 `.bak-4f2c-<ts>` 文件
   - 校验 SHA256 与部署前一致
   - 重启 Core
   - 再验 Legacy 响应与 Shadow 事件数

## 6. 验收边界

- 不把 SQLite WAL 修复算作本次已验收
- 不把 `statement_timeout` 验证当作故障注入
- 不开启 Shadow
- 不修改生产配置
- 不改变 Legacy 放行

## 7. 执行结果

2026-11-14 15:55:44 已按本策略部署：

- 备份：
  - `identity_api.py.bak-4f2c-20261114-155544`
  - `store.py.bak-4f2c-20261114-155544`
- 生产哈希：
  - `identity_api.py` `20620DA8E05A...`
  - `store.py` `3A20A70BFA52...`
- Core 15:56:07 重启成功
- endpoint 200/401、Legacy hash、Shadow 关闭、事件数不变
- PG 只读验证：上下文内 5s、退出后恢复、普通连接 0

部署边界未扩大：未开 Shadow，未改配置，未修改 4F-1/2A/2B 冻结内容。

> 后续：2026-11-14 17:10 G-01 路径 B 已用仓库完整 `store.py`
> （`E7B07A98...`）替换本最小补丁版；本文件保留为历史策略与回滚参考。
