"""Möbius Core —— Agent 运行时（LLM + 工具循环 + 记忆注入）。"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, AsyncIterator

import httpx

from . import store
from . import registry_migration, registry_runtime
from .capabilities import CapabilityService
from .services import GatewayClient, MediaDisabled, ServiceError, Supervisor

def _legacy_task_owner() -> bool:
    """回滚开关：MOBIUS_TASK_OWNER=gateway 时恢复 Agent 本地任务记录。"""
    return str(os.environ.get("MOBIUS_TASK_OWNER", "core")).strip().lower() == "gateway"


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "检索这个用户已确认的长期记忆与最近事件，用于保持角色一致性。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "关键词"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "记录用户明确表达且稳定的偏好或事实。写入的是候选记忆，需要管理员确认后才生效。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "记忆的键，如 称呼 / 喜好 / 作息"},
                    "value": {"type": "string", "description": "记忆内容，一句话"},
                    "confidence": {"type": "number", "description": "0~1 置信度"},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "按角色档案生成一张图片。只在用户明确要求看图/画图时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "character": {"type": "string", "description": "角色 id 或名字，如 bronya / 布洛妮娅；不确定就留空"},
                    "prompt": {"type": "string", "description": "画面描述，英文或中文均可"},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "speak",
            "description": "把一段话合成角色语音发给用户。只在用户明确要求听语音/朗读时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "要朗读的文本，不要超过 120 字"},
                    "voice": {"type": "string", "description": "音色 id，留空用角色默认"},
                },
                "required": ["text"],
            },
        },
    },
]

MEDIA_RULES = """工具使用规则：
- 只有当用户明确要求「画/生成一张图/来张图」时才调用 generate_image；不确定就问一句。
- 只有当用户明确要求「说一句/读出来/发语音」时才调用 speak。
- recall 在用户提到过去的事、称呼、偏好时先查一次。
- remember 只在用户明确说出稳定偏好时用，一次最多写 1 条，不要记录一次性的情绪或剧情细节。
- **不要在正文里写图片或音频的链接**，系统会自动把生成结果附在回复末尾。
- 一轮最多 1 张图 + 1 条语音。"""


def _chunk(text: str, size: int = 24) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


class Agent:
    def __init__(self, sup: Supervisor, gw: GatewayClient):
        self.sup = sup
        self.gw = gw
        self.capabilities = CapabilityService(gw, supervisor=sup)
        llm = store.CFG.get("llm", {})
        self.base_url = llm.get("base_url", "https://api.deepseek.com/v1").rstrip("/")
        self.api_key = llm.get("api_key", "")
        self.model = llm.get("model", "deepseek-flash")
        self.timeout = float(llm.get("timeout_sec", 120))
        self.max_rounds = int(llm.get("max_tool_rounds", 4))

    # ------------------------------------------------------------ LLM

    async def _chat(self, messages: list[dict], tools: list[dict] | None) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.8,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json=payload,
            )
        if r.status_code >= 400:
            raise ServiceError(f"LLM HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        if not data.get("choices"):
            raise ServiceError(f"LLM 返回异常: {str(data)[:300]}")
        return data

    # ------------------------------------------------------------ prompt

    def _memory_block(self, user_id: str | None, character: str | None) -> str:
        if not user_id:
            return ""
        lines: list[str] = []
        mem = store.mem_list(user_id)
        if mem:
            lines.append("【已确认的用户记忆】")
            for m in mem[:20]:
                lines.append(f"- {m['key']}：{m['value']}（置信度 {m.get('confidence', 0):.2f}）")
        tl = store.timeline_list(user_id, limit=5)
        if tl:
            lines.append("【最近事件】")
            for e in tl:
                ts = time.strftime("%m-%d %H:%M", time.localtime(e["ts"]))
                lines.append(f"- {ts} {e['text']}")
        cand = [c for c in store.cand_list("pending", 20) if c.get("user_id") == user_id]
        if cand:
            lines.append("【待确认记忆（供参考，尚未生效）】")
            for c in cand[:5]:
                lines.append(f"- {c['key']}：{c['value']}（待确认）")
        return "\n".join(lines)

    def _system_prompt(self, user_id: str | None, character: str | None) -> str:
        ch = registry_runtime.character(character)
        char_id = (ch or {}).get("id") or store.CFG.get("default_character")
        parts = [store.constitution_text(char_id)]
        mode = self.sup.get_mode()
        if mode == "off":
            parts.append("【媒体能力】当前已关闭。用户要求生图或语音时，用角色口吻说明现在不能生成，不要调用工具。")
        elif mode == "on_demand":
            parts.append("【媒体能力】按需唤醒已开启。首次生成需要 30~90 秒，回复时不要谎称已经完成。")
        else:
            parts.append("【媒体能力】预热中，通常可以立即生成。")
        mem = self._memory_block(user_id, character)
        if mem:
            parts.append(mem)
        if mode != "off":
            parts.append(MEDIA_RULES)
        return "\n\n".join(p for p in parts if p)

    # ------------------------------------------------------------ tools

    async def _exec_tool(self, name: str, args: dict, ctx: dict) -> dict:
        user_id = ctx["user_id"]
        session_id = ctx["session_id"]
        source = ctx["source"]
        if name == "recall":
            q = str(args.get("query") or "")
            hits = store.mem_search(user_id, q) if q else []
            tl = [e for e in store.timeline_list(user_id, 30) if q and q in e["text"]][:5]
            return {"ok": True, "memory": hits,
                    "timeline": [{"ts": e["ts"], "text": e["text"]} for e in tl],
                    "candidates": [c for c in store.cand_list("pending", 20)
                                   if c.get("user_id") == user_id][:5]}
        if name == "remember":
            key = str(args.get("key") or "").strip()[:40]
            val = str(args.get("value") or "").strip()[:200]
            if not key or not val:
                return {"ok": False, "error": "key/value 不能为空"}
            conf = float(args.get("confidence") or 0.6)
            c = store.cand_add(user_id, key, val, conf,
                               source=f"{source}:{session_id}", evidence=ctx.get("text", ""),
                               character=ctx.get("character"))
            store.audit(user_id, "memory.candidate", {"key": key, "value": val},
                        source=source, character=ctx.get("character"))
            return {"ok": True, "candidate_id": c["id"], "note": "已写入待确认队列，确认后生效"}
        if name == "generate_image":
            return await self._do_image(args, ctx)
        if name == "speak":
            return await self._do_voice(args, ctx)
        return {"ok": False, "error": f"未知工具 {name}"}

    async def _do_image(self, args: dict, ctx: dict) -> dict:
        if ctx.get("image_done"):
            return {"ok": False, "error": "本轮已生成过一张图，请直接使用"}
        char_key = str(args.get("character") or "").strip()
        ch = registry_runtime.character(char_key) if char_key else None
        if char_key and ch is None:
            return {"ok": False, "error": "character_not_found",
                    "hint": f"角色 {char_key} 未在已发布 Registry 中解析到"}
        if registry_runtime.mode() == "enforce" and not registry_migration.legacy_fallback_enabled() and ch is None:
            return {"ok": False, "error": "registry_required",
                    "hint": "legacy fallback 已关闭，且没有已发布角色"}
        char_id = (ch or {}).get("id")
        policy = (ch or {}).get("media_policy") or {}
        if policy.get("allow_image") is False:
            return {"ok": False, "error": f"角色 {char_id} 未开放生图"}
        prompt = str(args.get("prompt") or "").strip()
        try:
            await self.sup.ensure("image")
        except MediaDisabled:
            return {"ok": False, "error": "media_off", "hint": "媒体能力已关闭"}
        except ServiceError as e:
            return {"ok": False, "error": "comfyui_unavailable", "hint": str(e)}

        # ADR-007 / B1：core 模式任务由网关经 Core 创建；gateway 回滚模式恢复旧行为
        # （Agent 在 Core 本地预建任务，网关本地 task 作为 remote）。两种模式都只消费网关返回的 task_id。
        legacy = _legacy_task_owner()
        legacy_task = None
        if legacy:
            legacy_task = store.task_create(
                "image", ctx["source"], ctx["user_id"], ctx["session_id"], char_id, prompt,
                params={"workflow": (ch or {}).get("workflow", "portrait")}, status="running")
            store.task_update(legacy_task["id"], started_at=store.now())
            ctx["task_ids"].append(legacy_task["id"])
            ctx["pending_events"].append(legacy_task)
        ctx["image_done"] = True
        registry_workflow = None
        if registry_runtime.mode() == "enforce":
            registry_workflow = registry_runtime.workflow((ch or {}).get("workflow"))
        if (char_id and registry_runtime.mode() == "enforce" and registry_workflow is None
                and not registry_migration.legacy_fallback_enabled()):
            return {"ok": False, "error": "registry_workflow_required",
                    "hint": f"角色 {char_id} 的 workflow 没有已发布 Registry 版本，legacy fallback 已关闭"}
        try:
            resp = await self.capabilities.image(
                character=ch, character_key=char_key or char_id,
                workflow_graph=(registry_workflow or {}).get("graph"),
                prompt=prompt,
                user_id=ctx["user_id"], session_id=ctx["session_id"],
                source=ctx["source"], actor=ctx["source"],
                # 每次工具调用是一个新的逻辑请求；不能复用 session 派生 key，
                # 否则同会话下一张图会命中上一张图的 idempotency 并 409。
                idempotency_key=f"core-image-{uuid.uuid4().hex}",
            )
            task_id = resp.get("task_id") or resp.get("id")
            if not task_id:
                if legacy_task is not None:
                    store.task_update(legacy_task["id"], status="failed",
                                      error="网关未返回 task_id", finished_at=store.now())
                store.audit(ctx["user_id"], "image.generate", {"prompt": prompt},
                            result="fail", error="网关未返回 task_id", source=ctx["source"])
                return {"ok": False, "error": "gateway_error", "hint": "网关未返回任务 id"}
            if legacy_task is not None:
                store.task_update(legacy_task["id"], remote_id=task_id)
                audit_tid = legacy_task["id"]
                result_tid = legacy_task["id"]
            else:
                ctx["task_ids"].append(task_id)
                ctx.setdefault("pending_events", []).append(
                    store.task_get(task_id)
                    or {"id": task_id, "kind": "image", "status": resp.get("status", "pending")})
                audit_tid = task_id
                result_tid = task_id
            final = await self.gw.wait_task(task_id, timeout=240)
            status = store.normalize_task_status((final or {}).get("status"))
            if status == store.TASK_COMPLETED:
                result = final.get("result") or {}
                url = result.get("url") or final.get("url")
                result["url"] = url
                ctx["images"].append({"character": char_id, "url": url})
                if legacy_task is not None:
                    store.task_update(legacy_task["id"], status="succeeded", result=result,
                                      finished_at=store.now())
                store.audit(ctx["user_id"], "image.generate",
                            {"character": char_id, "prompt": prompt,
                             "loras": (ch or {}).get("loras"),
                             "seconds": result.get("seconds")},
                            task_id=audit_tid, character=char_id, source=ctx["source"])
                store.timeline_add(ctx["user_id"], f"生成了 {char_id} 的图片：{prompt}",
                                   kind="media", character=char_id,
                                   session_id=ctx["session_id"])
                return {"ok": True, "task_id": result_tid, "status": "completed",
                        "note": "图片会自动附在回复末尾，不要自己写链接"}
            if status == store.TASK_UNKNOWN:
                if legacy_task is not None:
                    store.task_update(legacy_task["id"], status="submission_unknown",
                                      error="gateway_submission_unknown", finished_at=store.now())
                store.audit(ctx["user_id"], "image.generate", {"prompt": prompt},
                            result="unknown", task_id=audit_tid,
                            character=char_id, source=ctx["source"])
                return {"ok": False, "error": "submission_unknown",
                        "hint": "提交结果未知，请到 Console 人工确认后再决定是否重试"}
            err = final.get("error") or status or "unknown"
            if legacy_task is not None:
                store.task_update(legacy_task["id"], status="failed", error=str(err),
                                  finished_at=store.now())
            store.audit(ctx["user_id"], "image.generate", {"prompt": prompt},
                        result="fail", error=str(err), task_id=audit_tid,
                        character=char_id, source=ctx["source"])
            return {"ok": False, "error": "generate_failed", "hint": str(err)}
        except Exception as e:
            if legacy_task is not None:
                store.task_update(legacy_task["id"], status="failed", error=str(e),
                                  finished_at=store.now())
            store.audit(ctx["user_id"], "image.generate", {"prompt": prompt},
                        result="fail", error=str(e), source=ctx["source"])
            return {"ok": False, "error": "gateway_error", "hint": str(e)}

    async def _do_voice(self, args: dict, ctx: dict) -> dict:
        if ctx.get("voice_done"):
            return {"ok": False, "error": "本轮已合成过一条语音"}
        text = str(args.get("text") or "").strip()
        if not text:
            return {"ok": False, "error": "text 为空"}
        ch = registry_runtime.character(ctx.get("character") or store.CFG.get("default_character")) or {}
        if (not ch and registry_runtime.mode() == "enforce"
                and not registry_migration.legacy_fallback_enabled()):
            return {"ok": False, "error": "registry_required",
                    "hint": "legacy fallback 已关闭，且没有已发布角色 VoiceProfile"}
        voice_cfg = ch.get("voice") or {}
        voice = args.get("voice") or voice_cfg.get("voice") or ""
        engine = voice_cfg.get("engine") or "kokoro"
        try:
            await self.sup.ensure("voice")
        except MediaDisabled:
            return {"ok": False, "error": "media_off", "hint": "媒体能力已关闭"}
        except ServiceError as e:
            return {"ok": False, "error": "tts_unavailable", "hint": str(e)}
        # ADR-007 / B2：core 模式由网关登记 Core 权威 speech 任务；
        # gateway 回滚模式恢复 Agent 本地任务记录（gateway 本地不产 Core 任务）。
        legacy = _legacy_task_owner()
        legacy_task = None
        if legacy:
            legacy_task = store.task_create(
                "voice", ctx["source"], ctx["user_id"], ctx["session_id"], ch.get("id"), text,
                params={"voice": voice, "engine": engine}, status="running")
            store.task_update(legacy_task["id"], started_at=store.now())
            ctx["task_ids"].append(legacy_task["id"])
            ctx["pending_events"].append(legacy_task)
        ctx["voice_done"] = True
        try:
            resp = await self.capabilities.voice(
                text=text, voice=voice, engine=engine,
                speed=float(voice_cfg.get("speed") or 1.0),
                character=ch, character_key=str(ctx.get("character") or ch.get("id") or ""),
                user_id=ctx["user_id"], session_id=ctx["session_id"],
                source=ctx["source"], actor=ctx["source"],
            )
            if not resp.get("ok"):
                raise ServiceError(resp.get("error") or "tts failed")
            url = resp.get("audio_url")
            task_id = resp.get("task_id")
            if legacy_task is not None:
                store.task_update(legacy_task["id"], status="succeeded",
                                  result={"url": url, "filename": resp.get("filename"),
                                          "seconds": resp.get("seconds")},
                                  finished_at=store.now())
                audit_tid = legacy_task["id"]
            else:
                if task_id:
                    ctx["task_ids"].append(task_id)
                audit_tid = task_id
            ctx["audio"].append(url)
            store.audit(ctx["user_id"], "voice.synthesize",
                        {"engine": engine, "voice": voice, "chars": len(text)},
                        task_id=audit_tid, character=ch.get("id"), source=ctx["source"])
            return {"ok": True, "task_id": audit_tid, "status": "completed",
                    "note": "语音会自动附在回复最后一行，不要自己写链接"}
        except Exception as e:
            if legacy_task is not None:
                store.task_update(legacy_task["id"], status="failed", error=str(e),
                                  finished_at=store.now())
            store.audit(ctx["user_id"], "voice.synthesize", {"engine": engine},
                        result="fail", error=str(e), source=ctx["source"])
            return {"ok": False, "error": "tts_error", "hint": str(e)}

    # ------------------------------------------------------------ 主循环

    async def run(self, *, source: str, user_id: str, session_id: str,
                  text: str | None = None, messages: list[dict] | None = None,
                  character: str | None = None) -> AsyncIterator[tuple[str, Any]]:
        ctx = {
            "user_id": user_id, "session_id": session_id, "source": source,
            "character": character, "text": text or "",
            "task_ids": [], "images": [], "audio": [],
        }
        mode = self.sup.get_mode()
        tools = TOOLS if mode != "off" else None

        msgs: list[dict] = []
        if messages:
            msgs = [dict(m) for m in messages if m.get("role") in ("system", "user", "assistant")]
            # 去掉 tool 角色的历史残留，避免上游把工具消息塞进来
            sys_idx = [i for i, m in enumerate(msgs) if m["role"] == "system"]
            inject = self._system_prompt(user_id, character)
            if sys_idx:
                msgs[sys_idx[0]]["content"] = inject + "\n\n" + str(msgs[sys_idx[0]]["content"])
            else:
                msgs.insert(0, {"role": "system", "content": inject})
            if text:
                msgs.append({"role": "user", "content": text})
            ctx["text"] = text or (msgs[-1]["content"] if msgs else "")
        else:
            sys_prompt = self._system_prompt(user_id, character)
            msgs.append({"role": "system", "content": sys_prompt})
            for t in store.turns_list(session_id, limit=20):
                msgs.append({"role": t["role"], "content": t["content"]})
            if text:
                msgs.append({"role": "user", "content": text})

        if text:
            store.turn_add(session_id, user_id, "user", text)

        reply = ""
        acked = False
        announced: set[str] = set()
        try:
            for _ in range(self.max_rounds):
                data = await self._chat(msgs, tools)
                msg = data["choices"][0]["message"]
                tool_calls = msg.get("tool_calls") or []
                if not tool_calls:
                    reply = (msg.get("content") or "").strip()
                    break
                msgs.append({
                    "role": "assistant",
                    "content": msg.get("content") or "",
                    "tool_calls": tool_calls,
                })
                for tc in tool_calls:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or ""
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except Exception:
                        args = {}
                    if name in ("generate_image", "speak") and not acked:
                        acked = True
                        yield "text", {"delta": "（正在处理，稍等…）\n"}
                    result = await self._exec_tool(name, args, ctx)
                    for t in ctx.pop("pending_events", []):
                        yield "task", t
                    msgs.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id") or "",
                        "content": json.dumps(result, ensure_ascii=False),
                    })
            else:
                data = await self._chat(msgs, None)
                reply = (data["choices"][0]["message"].get("content") or "").strip()
        except Exception as e:
            reply = f"（系统错误：{e}）"
            yield "error", {"error": "agent_error", "message": str(e)}

        if not reply:
            reply = "……" if not acked else "处理好了。"

        for piece in _chunk(reply):
            yield "text", {"delta": piece}
            yield "pause", {}

        # 媒体补充：链接由 Core 统一附加，不依赖模型
        extra: list[str] = []
        for img in ctx["images"]:
            if img.get("url"):
                extra.append(f"![{img.get('character') or 'image'}]({img['url']})")
        if extra:
            yield "text", {"delta": "\n" + "\n".join(extra) + "\n"}
        if ctx["audio"]:
            yield "text", {"delta": "\n" + ctx["audio"][-1] + "\n"}

        full = reply + ("\n" + "\n".join(extra) + "\n" if extra else "")
        if ctx["audio"]:
            full += "\n" + ctx["audio"][-1] + "\n"
        store.turn_add(session_id, user_id, "assistant", full)
        yield "done", {"reply": full, "task_ids": ctx["task_ids"],
                       "images": [i.get("url") for i in ctx["images"]],
                       "audio": ctx["audio"]}
