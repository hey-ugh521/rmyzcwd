"""为 LangBot 的每个发送者（QQ 号）自动创建 PowerContext Scope 并绑定。

原理：PowerContext 插件的 speaker 模式用 sha256(bot_uuid + "\\0" + sender_id) 作为
external_id 去解析 Scope；本脚本按同样算法预先创建 Scope + 绑定，实现
"QQ 号 = 独立账号/独立记忆"。

运行：D:\\Anaconda3\\envs\\open-webui\\python.exe ops\\powercontext\\provision.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

import psycopg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import credentials  # noqa: E402

DSN = os.environ.get("LANGBOT_DSN") or credentials.pg_dsn("langbot")
SERVER = os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000")


def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        SERVER + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            payload = {}
        return exc.code, payload


def binding_key(bot_uuid: str, sender_id: str) -> dict:
    external_id = hashlib.sha256(f"{bot_uuid}\0{sender_id}".encode("utf-8")).hexdigest()
    return {"integration": "langbot", "kind": "speaker", "external_id": external_id}


def resolve(bot_uuid: str, sender_id: str) -> str | None:
    status, payload = call(
        "POST", "/v1/scope-bindings/resolve",
        {"allow_default": False, "explicit_scope_id": None, "binding_keys": [binding_key(bot_uuid, sender_id)]},
    )
    if status == 200:
        return payload.get("scope_id")
    return None


def provision(bot_uuid: str, sender_id: str, display: str) -> str | None:
    scope_id = resolve(bot_uuid, sender_id)
    if scope_id:
        return scope_id
    status, payload = call(
        "POST", "/v1/scopes",
        {
            "title": f"QQ {display or sender_id}",
            "summary": f"LangBot 用户 {sender_id} 的独立上下文与记忆",
            "idempotency_key": f"langbot-speaker-{bot_uuid}-{sender_id}",
        },
    )
    if status not in (200, 201):
        return None
    scope_id = payload.get("scope_id")
    if not scope_id:
        return None
    status, _ = call(
        "PUT", "/v1/scope-bindings",
        {"key": binding_key(bot_uuid, sender_id), "scope_id": scope_id},
    )
    if status not in (200, 201):
        return None
    apply_preset_profile(scope_id, sender_id)
    return scope_id


PRESETS = r"C:\Users\lenovo\Desktop\_说明文档\mobius\config\account_profiles.json"


def apply_preset_profile(scope_id: str, sender_id: str) -> None:
    """新开通账号时写入预设画像（default + 该 QQ 的专属条目）。"""
    try:
        config = json.load(open(PRESETS, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    items = list(config.get("default") or []) + list((config.get("accounts") or {}).get(sender_id) or [])
    if not items:
        return
    status, payload = call("POST", "/v1/memory/entries/list", {"scope_id": scope_id})
    existing = {e.get("text", "").strip() for e in (payload.get("entries") or [])} if status == 200 else set()
    for text in items:
        text = (text or "").strip()
        if not text or text in existing:
            continue
        call("POST", "/v1/memory/remember",
             {"scope_id": scope_id, "kind": "profile", "text": text, "reason": "preset-profile"})


def main() -> int:
    with psycopg.connect(DSN, autocommit=True) as conn:
        rows = conn.execute(
            """
            select bot_id, user_id, max(user_name) as name
            from monitoring_messages
            where user_id is not null and user_id <> '' and bot_id is not null
            group by bot_id, user_id
            """
        ).fetchall()

    created = 0
    existing = 0
    failed = 0
    for bot_id, user_id, name in rows:
        scope = resolve(bot_id, user_id) if False else None  # resolve called inside provision
        try:
            before = resolve(bot_id, user_id)
            scope = before or provision(bot_id, user_id, name or "")
            if scope:
                if before:
                    existing += 1
                else:
                    created += 1
                    print(f"  + QQ {user_id} ({name}) -> scope {scope}")
            else:
                failed += 1
                print(f"  ! QQ {user_id} 开通失败")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ! QQ {user_id} 异常: {type(exc).__name__}: {exc}")
    print(f" 完成：新建 {created}，已存在 {existing}，失败 {failed}（共 {len(rows)} 个发送者）")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
