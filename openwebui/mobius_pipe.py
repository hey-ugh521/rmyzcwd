"""Möbius Pipe —— Open WebUI 的 Möbius 模型入口。

安装方式（二选一）：
1. 复制本文件到 Open WebUI 的 functions/pipes 目录（或通过 Open WebUI
   管理界面 Workspace → Functions → 新建 Function 粘贴本文件内容）。
2. 在 Open WebUI 模型选择里应看到「Möbius」，选中后所有对话转发到 Core。

链路（契约 §4.2）：
    Open WebUI ── Pipe.pipe() ── POST {CORE_URL}/v1/chat (SSE)
                                  event: meta / text / task / done / error

行为：
- 逐段 yield Core 返回的 text delta，Open WebUI 边收边渲染；
- 请求头附带 `X-Mobius-Client: openwebui`，身份用 X-Mobius-User / X-Mobius-Session；
- Core 未启动、超时、HTTP 错误、SSE error 事件都转成友好的中文提示，
  绝不把 Python 栈抛给用户；
- 只依赖标准库 urllib（不额外引入 requests，避免 Open WebUI 环境差异）。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Generator, Optional

try:  # Open WebUI 自带 pydantic；独立测试时允许缺失
    from pydantic import BaseModel, Field
except ImportError:  # pragma: no cover - 仅无 pydantic 的独立环境
    BaseModel = object  # type: ignore[assignment,misc]

    def Field(default=None, description: str = ""):  # type: ignore[misc]
        return default


class Pipe:
    """Open WebUI Pipe：把对话转发到 Möbius Core 的原生 SSE 接口。"""

    class Valves(BaseModel):
        CORE_URL: str = Field(
            default="http://127.0.0.1:8900",
            description="Möbius Core 地址（宿主机 8900）",
        )
        API_KEY: str = Field(
            default="",
            description="Core 的 Bearer api_key；回环地址可留空",
        )
        DEFAULT_CHARACTER: str = Field(
            default="",
            description="默认角色 id（留空由 Core 决定，例如 bronya）",
        )
        CONNECT_TIMEOUT: int = Field(
            default=15,
            description="连接 Core 的超时秒数",
        )
        READ_TIMEOUT: int = Field(
            default=300,
            description="读取 SSE 的单次 socket 超时秒数",
        )

    def __init__(self) -> None:
        self.type = "pipe"
        self.id = "mobius"
        self.name = "Möbius"
        self.valves = self.Valves()

    # ------------------------------------------------------------------
    # Open WebUI 接口
    # ------------------------------------------------------------------

    def pipes(self) -> list[dict[str, str]]:
        """模型列表里显示的名称。"""
        return [{"id": "mobius", "name": "Möbius"}]

    def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        **_kwargs: Any,
    ) -> Generator[str, None, None]:
        try:
            yield from self._stream(body or {}, __user__ or {}, __event_emitter__)
        except GeneratorExit:  # 客户端断开，正常退出
            raise
        except Exception as exc:  # noqa: BLE001 - 最后一层兜底，绝不向 UI 抛栈
            yield (
                "\n\n⚠️ Möbius Pipe 内部错误：%s。"
                "请检查 Open WebUI 日志，或确认 Pipe 代码版本。"
                % (type(exc).__name__ + ": " + str(exc))
            )

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _stream(self, body, user, event_emitter) -> Generator[str, None, None]:
        text = _extract_user_text(body.get("messages"))
        if not text.strip():
            yield "⚠️ 没有可发送给 Möbius 的文本内容（消息为空或仅含附件）。"
            return

        user_id = _user_id(body, user)
        session_id = _session_id(body)
        payload: dict[str, Any] = {
            "source": "web",
            "user_id": user_id,
            "session_id": session_id,
            "text": text,
        }
        character = (body.get("character") or self.valves.DEFAULT_CHARACTER or "").strip()
        if character:
            payload["character"] = character

        request = self._build_request(payload, user_id, session_id)
        base = str(self.valves.CORE_URL or "").rstrip("/")
        url = base + "/v1/chat"

        try:
            response = urllib.request.urlopen(
                request,
                timeout=max(1, int(self.valves.READ_TIMEOUT or 300)),
            )
        except urllib.error.HTTPError as exc:
            yield "\n\n" + _http_error_message(exc, base)
            return
        except urllib.error.URLError as exc:
            yield "\n\n" + _connection_error_message(base, exc.reason)
            return
        except TimeoutError:
            yield "\n\n" + _connection_error_message(base, "连接超时")
            return
        except (OSError, ValueError) as exc:
            yield "\n\n" + _connection_error_message(base, str(exc))
            return

        content_type = (response.headers.get("Content-Type") or "").lower()
        if "text/event-stream" not in content_type:
            # Core 理论上只回 SSE；非 SSE 时尽量解析 JSON 报错或正文
            yield _handle_non_sse(response, base)
            return

        collected: list[str] = []
        try:
            for event, data in _iter_sse(response):
                if data == "[DONE]":
                    break
                if not data:
                    continue
                try:
                    payload_event = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(payload_event, dict):
                    continue

                if event == "text":
                    delta = payload_event.get("delta") or payload_event.get("text") or ""
                    if delta:
                        collected.append(str(delta))
                        yield str(delta)
                elif event == "task":
                    _emit_task_status(event_emitter, payload_event)
                elif event == "done":
                    reply = payload_event.get("reply")
                    task_ids = payload_event.get("task_ids") or []
                    if not collected and isinstance(reply, str) and reply.strip():
                        collected.append(reply)
                        yield reply
                    _emit_done_status(event_emitter, reply, task_ids)
                    break
                elif event == "error":
                    yield "\n\n" + _sse_error_message(payload_event)
                    return
                elif event == "meta":
                    continue
                else:
                    # 未知事件：兼容地把 delta 当正文
                    delta = payload_event.get("delta")
                    if isinstance(delta, str) and delta:
                        collected.append(delta)
                        yield delta
        except TimeoutError:
            yield "\n\n⚠️ Möbius Core 响应超时（%ss）。任务可能仍在执行，可稍后在 Console 任务中心查看。" % (
                self.valves.READ_TIMEOUT,
            )
            return
        except (urllib.error.URLError, OSError, ValueError) as exc:
            yield "\n\n⚠️ 读取 Möbius Core 流式响应中断：%s。可稍后重试，或到 Console 任务中心查看任务状态。" % exc
            return
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass

        if not collected:
            yield "⚠️ Möbius Core 没有返回任何文本（可能是模型侧异常），请稍后重试或查看 Console 审计。"

    def _build_request(self, payload: dict, user_id: str, session_id: str) -> urllib.request.Request:
        base = str(self.valves.CORE_URL or "").rstrip("/")
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Mobius-Client": "openwebui",
            "X-Mobius-User": user_id,
            "X-Mobius-Session": session_id,
            "User-Agent": "MobiusPipe/1.0 (+openwebui)",
        }
        api_key = str(self.valves.API_KEY or "").strip()
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return urllib.request.Request(base + "/v1/chat", data=data, headers=headers, method="POST")


# ----------------------------------------------------------------------
# 模块级工具函数（便于单测）
# ----------------------------------------------------------------------

def _extract_user_text(messages: Any) -> str:
    """取最后一条 user 消息的文本；兼容 OpenAI 多模态 content 数组。"""
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(parts)
        return ""
    return ""


def _pick_identity(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _user_id(body: dict, user: dict) -> str:
    candidate = _pick_identity(body.get("user_id"))
    if not candidate and isinstance(user, dict):
        candidate = _pick_identity(user.get("id") or user.get("email") or user.get("name"))
    if not candidate:
        candidate = "unknown"
    if candidate.startswith(("web:", "qq:", "console:")):
        return candidate
    return "web:" + candidate


def _session_id(body: dict) -> str:
    candidate = _pick_identity(body.get("session_id"))
    if candidate:
        if candidate.startswith(("web:", "qq:", "console:")):
            return candidate
        return "web:" + candidate
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    for key in ("chat_id", "thread_id", "session_id", "conversation_id"):
        value = _pick_identity(metadata.get(key))
        if value:
            return "web:" + value
    return "web:openwebui"


def _iter_sse(response) -> Generator[tuple[str, str], None, None]:
    """逐行解析 SSE，产出 (event, data)。"""
    event = "message"
    data_lines: list[str] = []

    def dispatch() -> tuple[str, str]:
        return event, "\n".join(data_lines)

    while True:
        raw = response.readline()
        if not raw:
            if data_lines:
                yield dispatch()
            return
        try:
            line = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - readline 返回 bytes，理论上不会失败
            continue
        line = line.rstrip("\r\n")
        if line == "":
            if data_lines:
                yield dispatch()
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[len("event:"):].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].lstrip())
        # id: / retry: / 其他字段忽略


def _connection_error_message(base: str, reason: Any) -> str:
    reason_text = str(reason).strip().rstrip("。.；; \t")
    return (
        "⚠️ 无法连接 Möbius Core（%s）：%s。\n"
        "请确认 Core 已在 8900 端口启动（`python mobius/core/server.py` 或配套启动脚本），"
        "并检查 Pipe Valves 里的 CORE_URL。"
        % (base, reason_text)
    )


def _http_error_message(exc: urllib.error.HTTPError, base: str) -> str:
    detail = ""
    try:
        raw = exc.read() or b""
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                detail = str(parsed.get("message") or parsed.get("error") or "")
            else:
                detail = text
        except (json.JSONDecodeError, TypeError):
            detail = text
    except Exception:  # noqa: BLE001
        detail = ""
    detail = detail.strip()
    suffix = ("：" + detail[:400]) if detail else ""
    out = "⚠️ Möbius Core 返回 HTTP %s%s%s。请稍后重试，或查看 Console 审计。" % (
        exc.code,
        (" " + str(exc.reason)) if getattr(exc, "reason", None) else "",
        suffix,
    )
    if exc.code == 401:
        out += "\n提示：鉴权失败，请在 Pipe Valves 配置正确的 API_KEY。"
    elif exc.code == 409:
        out += "\n提示：Core 返回 409，常见于媒体模式为 off，可在 Console 概览切换媒体模式。"
    return out


def _sse_error_message(payload: dict) -> str:
    message = payload.get("message") or payload.get("error") or "未知错误"
    code = payload.get("error") if payload.get("error") != message else ""
    hint = payload.get("hint")
    out = "⚠️ Möbius 处理失败：%s" % message
    if code:
        out += "（code: %s）" % code
    if hint:
        out += "\n提示：%s" % hint
    return out


def _handle_non_sse(response, base: str) -> str:
    try:
        raw = response.read() or b""
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return "⚠️ Möbius Core 返回了非流式响应且读取失败。"
    finally:
        try:
            response.close()
        except Exception:  # noqa: BLE001
            pass
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text.strip()[:1000] or "⚠️ Möbius Core 返回了空响应。"
    if isinstance(parsed, dict) and (parsed.get("message") or parsed.get("error")):
        return _sse_error_message(parsed)
    return text.strip()[:1000] or "⚠️ Möbius Core 返回了空响应。"


def _emit_task_status(event_emitter, task: dict) -> None:
    if not callable(event_emitter):
        return
    try:
        task_id = task.get("id", "")
        status = task.get("status", "")
        kind = task.get("kind", "")
        event_emitter({
            "type": "status",
            "data": {
                "description": "Möbius 任务 %s（%s / %s）" % (task_id, kind, status),
                "done": False,
            },
        })
    except Exception:  # noqa: BLE001 - 事件发射失败不影响正文
        return


def _emit_done_status(event_emitter, reply: Any, task_ids: Any) -> None:
    if not callable(event_emitter):
        return
    try:
        suffix = ""
        if isinstance(task_ids, list) and task_ids:
            suffix = " · 任务 " + ", ".join(str(t) for t in task_ids[:4])
        event_emitter({
            "type": "status",
            "data": {"description": "Möbius 已完成" + suffix, "done": True},
        })
    except Exception:  # noqa: BLE001
        return
