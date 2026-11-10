"""M1a 契约测试：Core 权威任务生命周期（ADR-007）。

覆盖：
  1. 迁移：新库 schema_version=2 且列齐；旧库（原始 16 列）ALTER 补列升级
  2. 创建 / 幂等命中 / 同 key 异 payload 冲突（409 语义）
  3. 事件：seq 幂等去重、状态机合法/非法转移、submission_unknown 独立终态、历史词归一
  4. 取消：cancel_requested、终态 no-op、取消竞态（completed + cancel_requested）
  5. 租约：仅 running 可续约、过期租约置 failed(lease_expired)
  6. API 契约（FastAPI TestClient，回环 testclient 豁免鉴权）：
     POST /v1/tasks、POST /v1/tasks/{id}/events、GET /v1/tasks[/{id}]、POST /v1/tasks/{id}/cancel

隔离：默认临时目录 + 临时 sqlite 库；若显式给 CORE_DB_BACKEND=pg + CORE_DB_DSN（scratch 库，
如 mobius_m1_verify）则在 PG 上跑同一套。绝不连生产库、不打印密钥。

运行：
  D:\\Anaconda3\\envs\\open-webui\\python.exe tests\\test_m1_task_owner.py
  # PG（可选，scratch）：CORE_DB_BACKEND=pg CORE_DB_DSN=postgresql://...mobius_m1_verify ...
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---- 导入 core 之前完成隔离 ----
_BACKEND = os.environ.get("CORE_DB_BACKEND", "sqlite").strip().lower()
_USE_PG = _BACKEND == "pg" and bool(os.environ.get("CORE_DB_DSN"))
_TMP = Path(tempfile.mkdtemp(prefix="m1-owner-"))
if _USE_PG:
    # 用调用方提供的 scratch PG；仅清掉会命中生产 secrets 的回退，不改 DSN
    os.environ.setdefault("MOBIUS_SECRETS_FILE", str(_TMP / "absent-secrets.json"))
else:
    _cfg = _TMP / "config.json"
    _cfg.write_text(json.dumps({"db": (_TMP / "core.db").as_posix(), "db_backend": "sqlite"}),
                    encoding="utf-8")
    os.environ["MOBIUS_CONFIG"] = str(_cfg)
    os.environ["MOBIUS_SECRETS_FILE"] = str(_TMP / "absent-secrets.json")
    os.environ["CORE_DB_BACKEND"] = "sqlite"
    os.environ.pop("CORE_DB_DSN", None)

sys.path.insert(0, str(ROOT))
from core import store  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


# ---------------------------------------------------------------- 1. 迁移
def test_migration_fresh() -> None:
    print(f"\n[1] 迁移（新库，backend={store.DB_BACKEND}）")
    with store.conn() as c:
        ver = c.execute("SELECT v FROM schema_meta WHERE k='schema_version'").fetchone()
        cols = store._table_columns(c, "tasks")
    check("schema_version=2", ver is not None and str(ver["v"]) == "2", str(ver and ver["v"]))
    need = ("idempotency_key", "payload_hash", "payload_json", "execution_id", "error_code",
            "attempts", "lease_owner", "lease_expires_at", "cancel_requested", "version",
            "last_event_seq")
    missing = [x for x in need if x not in cols]
    check("权威字段列齐全", not missing, f"缺={missing}")


def test_migration_from_old() -> None:
    print("\n[2] 迁移（旧库升级：原始 16 列 → v2）")
    if store.DB_BACKEND == "pg":
        # 在 scratch PG 上建一张旧结构表，验证 _migrate_tasks 的 PG 分支
        import psycopg
        with psycopg.connect(os.environ["CORE_DB_DSN"], autocommit=True) as con:
            con.execute("DROP TABLE IF EXISTS tasks_old")
            con.execute("""CREATE TABLE tasks_old(
                id TEXT PRIMARY KEY, kind TEXT, status TEXT, source TEXT, user_id TEXT,
                session_id TEXT, character TEXT, prompt TEXT, params TEXT, result TEXT,
                error TEXT, remote_id TEXT, created_at REAL, started_at REAL,
                finished_at REAL, updated_at REAL)""")
            con.execute("INSERT INTO tasks_old(id,kind,status,created_at,updated_at) "
                        "VALUES('old1','image','succeeded',0,0)")
        # 用 store 的连接适配器跑迁移（针对 tasks_old 需临时改表名，这里直接验证列探测+ALTER 语义）
        with store.conn() as c:
            have = store._table_columns(c, "tasks_old")
            for col, decl in store.TASKS_MIGRATION_COLS:
                if col not in have:
                    c.execute(f"ALTER TABLE tasks_old ADD COLUMN {col} {decl}")
            after = store._table_columns(c, "tasks_old")
        check("PG 旧表 ALTER 补列成功",
              all(col in after for col, _ in store.TASKS_MIGRATION_COLS))
        return

    old = _TMP / "old.db"
    con = sqlite3.connect(str(old))
    con.row_factory = sqlite3.Row  # 与 store.conn() 一致，_table_columns 按键取列名
    con.execute("""CREATE TABLE tasks(
        id TEXT PRIMARY KEY, kind TEXT, status TEXT, source TEXT, user_id TEXT,
        session_id TEXT, character TEXT, prompt TEXT, params TEXT, result TEXT,
        error TEXT, remote_id TEXT, created_at REAL, started_at REAL,
        finished_at REAL, updated_at REAL)""")
    con.execute("INSERT INTO tasks(id,kind,status,created_at,updated_at) "
                "VALUES('old1','image','succeeded',0,0)")
    con.commit()
    before = {r[1] for r in con.execute("PRAGMA table_info(tasks)").fetchall()}
    check("旧库初始为 16 列（无 idempotency_key）", "idempotency_key" not in before,
          f"{len(before)} 列")
    store._migrate_tasks(con)  # 直接对旧库连接跑迁移
    con.commit()
    after = {r[1] for r in con.execute("PRAGMA table_info(tasks)").fetchall()}
    ver = con.execute("SELECT v FROM schema_meta WHERE k='schema_version'").fetchone()
    check("旧库 ALTER 补齐权威字段",
          all(col in after for col, _ in store.TASKS_MIGRATION_COLS), f"缺={[c for c,_ in store.TASKS_MIGRATION_COLS if c not in after]}")
    check("旧库升级后 schema_version=2", ver is not None and ver[0] == "2")
    # 既有行保留、新列默认值
    row = con.execute("SELECT status, version, attempts, cancel_requested FROM tasks WHERE id='old1'").fetchone()
    check("既有行保留且新列有默认值", row[0] == "succeeded" and row[1] == 0 and row[3] == 0,
          str(row))
    # 幂等：再跑一次不报错、不重复加列
    store._migrate_tasks(con); con.commit()
    after2 = {r[1] for r in con.execute("PRAGMA table_info(tasks)").fetchall()}
    check("迁移幂等（重复执行列数不变）", after2 == after)
    con.close()


# ---------------------------------------------------------------- 3. 创建/幂等/冲突
def test_create_idempotent() -> None:
    print("\n[3] 创建 / 幂等命中 / 冲突")
    t1, h1 = store.task_create_authoritative(kind="image", payload={"prompt": "cat"},
                                             idempotency_key="mk1", source="qq",
                                             user_id="u1", session_id="s1", character="bronya")
    check("首次创建非命中，状态 pending", h1 is False and t1["status"] == "pending",
          f"hit={h1} status={t1['status']}")
    check("task_id 由 Core 分配", bool(t1["id"]) and t1["id"].startswith("t_"), t1["id"])
    check("payload 落库可读回", t1.get("payload") == {"prompt": "cat"}, str(t1.get("payload")))

    t2, h2 = store.task_create_authoritative(kind="image", payload={"prompt": "cat"},
                                             idempotency_key="mk1", source="qq")
    check("同 key 同 payload → 命中原任务", h2 is True and t2["id"] == t1["id"])

    # payload 字段顺序无关
    t3, h3 = store.task_create_authoritative(kind="image", payload={"a": 1, "b": 2},
                                             idempotency_key="mk-order")
    t4, h4 = store.task_create_authoritative(kind="image", payload={"b": 2, "a": 1},
                                             idempotency_key="mk-order")
    check("payload 字段顺序不影响幂等", h4 is True and t4["id"] == t3["id"])

    try:
        store.task_create_authoritative(kind="image", payload={"prompt": "dog"},
                                        idempotency_key="mk1")
        check("同 key 异 payload → TaskConflictError", False, "未抛异常")
    except store.TaskConflictError as exc:
        check("同 key 异 payload → TaskConflictError", exc.task_id == t1["id"])

    t5, h5 = store.task_create_authoritative(kind="image", payload={"prompt": "x"})
    check("无 idempotency_key → 每次新建", h5 is False and t5["id"] != t1["id"])


# ---------------------------------------------------------------- 4. 事件/状态机
def test_events_state_machine() -> None:
    print("\n[4] 事件：seq 幂等 + 状态机 + submission_unknown + 历史词归一")
    t, _ = store.task_create_authoritative(kind="image", payload={"p": 1}, idempotency_key="ev1")
    tid = t["id"]
    r = store.task_apply_authoritative_event(tid, event="running", seq=1, execution_id="prompt_9",
                                             lease_owner="gw", lease_expires_at=store.now() + 60)
    check("pending→running 合法", r["status"] == "running")
    g = store.task_get(tid)
    check("execution_id/started_at 落库", g["execution_id"] == "prompt_9" and g["started_at"],
          f"exec={g['execution_id']}")

    dup = store.task_apply_authoritative_event(tid, event="completed", seq=1)
    check("seq<=last 被去重（不推进状态）", dup.get("duplicate") is True
          and store.task_get(tid)["status"] == "running", str(dup))

    r2 = store.task_apply_authoritative_event(tid, event="succeeded", seq=2,
                                              result={"filename": "x.png"})
    check("历史词 succeeded 归一为 completed", r2["status"] == "completed"
          and store.task_get(tid)["status"] == "completed")
    check("result 落库", store.task_get(tid)["result"] == {"filename": "x.png"})

    try:
        store.task_apply_authoritative_event(tid, event="running", seq=3)
        check("终态后非法转移被拒", False, "未抛异常")
    except store.IllegalTransitionError as exc:
        check("终态后非法转移被拒", exc.src == "completed" and exc.dst == "running")

    # submission_unknown 独立终态 + 仅可转 failed
    t2, _ = store.task_create_authoritative(kind="image", payload={"p": 2}, idempotency_key="ev2")
    store.task_apply_authoritative_event(t2["id"], event="running", seq=1)
    store.task_apply_authoritative_event(t2["id"], event="submission_unknown", seq=2,
                                         error_code="submission_unknown")
    check("submission_unknown 是终态", store.task_get(t2["id"])["status"] == "submission_unknown")
    try:
        store.task_apply_authoritative_event(t2["id"], event="completed", seq=3)
        check("submission_unknown→completed 非法", False, "未抛异常")
    except store.IllegalTransitionError:
        check("submission_unknown→completed 非法（禁止自动重提）", True)
    store.task_apply_authoritative_event(t2["id"], event="failed", seq=4,
                                         error_code="submission_unknown_timeout")
    check("submission_unknown→failed 合法（24h 超时）",
          store.task_get(t2["id"])["status"] == "failed"
          and store.task_get(t2["id"])["error_code"] == "submission_unknown_timeout")

    # 任务不存在
    try:
        store.task_apply_authoritative_event("t_nope", event="running", seq=1)
        check("未知任务事件 → KeyError", False, "未抛异常")
    except KeyError:
        check("未知任务事件 → KeyError（API 映射 404）", True)


# ---------------------------------------------------------------- 5. 取消
def test_cancel() -> None:
    print("\n[5] 取消：cancel_requested + 终态 no-op + 竞态")
    t, _ = store.task_create_authoritative(kind="image", payload={"p": 3}, idempotency_key="c1")
    store.task_apply_authoritative_event(t["id"], event="running", seq=1)
    res = store.task_request_cancel(t["id"])
    check("running 取消 → cancel_requested=1，状态仍 running",
          res.get("cancel_requested") is True and store.task_get(t["id"])["status"] == "running")
    # 完成先到：completed + cancel_requested 保留（不引入第二终态）
    store.task_apply_authoritative_event(t["id"], event="completed", seq=2, result={"f": "y.png"})
    g = store.task_get(t["id"])
    check("取消竞态：completed + cancel_requested=true",
          g["status"] == "completed" and int(g["cancel_requested"]) == 1, f"{g['status']}/{g['cancel_requested']}")
    res2 = store.task_request_cancel(t["id"])
    check("终态再取消 → no-op", "note" in res2 and g["status"] == "completed", str(res2))


# ---------------------------------------------------------------- 6. 租约
def test_lease() -> None:
    print("\n[6] 租约：续约 + 过期置 failed(lease_expired)")
    t, _ = store.task_create_authoritative(kind="image", payload={"p": 4}, idempotency_key="l1")
    r0 = store.task_renew_lease(t["id"], "gw", 60)
    check("pending 不可续约（仅 running）", r0.get("ok") is False and r0.get("reason") == "not_running")
    store.task_apply_authoritative_event(t["id"], event="running", seq=1,
                                         lease_owner="gw", lease_expires_at=store.now() + 60)
    r1 = store.task_renew_lease(t["id"], "gw", 60)
    check("running 可续约", r1.get("ok") is True and r1["lease_expires_at"] > store.now())

    # 造一个已过期租约的 running 任务
    t2, _ = store.task_create_authoritative(kind="image", payload={"p": 5}, idempotency_key="l2")
    store.task_apply_authoritative_event(t2["id"], event="running", seq=1,
                                         lease_owner="gw", lease_expires_at=store.now() - 10)
    expired = store.task_expire_stale_leases()
    g2 = store.task_get(t2["id"])
    check("过期租约 running → failed(lease_expired)",
          t2["id"] in expired and g2["status"] == "failed" and g2["error_code"] == "lease_expired",
          f"expired={expired} status={g2['status']} code={g2['error_code']}")
    check("未过期任务不受影响", store.task_get(t["id"])["status"] == "running")


# ---------------------------------------------------------------- 7. API 契约
def test_api_contract() -> None:
    print("\n[7] API 契约（FastAPI TestClient，回环豁免鉴权）")
    from fastapi.testclient import TestClient
    from core import server as coreserver
    client = TestClient(coreserver.app)  # 不用 with，避免触发 lifespan（拉起服务）

    r = client.post("/v1/tasks", json={"kind": "image", "payload": {"prompt": "api-cat"},
                                       "idempotency_key": "api1", "source": "qq",
                                       "user_id": "u", "session_id": "s", "character": "bronya"})
    check("POST /v1/tasks 创建 200", r.status_code == 200 and r.json()["idempotent_hit"] is False,
          f"{r.status_code} {r.text[:120]}")
    tid = r.json()["task_id"]
    check("返回 status=pending（规范词）", r.json()["status"] == "pending")

    r2 = client.post("/v1/tasks", json={"kind": "image", "payload": {"prompt": "api-cat"},
                                        "idempotency_key": "api1", "source": "qq"})
    check("同 key 同 payload → idempotent_hit=true 同 task",
          r2.json()["idempotent_hit"] is True and r2.json()["task_id"] == tid)

    r3 = client.post("/v1/tasks", json={"kind": "image", "payload": {"prompt": "api-dog"},
                                        "idempotency_key": "api1"})
    check("同 key 异 payload → 409", r3.status_code == 409 and r3.json()["error"] == "idempotency_conflict",
          f"{r3.status_code}")

    r4 = client.post("/v1/tasks", json={"kind": "bogus", "payload": {}})
    check("非法 kind → 400", r4.status_code == 400)

    re1 = client.post(f"/v1/tasks/{tid}/events",
                      json={"event": "running", "seq": 1, "execution_id": "p1"})
    check("POST events running → 200", re1.status_code == 200 and re1.json()["status"] == "running")
    re2 = client.post(f"/v1/tasks/{tid}/events", json={"event": "completed", "seq": 1})
    check("events seq 重放 → duplicate", re2.json().get("duplicate") is True)
    re3 = client.post(f"/v1/tasks/{tid}/events",
                      json={"event": "completed", "seq": 2, "result": {"f": "z.png"}})
    check("events completed → 200", re3.status_code == 200 and re3.json()["status"] == "completed")
    re4 = client.post(f"/v1/tasks/{tid}/events", json={"event": "running", "seq": 3})
    check("events 非法转移 → 409", re4.status_code == 409 and re4.json()["error"] == "illegal_transition")
    re5 = client.post("/v1/tasks/t_nope/events", json={"event": "running", "seq": 1})
    check("events 未知任务 → 404", re5.status_code == 404)

    rg = client.get(f"/v1/tasks/{tid}")
    check("GET /v1/tasks/{id} → 200 且 status 规范词",
          rg.status_code == 200 and rg.json()["status"] == "completed")
    rl = client.get("/v1/tasks", params={"limit": 5})
    check("GET /v1/tasks 列表", rl.status_code == 200 and isinstance(rl.json()["tasks"], list))
    r404 = client.get("/v1/tasks/t_nope")
    check("GET 未知任务 → 404", r404.status_code == 404)

    # cancel：新任务 running 后取消
    rc0 = client.post("/v1/tasks", json={"kind": "speech", "payload": {"text": "hi"},
                                         "idempotency_key": "api-cancel"})
    cid = rc0.json()["task_id"]
    client.post(f"/v1/tasks/{cid}/events", json={"event": "running", "seq": 1})
    rc = client.post(f"/v1/tasks/{cid}/cancel")
    check("POST /v1/tasks/{id}/cancel → cancel_requested",
          rc.status_code == 200 and rc.json().get("cancel_requested") is True)
    rc404 = client.post("/v1/tasks/t_nope/cancel")
    check("cancel 未知任务 → 404", rc404.status_code == 404)


def main() -> int:
    print(f"==> M1a Core 权威任务契约测试（backend={store.DB_BACKEND}）")
    test_migration_fresh()
    test_migration_from_old()
    test_create_idempotent()
    test_events_state_machine()
    test_cancel()
    test_lease()
    test_api_contract()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
