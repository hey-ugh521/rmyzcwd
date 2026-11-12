"""Core-native OneBot (v11) HTTP ingress for QQ.

B1 scope: private/group text messages. The adapter is deliberately transport-thin:
NapCat posts OneBot events to Core, Core runs the existing Agent, and Core calls
OneBot's HTTP action endpoint to reply.

The feature is opt-in through config.json `qq.enabled`; importing this module has
no side effects and creates no network connections.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable

import httpx

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
LOOPBACK_ALLOW_UNAUTHENTICATED = False


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [x.strip() for x in value.split(",") if x.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


class MessageDedupe:
    """Small TTL + LRU set for OneBot message_id dedupe.

    Mirrors the semantics of the LangBot patch: empty keys are never deduped,
    TTL <= 0 disables dedupe, and max_size evicts the least-recently-used entry.
    """

    def __init__(self, ttl: float = 600.0, max_size: int = 2048,
                 clock: Callable[[], float] | None = None) -> None:
        self.ttl = max(0.0, float(ttl))
        self.max_size = max(1, int(max_size))
        self._clock = clock or time.monotonic
        self._items: OrderedDict[str, float] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def _prune(self, now: float) -> None:
        if not self.enabled:
            return
        cutoff = now - self.ttl
        while self._items:
            key, ts = next(iter(self._items.items()))
            if ts >= cutoff:
                break
            self._items.popitem(last=False)

    def is_duplicate(self, key: str) -> bool:
        if not key or not self.enabled:
            return False
        now = self._clock()
        self._prune(now)
        if key in self._items:
            self._items.move_to_end(key)
            return True
        self._items[key] = now
        while len(self._items) > self.max_size:
            self._items.popitem(last=False)
        return False

    def stats(self) -> dict[str, Any]:
        return {"size": len(self._items), "ttl": self.ttl,
                "max_size": self.max_size, "enabled": self.enabled}


class QQConfig:
    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        raw = raw or {}
        self.enabled = _as_bool(raw.get("enabled"), False)
        self.onebot_http = str(raw.get("onebot_http") or "http://127.0.0.1:3000").rstrip("/")
        self.token = str(raw.get("token") or "")
        self.self_id = str(raw.get("self_id") or "")
        self.character = str(raw.get("character") or "").strip() or None
        self.group_require_at = _as_bool(raw.get("group_require_at"), True)
        self.allowed_groups = {str(x) for x in _as_list(raw.get("allowed_groups"))}
        self.dedupe_ttl = float(raw.get("dedupe_ttl") or 600)
        self.dedupe_max = int(raw.get("dedupe_max") or 2048)
        self.max_reply_chars = max(80, int(raw.get("max_reply_chars") or 1200))
        self.allow_unauthenticated_loopback = _as_bool(
            raw.get("allow_unauthenticated_loopback"), LOOPBACK_ALLOW_UNAUTHENTICATED
        )
        self.action_timeout_sec = float(raw.get("action_timeout_sec") or 15)


class QQAdapter:
    def __init__(self, agent: Any, config: dict[str, Any] | None = None,
                 send_action: Callable[[str, str, str], Awaitable[None]] | None = None) -> None:
        self.agent = agent
        self.cfg = QQConfig(config)
        self.dedupe = MessageDedupe(self.cfg.dedupe_ttl, self.cfg.dedupe_max)
        self._send_action = send_action
        self.last_event_at: float | None = None
        self.last_reply_at: float | None = None
        self.last_error: str | None = None
        self.handled = 0

    # ------------------------------------------------------------------ auth

    def authorized(self, request: Any) -> bool:
        """Validate the inbound webhook token and loopback boundary."""
        host = getattr(getattr(request, "client", None), "host", "") or ""
        if host not in LOOPBACK:
            return False
        if not self.cfg.token:
            return self.cfg.allow_unauthenticated_loopback
        headers = getattr(request, "headers", {}) or {}
        auth = str(headers.get("authorization") or "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not token:
            token = str(headers.get("x-onebot-token") or "").strip()
        return bool(token) and hmac.compare_digest(token, self.cfg.token)

    # ------------------------------------------------------------- event I/O

    async def handle_event(self, event: dict[str, Any]) -> str | None:
        """Process one OneBot event. Returns the reply text, or None if ignored."""
        if not self.cfg.enabled:
            return None
        if not isinstance(event, dict):
            return None
        if event.get("post_type") != "message":
            return None
        message_type = str(event.get("message_type") or "")
        if message_type not in {"private", "group"}:
            return None

        self.last_event_at = time.time()
        self_id = str(event.get("self_id") or self.cfg.self_id or "")
        sender_id = str(event.get("user_id") or "")
        if sender_id and self_id and sender_id == self_id:
            return None

        message = event.get("message")
        text = extract_text(message)
        if not text:
            return None

        if not should_handle(event, self.cfg, self_id):
            return None

        key = str(event.get("message_id") or "") or _event_fallback_key(event)
        if self.dedupe.is_duplicate(key):
            return None

        user_id, session_id, target = session_for(event)
        reply = ""
        try:
            async for ev, payload in self.agent.run(
                source="qq", user_id=user_id, session_id=session_id,
                text=text, character=self.cfg.character,
            ):
                if ev == "error":
                    self.last_error = str((payload or {}).get("message") or "agent_error")
                elif ev == "done":
                    reply = str((payload or {}).get("reply") or "").strip()
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            reply = ""

        if not reply:
            reply = "（没有生成回复，请稍后再试）"
        for chunk in split_text(reply, self.cfg.max_reply_chars):
            try:
                await self._send(target[0], target[1], chunk)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"send_failed: {type(exc).__name__}: {exc}"
                break
        self.handled += 1
        self.last_reply_at = time.time()
        return reply

    async def _send(self, target_kind: str, target_id: str, text: str) -> None:
        if self._send_action is not None:
            await self._send_action(target_kind, target_id, text)
            return
        action = "send_group_msg" if target_kind == "group" else "send_private_msg"
        params: dict[str, Any] = {"message": [{"type": "text", "data": {"text": text}}]}
        if target_kind == "group":
            params["group_id"] = int(target_id) if target_id.isdigit() else target_id
        else:
            params["user_id"] = int(target_id) if target_id.isdigit() else target_id
        headers = {"Content-Type": "application/json"}
        if self.cfg.token:
            headers["Authorization"] = f"Bearer {self.cfg.token}"
        async with httpx.AsyncClient(timeout=self.cfg.action_timeout_sec) as client:
            resp = await client.post(f"{self.cfg.onebot_http}/{action}", headers=headers, json=params)
            if resp.status_code >= 400:
                raise RuntimeError(f"OneBot {action} HTTP {resp.status_code}: {resp.text[:200]}")

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.cfg.enabled,
            "transport": "http_webhook",
            "onebot_http": self.cfg.onebot_http,
            "self_id": self.cfg.self_id,
            "character": self.cfg.character,
            "group_require_at": self.cfg.group_require_at,
            "allowed_groups": sorted(self.cfg.allowed_groups),
            "dedupe": self.dedupe.stats(),
            "handled": self.handled,
            "last_event_at": self.last_event_at,
            "last_reply_at": self.last_reply_at,
            "last_error": self.last_error,
        }


# ------------------------------------------------------------------ pure helpers

def extract_text(message: Any) -> str:
    """Join text segments from an OneBot message array (or accept a raw string)."""
    if isinstance(message, str):
        return message.strip()
    if not isinstance(message, list):
        return ""
    parts: list[str] = []
    for seg in message:
        if not isinstance(seg, dict):
            continue
        if seg.get("type") == "text":
            data = seg.get("data") or {}
            text = data.get("text") if isinstance(data, dict) else None
            if text:
                parts.append(str(text))
    return "".join(parts).strip()


def is_at_self(message: Any, self_id: str) -> bool:
    if not self_id or not isinstance(message, list):
        return False
    target = str(self_id)
    for seg in message:
        if not isinstance(seg, dict) or seg.get("type") != "at":
            continue
        data = seg.get("data") or {}
        if isinstance(data, dict) and str(data.get("qq") or "") == target:
            return True
    return False


def should_handle(event: dict[str, Any], cfg: QQConfig, self_id: str) -> bool:
    if str(event.get("message_type")) == "private":
        return True
    group_id = str(event.get("group_id") or "")
    if cfg.allowed_groups and group_id not in cfg.allowed_groups:
        return False
    if cfg.group_require_at and not is_at_self(event.get("message"), self_id):
        return False
    return True


def session_for(event: dict[str, Any]) -> tuple[str, str, tuple[str, str]]:
    """Return (core user_id, core session_id, (target_kind, target_id))."""
    sender = str(event.get("user_id") or "unknown")
    user_id = f"qq:{sender}"
    if str(event.get("message_type")) == "group":
        group_id = str(event.get("group_id") or "unknown")
        return user_id, f"qq:group:{group_id}", ("group", group_id)
    return user_id, f"qq:private:{sender}", ("private", sender)


def split_text(text: str, limit: int = 1200) -> list[str]:
    """Split a reply into OneBot-sized chunks, preferring newline boundaries."""
    text = text.strip()
    if not text:
        return []
    limit = max(80, int(limit))
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")
    return [c for c in chunks if c]


def _event_fallback_key(event: dict[str, Any]) -> str:
    raw = "|".join(str(event.get(k) or "") for k in (
        "time", "self_id", "message_type", "group_id", "user_id", "raw_message"
    ))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() if raw.strip("|") else ""
