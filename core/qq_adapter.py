"""Core-native OneBot (v11) HTTP ingress for QQ.

B1 scope: private/group text messages. The adapter is deliberately transport-thin:
NapCat posts OneBot events to Core, Core runs the existing Agent, and Core calls
OneBot's HTTP action endpoint to reply.

The feature is opt-in through config.json `qq.enabled`; importing this module has
no side effects and creates no network connections.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
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
        self.group_at_sender = _as_bool(raw.get("group_at_sender"), True)
        self.rate_limit_per_min = max(0, int(raw.get("rate_limit_per_min") or 12))
        self.aggregate_window_sec = max(0.0, float(raw.get("aggregate_window_sec") or 0))
        self.send_media = _as_bool(raw.get("send_media"), True)
        self.media_base = str(raw.get("media_base") or "http://127.0.0.1:8899").rstrip("/")
        self.max_media_items = max(0, int(raw.get("max_media_items") or 2))
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
        self._rate: dict[str, list[float]] = {}
        self._buffers: dict[str, dict[str, Any]] = {}
        self._buffer_tasks: set[asyncio.Task] = set()
        self.last_event_at: float | None = None
        self.last_reply_at: float | None = None
        self.last_error: str | None = None
        self.handled = 0
        self.rate_limited = 0

    # ------------------------------------------------------------------ auth

    def authorized(self, request: Any, raw_body: bytes = b"") -> bool:
        """Validate the inbound webhook token and loopback boundary.

        NapCat's HTTP client sends ``X-Signature: sha1=HMAC-SHA1(raw_body, token)``
        rather than an Authorization header. Support that plus direct bearer
        tokens for other OneBot implementations/tests.
        """
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
        if token and hmac.compare_digest(token, self.cfg.token):
            return True
        signature = str(headers.get("x-signature") or "").strip()
        if signature.lower().startswith("sha1=") and raw_body:
            expected = "sha1=" + hmac.new(
                self.cfg.token.encode("utf-8"), raw_body, hashlib.sha1
            ).hexdigest()
            return hmac.compare_digest(signature.lower(), expected)
        return False

    # ------------------------------------------------------------- event I/O

    def _allow_rate(self, key: str) -> bool:
        limit = self.cfg.rate_limit_per_min
        if limit <= 0:
            return True
        now = time.monotonic()
        window = [t for t in self._rate.get(key, []) if now - t < 60]
        if len(window) >= limit:
            self._rate[key] = window
            return False
        window.append(now)
        self._rate[key] = window
        return True

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

        rate_key = f"{message_type}:{event.get('group_id') or event.get('user_id')}:{sender_id}"
        if not self._allow_rate(rate_key):
            self.rate_limited += 1
            self.last_error = "rate_limited"
            return None

        user_id, session_id, target = session_for(event)
        if self.cfg.aggregate_window_sec > 0:
            buffer_key = f"{session_id}|{sender_id}"
            buf = self._buffers.get(buffer_key)
            if buf is None:
                buf = {"texts": [], "event": event, "sender_id": sender_id}
                self._buffers[buffer_key] = buf
                task = asyncio.create_task(self._flush_buffer(buffer_key))
                self._buffer_tasks.add(task)
                task.add_done_callback(self._buffer_tasks.discard)
            buf["texts"].append(text)
            return None

        return await self._respond(event, text)

    async def _flush_buffer(self, buffer_key: str) -> None:
        await asyncio.sleep(self.cfg.aggregate_window_sec)
        buf = self._buffers.pop(buffer_key, None)
        if not buf:
            return
        text = "\n".join(t for t in buf.get("texts", []) if t).strip()
        if text:
            await self._respond(buf["event"], text)

    async def _respond(self, event: dict[str, Any], text: str) -> str | None:
        message_type = str(event.get("message_type") or "")
        self_id = str(event.get("self_id") or self.cfg.self_id or "")
        sender_id = str(event.get("user_id") or "")
        user_id, session_id, target = session_for(event)
        reply = ""
        images: list[str] = []
        audio: list[str] = []
        try:
            async for ev, payload in self.agent.run(
                source="qq", user_id=user_id, session_id=session_id,
                text=text, character=self.cfg.character,
            ):
                if ev == "error":
                    self.last_error = str((payload or {}).get("message") or "agent_error")
                elif ev == "done":
                    reply = str((payload or {}).get("reply") or "").strip()
                    images = [str(u) for u in (payload or {}).get("images") or [] if u]
                    audio = [str(u) for u in (payload or {}).get("audio") or [] if u]
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"
            reply = ""

        reply = strip_media_refs(reply, images + audio)
        if not reply and not images and not audio:
            reply = "（没有生成回复，请稍后再试）"
        for chunk in split_text(reply, self.cfg.max_reply_chars):
            try:
                await self._send(target[0], target[1], chunk, sender_id)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"send_failed: {type(exc).__name__}: {exc}"
                break
        if self.cfg.send_media and (images or audio):
            try:
                await self._send_media(target[0], target[1], images, audio)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"media_send_failed: {type(exc).__name__}: {exc}"
        self.handled += 1
        self.last_reply_at = time.time()
        return reply

    async def _send(self, target_kind: str, target_id: str, text: str,
                    sender_id: str = "") -> None:
        if not text:
            return
        if self._send_action is not None:
            await self._send_action(target_kind, target_id, text)
            return
        action = "send_group_msg" if target_kind == "group" else "send_private_msg"
        params: dict[str, Any] = {
            "message": build_reply_segments(
                target_kind, text, sender_id, self.cfg.group_at_sender
            )
        }
        if target_kind == "group":
            params["group_id"] = int(target_id) if target_id.isdigit() else target_id
        else:
            params["user_id"] = int(target_id) if target_id.isdigit() else target_id
        await self._action(action, params)

    async def _send_media(self, target_kind: str, target_id: str,
                          images: list[str], audio: list[str]) -> None:
        if self._send_action is not None:
            return
        action = "send_group_msg" if target_kind == "group" else "send_private_msg"
        base: dict[str, Any] = {}
        if target_kind == "group":
            base["group_id"] = int(target_id) if target_id.isdigit() else target_id
        else:
            base["user_id"] = int(target_id) if target_id.isdigit() else target_id
        media = [("image", u) for u in images[: self.cfg.max_media_items]]
        media += [("record", u) for u in audio[: self.cfg.max_media_items]]
        if not self.cfg.send_media:
            return
        for seg_type, url in media:
            absolute = self._absolute_url(url)
            if not absolute:
                continue
            await self._action(action, {
                **base,
                "message": [{"type": seg_type, "data": {"file": absolute}}],
            })

    def _absolute_url(self, url: str) -> str:
        url = str(url or "").strip()
        if not url:
            return ""
        if url.startswith("//"):
            return "http:" + url
        if url.startswith("/"):
            return self.cfg.media_base + url
        return url

    async def _action(self, action: str, params: dict[str, Any]) -> None:
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
            "group_at_sender": self.cfg.group_at_sender,
            "rate_limit_per_min": self.cfg.rate_limit_per_min,
            "aggregate_window_sec": self.cfg.aggregate_window_sec,
            "rate_limited": self.rate_limited,
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


def strip_media_refs(text: str, urls: list[str] | None = None) -> str:
    """Remove media URLs/markdown that the adapter sends as native segments."""
    out = str(text or "")
    for url in urls or []:
        if url:
            out = out.replace(str(url), "")
    out = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def build_reply_segments(target_kind: str, text: str, sender_id: str,
                         at_sender: bool = True) -> list[dict[str, Any]]:
    """Build OneBot message segments for a reply.

    Group replies can @ the sender so the reply is visible/notifying; private
    replies are always plain text.
    """
    segments: list[dict[str, Any]] = []
    if target_kind == "group" and at_sender and sender_id:
        segments.append({"type": "at", "data": {"qq": str(sender_id)}})
        segments.append({"type": "text", "data": {"text": " " + text}})
    else:
        segments.append({"type": "text", "data": {"text": text}})
    return segments


def _event_fallback_key(event: dict[str, Any]) -> str:
    raw = "|".join(str(event.get(k) or "") for k in (
        "time", "self_id", "message_type", "group_id", "user_id", "raw_message"
    ))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() if raw.strip("|") else ""
