"""T1 回归：Gateway(ADR-002) → Core 状态词映射 + submission_unknown 独立终态语义。

覆盖 Core 三处消费点的共同基座 core/store.py：
  - map_gateway_status：网关词 → Core 词，未知词原样保留（不静默吞）
  - _GW_STATUS：submission_unknown 是独立终态，不得折叠成可重试的 failed
  - task_apply_event：outbox 事件投影 + seq 去重

隔离：导入前把配置指向临时 sqlite 库、secrets 指向不存在的临时文件，
绝不连接生产 PG（CORE_DB_BACKEND=sqlite + 临时 db 路径）。

运行：
  D:\\Anaconda3\\envs\\open-webui\\python.exe tests\\test_t1_status_vocab.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---- 导入 core.store 之前完成隔离（store 在 import 期即 init_db）----
_TMP = Path(tempfile.mkdtemp(prefix="t1-status-"))
_cfg = _TMP / "config.json"
_cfg.write_text(
    json.dumps({"db": (_TMP / "t1.db").as_posix(), "db_backend": "sqlite"}),
    encoding="utf-8",
)
os.environ["MOBIUS_CONFIG"] = str(_cfg)
os.environ["MOBIUS_SECRETS_FILE"] = str(_TMP / "absent-secrets.json")  # 不存在 -> 不加载任何密钥
os.environ["CORE_DB_BACKEND"] = "sqlite"
os.environ.pop("CORE_DB_DSN", None)
sys.path.insert(0, str(ROOT))

from core import store  # noqa: E402

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def test_mapping() -> None:
    print("\n[1] map_gateway_status：ADR-002 网关词 → Core 词")
    cases = {
        "pending": "queued",
        "running": "running",
        "completed": "succeeded",
        "failed": "failed",
        "cancelled": "canceled",
        "canceled": "canceled",
        "submission_unknown": "submission_unknown",
    }
    for gw_word, core_word in cases.items():
        got = store.map_gateway_status(gw_word)
        check(f"{gw_word} → {core_word}", got == core_word, f"got={got!r}")
    check("未知词原样保留（便于排查，不静默吞）", store.map_gateway_status("weird") == "weird")
    check("空串原样", store.map_gateway_status("") == "")
    check("None 容错为空串", store.map_gateway_status(None) == "")


def test_submission_unknown_invariant() -> None:
    print("\n[2] submission_unknown 独立终态语义")
    check("_GW_STATUS[submission_unknown] == submission_unknown",
          store._GW_STATUS.get("submission_unknown") == "submission_unknown")
    check("submission_unknown 不折叠成 failed",
          store.map_gateway_status("submission_unknown") != store.map_gateway_status("failed"))
    check("cancelled(双l)/canceled(单l) 同归 canceled",
          store.map_gateway_status("cancelled") == store.map_gateway_status("canceled") == "canceled")


def test_projection() -> None:
    print("\n[3] task_apply_event：网关事件投影到 Core 任务表（隔离 sqlite）")
    ev1 = {"seq": 101, "payload": {"task_id": "g_t1a", "kind": "image",
                                   "status": "completed", "source": "qq",
                                   "result": {"filename": "x.png"}}}
    store.task_apply_event(ev1)
    rec1 = store.task_get("g_t1a")
    check("completed 事件 → Core succeeded",
          rec1 is not None and rec1["status"] == "succeeded",
          f"status={rec1 and rec1['status']}")

    ev2 = {"seq": 102, "payload": {"task_id": "g_t1b", "kind": "image",
                                   "status": "submission_unknown", "source": "qq",
                                   "error_message": "提交超时"}}
    store.task_apply_event(ev2)
    rec2 = store.task_get("g_t1b")
    check("submission_unknown 事件 → Core submission_unknown",
          rec2 is not None and rec2["status"] == "submission_unknown",
          f"status={rec2 and rec2['status']}")

    dup = store.task_apply_event({"seq": 102, "payload": {"task_id": "g_t1b", "status": "completed"}})
    after = store.task_get("g_t1b")
    check("旧 seq 重放被去重，不覆盖既有终态",
          dup.get("duplicate") is True and after["status"] == "submission_unknown",
          f"dup={dup} status={after['status']}")

    ev3 = {"seq": 103, "payload": {"task_id": "g_t1c", "kind": "image", "status": "running"}}
    store.task_apply_event(ev3)
    check("running 事件 → Core running", store.task_get("g_t1c")["status"] == "running")


def main() -> int:
    print("==> T1 状态词映射回归（ADR-002 → Core）")
    test_mapping()
    test_submission_unknown_invariant()
    test_projection()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
