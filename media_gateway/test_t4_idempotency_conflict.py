"""T4 回归：幂等键冲突处理。

契约：
  - 同 idempotency_key + 同 payload → 命中既有任务（hit=True），不重复建。
  - 同 idempotency_key + 异 payload → TaskConflict（409 语义）。
  - 异 key → 新任务。
  - pg 端并发：同 key 穿过前置 SELECT 撞唯一索引 UniqueViolation 时，回滚重查后
    仍按「同 payload 命中 / 异 payload 冲突 / 无行重抛」处理（T4 新增 catch 块）。

隔离：
  - sqlite 端：临时库真实集成。
  - pg 端：PgTaskStore.__new__ 绕过会连库的 __init__，monkeypatch _connect/get，
    用假连接驱动 UniqueViolation 分支，绝不连生产 PG。
  - 另含 schema 静态检查：唯一索引 ux_task_idem_key 存在。

运行：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe media_gateway\\test_t4_idempotency_conflict.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg  # noqa: E402
import pg_taskstore as pgmod  # noqa: E402
from taskstore import DurableTaskStore, TaskConflict  # noqa: E402
from pg_taskstore import PgTaskStore  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


# ---------------------------------------------------------------- sqlite 端
def test_sqlite_idempotency() -> None:
    print("\n[1] sqlite submit 幂等/冲突（临时库真实集成）")
    tmp = tempfile.mkdtemp(prefix="t4-sqlite-")
    store = DurableTaskStore(os.path.join(tmp, "t4.db"))

    rec1, hit1 = store.submit(kind="image", payload={"prompt": "cat"}, spec={},
                              idempotency_key="k1")
    check("首次提交非命中", hit1 is False, f"hit={hit1}")

    rec2, hit2 = store.submit(kind="image", payload={"prompt": "cat"}, spec={},
                              idempotency_key="k1")
    check("同 key 同 payload → 命中既有", hit2 is True and rec2.id == rec1.id,
          f"hit={hit2} id={rec2.id} vs {rec1.id}")

    try:
        store.submit(kind="image", payload={"prompt": "dog"}, spec={}, idempotency_key="k1")
        check("同 key 异 payload → TaskConflict", False, "未抛异常")
    except TaskConflict as exc:
        check("同 key 异 payload → TaskConflict", exc.task_id == rec1.id,
              f"task_id={exc.task_id}")

    rec3, hit3 = store.submit(kind="image", payload={"prompt": "cat"}, spec={},
                              idempotency_key="k2")
    check("异 key → 新任务", hit3 is False and rec3.id != rec1.id, f"id={rec3.id}")


# ---------------------------------------------------------------- pg 端（mock UniqueViolation）
class _FakeCursor:
    """前置 SELECT 返回 None（模拟并发穿过）；INSERT 抛 UniqueViolation。"""

    def __init__(self, con: "_FakeConn") -> None:
        self._con = con

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *a) -> bool:
        return False

    def execute(self, sql: str, args: tuple = ()):
        if "INSERT" in sql.upper():
            raise psycopg.errors.UniqueViolation("23505 duplicate key (idempotency_key)")
        return self  # 前置 SELECT

    def fetchone(self):
        return None  # 前置 SELECT 无行 -> 走到 INSERT


class _FakeRequery:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """except 分支里的 con.execute(重查) 按 scenario 返回行。"""

    def __init__(self, scenario: str, match_hash: str) -> None:
        self._scenario = scenario
        self._match_hash = match_hash
        self.rolled_back = False
        self.committed = False

    def __enter__(self) -> "_FakeConn":
        return self

    def __exit__(self, *a) -> bool:
        return False

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def rollback(self) -> None:
        self.rolled_back = True

    def commit(self) -> None:
        self.committed = True

    def execute(self, sql: str, args: tuple = ()):
        if self._scenario == "none":
            return _FakeRequery(None)
        if self._scenario == "match":
            return _FakeRequery({"task_id": "g_existing", "payload_hash": self._match_hash})
        return _FakeRequery({"task_id": "g_existing", "payload_hash": "different-hash"})


def _make_pg(scenario: str, payload: dict) -> PgTaskStore:
    pg = PgTaskStore.__new__(PgTaskStore)  # 绕过 __init__/ensure_schema（会连库）
    pg._dsn = "postgresql://unused"
    pg._outbox_limit = 500
    phash = pgmod.canonical_hash(payload)
    pg._connect = lambda: _FakeConn(scenario, phash)  # type: ignore[method-assign]
    sentinel = {"task_id": "g_existing", "status": "pending"}
    pg.get = lambda tid: sentinel  # type: ignore[method-assign]
    return pg


def test_pg_unique_violation() -> None:
    print("\n[2] pg submit UniqueViolation 分支（假连接 mock，不连 PG）")
    payload = {"prompt": "cat"}

    pg_match = _make_pg("match", payload)
    rec, hit = pg_match.submit(kind="image", payload=payload, spec={}, idempotency_key="k1")
    check("并发撞唯一索引 + 同 payload → 命中既有（hit=True）", hit is True,
          f"hit={hit} rec={rec}")

    pg_conf = _make_pg("conflict", payload)
    try:
        pg_conf.submit(kind="image", payload=payload, spec={}, idempotency_key="k1")
        check("并发撞唯一索引 + 异 payload → TaskConflict", False, "未抛异常")
    except TaskConflict:
        check("并发撞唯一索引 + 异 payload → TaskConflict", True)

    pg_none = _make_pg("none", payload)
    try:
        pg_none.submit(kind="image", payload=payload, spec={}, idempotency_key="k1")
        check("重查无行（task_id 主键冲突）→ 重抛 UniqueViolation", False, "未抛异常")
    except psycopg.errors.UniqueViolation:
        check("重查无行（task_id 主键冲突）→ 重抛 UniqueViolation", True)
    except TaskConflict:
        check("重查无行（task_id 主键冲突）→ 重抛 UniqueViolation", False, "误抛 TaskConflict")


def test_schema_static() -> None:
    print("\n[3] schema 静态检查")
    check("pg SCHEMA 含唯一索引 ux_task_idem_key(idempotency_key)",
          "ux_task_idem_key" in pgmod.SCHEMA
          and "UNIQUE INDEX" in pgmod.SCHEMA
          and "idempotency_key" in pgmod.SCHEMA)
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pg_taskstore.py"),
               encoding="utf-8").read()
    check("pg submit 捕获 psycopg.errors.UniqueViolation",
          "except psycopg.errors.UniqueViolation" in src)


def main() -> int:
    print("==> T4 幂等键冲突回归")
    test_sqlite_idempotency()
    test_pg_unique_violation()
    test_schema_static()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
