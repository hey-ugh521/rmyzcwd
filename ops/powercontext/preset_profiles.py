"""给每个 QQ 号预设画像（写入其 PowerContext 独立 Scope）。

画像来源：config/account_profiles.json
- accounts["<QQ号>"]：该账号的画像条目
- default：可选，对所有已开通账号生效的公共条目

运行：D:\\Anaconda3\\envs\\open-webui\\python.exe ops\\powercontext\\preset_profiles.py [--default]
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
PROFILES = r"C:\Users\lenovo\Desktop\_说明文档\mobius\config\account_profiles.json"


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


def external_id(bot_uuid: str, sender_id: str) -> str:
    return hashlib.sha256(f"{bot_uuid}\0{sender_id}".encode("utf-8")).hexdigest()


def resolve_scope(bot_uuids: list[str], sender_id: str) -> str | None:
    keys = [
        {"integration": "langbot", "kind": "speaker", "external_id": external_id(b, sender_id)}
        for b in bot_uuids
    ]
    status, payload = call(
        "POST", "/v1/scope-bindings/resolve",
        {"allow_default": False, "explicit_scope_id": None, "binding_keys": keys},
    )
    if status == 200:
        return payload.get("scope_id")
    return None


def list_texts(scope_id: str) -> set[str]:
    status, payload = call("POST", "/v1/memory/entries/list", {"scope_id": scope_id})
    if status != 200:
        return set()
    return {entry.get("text", "").strip() for entry in payload.get("entries", [])}


def remember(scope_id: str, text: str, reason: str) -> bool:
    status, _ = call(
        "POST", "/v1/memory/remember",
        {"scope_id": scope_id, "kind": "profile", "text": text, "reason": reason},
    )
    return status == 200


def create_scope_and_bind(bot_uuid: str, sender_id: str) -> str | None:
    """账号尚未出现在 LangBot 消息里时，也可预先开通 Scope。"""
    status, payload = call(
        "POST", "/v1/scopes",
        {
            "title": f"QQ {sender_id}",
            "summary": f"LangBot 用户 {sender_id} 的独立上下文与记忆",
            "idempotency_key": f"langbot-speaker-{bot_uuid}-{sender_id}",
        },
    )
    if status not in (200, 201):
        return None
    scope_id = payload.get("scope_id")
    if not scope_id:
        return None
    key = {"integration": "langbot", "kind": "speaker", "external_id": external_id(bot_uuid, sender_id)}
    status, _ = call("PUT", "/v1/scope-bindings", {"key": key, "scope_id": scope_id})
    return scope_id if status in (200, 201) else None


def main() -> int:
    apply_default = "--default" in sys.argv
    config = json.load(open(PROFILES, encoding="utf-8"))
    default_items = config.get("default") or []
    accounts = config.get("accounts") or {}

    with psycopg.connect(DSN, autocommit=True) as conn:
        bot_uuids = [r[0] for r in conn.execute("select uuid from bots").fetchall()]
        rows = conn.execute(
            """
            select bot_id, user_id, max(user_name)
            from monitoring_messages
            where user_id is not null and user_id <> '' and bot_id is not null
            group by bot_id, user_id
            """
        ).fetchall()

    known = {user_id for _, user_id, _ in rows}
    with psycopg.connect(DSN, autocommit=True) as conn:
        bot_row = conn.execute(
            "select uuid from bots where name like '%群友%' limit 1"
        ).fetchone()
        primary_bot = bot_row[0] if bot_row else (bot_uuids[0] if bot_uuids else None)

    targets: dict[str, list[str]] = {}
    for qq, items in accounts.items():
        targets[qq] = list(items or []) + default_items
    if apply_default:
        for qq in known:
            targets.setdefault(qq, []).extend(default_items)

    total_added = 0
    for qq, items in targets.items():
        scope = resolve_scope(bot_uuids, qq)
        if not scope and primary_bot:
            scope = create_scope_and_bind(primary_bot, qq)
            if scope:
                print(f"  + QQ {qq} 自动开通 Scope {scope}")
        if not scope:
            print(f"  ! QQ {qq} 没有绑定 Scope，跳过（PowerContext 不可用或开通失败）")
            continue
        existing = list_texts(scope)
        added = 0
        for text in items:
            text = (text or "").strip()
            if not text or text in existing:
                continue
            if remember(scope, text, "preset-profile"):
                added += 1
                existing.add(text)
        total_added += added
        print(f"  QQ {qq} -> {scope}：新增画像 {added} 条（已有 {len(existing)-added} 条）")
    print(f" 完成：新增 {total_added} 条画像")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
