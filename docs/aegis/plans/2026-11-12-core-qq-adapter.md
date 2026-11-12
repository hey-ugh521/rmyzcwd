# Core QQ Adapter (B1) — Implementation Plan

## Aegis Visibility

B1 adds a new Core-owned ingress and a new external contract (OneBot events/actions), so it needs a bounded plan rather than an owner-local edit. The plan keeps the current runnable LangBot stack untouched and makes the adapter opt-in.

## Goal

Add the smallest Core-native QQ text path:

```
NapCat HTTP client -> POST /onebot/event -> Core QQAdapter
Core Agent -> POST NapCat send_private_msg / send_group_msg -> QQ
```

No LangBot in the B1 path. Feature is disabled by default.

## Approved Scope

- Private text messages: always handled.
- Group text: only when the bot is `@`-mentioned; optional group allowlist.
- Core session mapping:
  - user: `qq:<user_id>`
  - private session: `qq:private:<user_id>`
  - group session: `qq:group:<group_id>`
- Use Core character/system prompt/memory via the existing `Agent.run`.
- Simple message-id dedupe.
- Config-driven `qq` block.
- HTTP webhook transport (no new WebSocket dependency); adapter boundary allows a future reverse-WS transport.

Out of scope:

- Images/audio back to QQ (B3)
- NapCat login management
- LangBot removal from installer
- Studio QQ settings (B4)
- Full group conversation heuristics beyond @ and allowlist

## Architecture / Owner

New owner: `core/qq_adapter.py` (Core-owned, no separate process).

Existing owner reused: `core.agent.Agent.run` for personality/memory/tools. It returns a stream of `(event, payload)`; `done.payload.reply` is the final text.

Transport choice for B1: OneBot HTTP webhook + HTTP actions. NapCat already exposes HTTP on `127.0.0.1:3000` and supports HTTP clients; this avoids adding a WebSocket client dependency to the Core runtime. The adapter isolates parsing/sending so a WS transport can replace the webhook later.

Security:

- Endpoint only accepts loopback clients.
- Adapter requires configured OneBot token; empty token means reject unless explicitly `allow_unauthenticated_loopback=true` (test only).
- Feature disabled by default; no startup side effects.

## Baseline / Authority Refs

- `core/agent.py` — `Agent.run(*, source, user_id, session_id, text, character)` and `done` payload.
- `core/server.py` — FastAPI app, `/api` auth middleware, lifespan, config bootstrap.
- `core/store.py` — `CFG`, `default_character`, `turn_add` behavior via Agent.
- `core/services.py` — existing OneBot login probe patterns.
- `config.example.json` — service/config shape.
- `tests/test_onebot_dedupe.py` — established dedupe behavior to mirror.
- `docs/archive/reports-history/LANGBOT-CUTOVER.md` — prior cutover intent and rollback context.

## Compatibility Boundary

- `qq.enabled=false` must be byte-for-byte behavior-neutral: no endpoint registration side effects, no background tasks, no LangBot changes.
- The new endpoint is `/onebot/event` (not `/api`/`/v1`); it owns its own token check and loopback check.
- Existing Core API/Studio/Chat behavior must not change.
- No production config changed by this task; example config only.

## Change Necessity

Current Core has no QQ ingress; LangBot owns it. To make Core the brain for QQ, code must receive OneBot events, map identity/session, call `Agent.run`, and send actions. Documentation/config alone cannot deliver a message path.

## TDD Route

- Mode: `off`
- Decision: `skipped` (no explicit strict TDD authority)
- Posture: implement minimal path, then unit tests for pure parsing/dedupe/policy and a fake-webhook integration test.
- Verification: `python -m unittest`/standalone test script; fake OneBot server; optional real NapCat/small account.

## Tasks

### Task 1 — Config + pure helpers

Files: `core/qq_adapter.py`, `config.example.json`

- Define `QQConfig` loaded from `CFG.get("qq", {})`:
  - `enabled`, `onebot_http`, `token`, `self_id`, `character`,
    `group_require_at`, `allowed_groups`, `dedupe_ttl`, `dedupe_max`,
    `max_reply_chars`.
- Pure functions:
  - `extract_text(message) -> str` (join text segments; ignore non-text in B1)
  - `is_at_self(message, self_id) -> bool`
  - `session_for(event) -> tuple[user_id, session_id, reply_target]`
  - `MessageDedupe` (TTL + LRU, same semantics as LangBot patch)
- Token resolves from config/secrets via existing `secrets.apply_to_config`; no new secret store.

Verification: import module; pure helper test; no network.

### Task 2 — Adapter + endpoint

Files: `core/qq_adapter.py`, `core/server.py`

- `QQAdapter` holds config, dedupe, an `httpx.AsyncClient` for actions.
- `handle_event(event)`:
  - ignore non-message/meta events;
  - ignore self messages, empty text, duplicate message_id;
  - private -> handle; group -> require @ and group allowlist;
  - run `Agent.run` collecting `done.reply`;
  - send via `send_private_msg`/`send_group_msg` with text message segment;
  - split long text to `max_reply_chars`.
- `POST /onebot/event` in `core/server.py`:
  - loopback check;
  - token check (`Authorization: Bearer` or `X-OneBot-Token`);
  - return 204 immediately; process in a background task;
  - never raise into NapCat.
- `GET /api/qq/status` (read-only) for Studio/ops later: enabled, self_id, last_event_at, last_reply_at, last_error.
- Config loaded once; adapter object created in app scope; no startup connection in B1.

Verification: fake webhook POST via FastAPI TestClient; fake Agent or stub; action captured by fake HTTP transport.

### Task 3 — Tests

Files: `tests/test_qq_adapter.py`

- Pure tests: text extraction, @ detection, private/group session IDs, group policy, dedupe, long reply splitting.
- Integration test without real QQ:
  - fake `httpx.MockTransport` for `send_private_msg`;
  - fake `Agent.run` yielding `done`;
  - call `QQAdapter.handle_event`;
  - assert exact OneBot action payload and no LangBot dependency.
- Optional smoke: start Core with `qq.enabled=true` and post a fake event to `/onebot/event`.

### Task 4 — Manual fake OneBot / real NapCat acceptance

- Fake HTTP client posts a private event to Core; verify QQ action payload.
- Real acceptance later with small QQ account:
  - add NapCat HTTP client -> `http://127.0.0.1:8900/onebot/event`;
  - disable LangBot WS client while testing;
  - send private text and group @;
  - verify Core turn rows and reply text.

## Risks / Retained Paths

- NapCat HTTP client config is not modified automatically in B1; it is a manual acceptance step.
- LangBot remains the current production QQ path; B1 does not touch its config.
- No websocket client dependency is added; if B2 needs streaming/heartbeat, revisit transport.
- Group policy is intentionally minimal; B2 owns richer heuristics.

## Retirement

No old owner retired in B1. LangBot stays until B4 after B1–B3 acceptance. Adapter is opt-in and can be disabled without rollback of code.

## Verification Commands

```powershell
# Core venv
D:\AI\mobius-runtime\python-core\Scripts\python.exe -m unittest tests.test_qq_adapter -v
# or standalone test if repo pattern requires
D:\AI\mobius-runtime\python-core\Scripts\python.exe tests\test_qq_adapter.py
```

## Execution Route

Inline, single-owner slice. User confirmation required: no — B1 is authorized; production QQ config remains untouched until manual acceptance.
