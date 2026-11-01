"""Pipeline 配置守卫：防止提示词被静默覆盖。

背景
----
2026-11-01 15:54（SQLite 时期）`legacy_pipelines.config` 曾被改成 1284 字符默认模板，
导致 P0.4 提示词丢失。迁移 PG 后已恢复，本守卫用于**及时发现再次被改**。

用法
----
  python ops/pipeline_guard.py --snapshot   # 以当前配置为基准（人工确认后执行）
  python ops/pipeline_guard.py --check      # 与基准比对，漂移时 exit 1 并打印差异
  python ops/pipeline_guard.py --show       # 显示当前指纹

说明
----
- 只读 PG，不修改任何配置
- 基准文件：docs/prompts/pipeline-guard.json（入库）
- 只对提示词 hash 敏感；其他 pipeline 字段变化会单独提示但不判失败
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import psycopg

DSN = os.environ.get(
    "MOBIUS_PIPELINE_DSN",
    "postgresql://postgres:" + os.environ.get("PG_PASSWORD", "") + "@127.0.0.1:55432/langbot",
)
RUNNER = "plugin:langbot-team/LocalAgent/default"
GUARD_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "prompts", "pipeline-guard.json"
)


def current() -> dict:
    with psycopg.connect(DSN) as con:
        row = con.execute("select uuid, config, updated_at from legacy_pipelines").fetchone()
    uuid, cfg, updated = row
    if isinstance(cfg, str):
        cfg = json.loads(cfg)
    prompt = cfg["ai"]["runner_config"][RUNNER]["prompt"]
    text = prompt[0].get("content", "")
    return {
        "pipeline_uuid": uuid,
        "prompt_role": prompt[0].get("role"),
        "prompt_len": len(text),
        "prompt_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "exception_handling": cfg.get("output", {}).get("misc", {}).get("exception-handling"),
        "updated_at": str(updated),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    cur = current()
    if args.show or not (args.snapshot or args.check):
        print(json.dumps(cur, ensure_ascii=False, indent=2))
        return 0

    if args.snapshot:
        os.makedirs(os.path.dirname(GUARD_FILE), exist_ok=True)
        with open(GUARD_FILE, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, ensure_ascii=False, indent=2)
        print(f"已建立基准: {GUARD_FILE}")
        print(json.dumps(cur, ensure_ascii=False, indent=2))
        return 0

    # --check
    if not os.path.exists(GUARD_FILE):
        print("⚠️ 无基准文件，请先 --snapshot")
        return 2
    with open(GUARD_FILE, encoding="utf-8") as fh:
        base = json.load(fh)
    drift = {
        k: {"基准": base.get(k), "当前": cur.get(k)}
        for k in ("prompt_role", "prompt_len", "prompt_sha256", "exception_handling")
        if base.get(k) != cur.get(k)
    }
    if drift:
        print("❌ 提示词/pipeline 配置发生漂移：")
        print(json.dumps(drift, ensure_ascii=False, indent=2))
        return 1
    print(f"✅ 一致（prompt_len={cur['prompt_len']}, sha256={cur['prompt_sha256'][:16]}…）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
