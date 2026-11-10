"""TTS Provider Adapter（M2b，ADR-008）。

Kokoro（CPU）/ GSV / F5 三个 Provider 共享 HTTP 协议，但能力/资源/默认 voice 不同。
Provider 只负责合成音频字节；落盘/命名/URL 由 Gateway runtime 统一处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .base import (
    ExecutionState,
    ProviderCapabilities,
    ProviderError,
    ProviderExecution,
    ProviderHealth,
    ProviderRequest,
    ProviderSubmission,
    ProviderUnavailable,
    ResourceClass,
)

PostFn = Callable[..., Awaitable[Any]]


@dataclass
class TTSAudio:
    audio: bytes
    engine: str
    voice: str
    speed: float


class TTSHttpProvider:
    """HTTP /v1/audio/speech Provider 基类；不落盘、不做业务校验。"""

    id = "tts"
    engine = "tts"
    kind = "speech"
    capabilities = ProviderCapabilities(
        modes=("tts",), resource=ResourceClass.CPU, max_concurrency=2,
        supports_cancel=False, supports_recovery=False,
    )

    def __init__(
        self,
        post_fn: PostFn,
        base_url: str,
        *,
        timeout: float = 120.0,
        default_voice: str = "",
        modes: tuple[str, ...] | None = None,
        resource: ResourceClass | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self._post = post_fn
        self._base = str(base_url).rstrip("/")
        self._timeout = timeout
        self._default_voice = default_voice
        if modes is not None or resource is not None or max_concurrency is not None:
            self.capabilities = ProviderCapabilities(
                modes=modes or self.capabilities.modes,
                resource=resource or self.capabilities.resource,
                max_concurrency=max_concurrency or self.capabilities.max_concurrency,
                supports_cancel=self.capabilities.supports_cancel,
                supports_recovery=self.capabilities.supports_recovery,
            )

    async def health(self) -> ProviderHealth:
        try:
            resp = await self._post(
                f"{self._base}/health",
                {},
                timeout=min(self._timeout, 5.0),
            )
            ok = int(getattr(resp, "status_code", 0)) < 500
            return ProviderHealth(healthy=ok, state="ready" if ok else "down")
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(healthy=False, state="error", detail=type(exc).__name__)

    async def synthesize(self, req: ProviderRequest) -> TTSAudio:
        payload_in = dict(req.payload or {})
        text = str(payload_in.get("text") or "").strip()
        if not text:
            raise ProviderError("missing_text", "text 不能为空", 400)
        voice = str(payload_in.get("voice") or self._default_voice or "")
        speed = float(payload_in.get("speed") or 1.0)
        speed = max(0.5, min(2.0, speed))
        body = {
            "model": "tts",
            "input": text,
            "voice": voice,
            "response_format": "wav",
            "speed": speed,
        }
        try:
            resp = await self._post(
                f"{self._base}/v1/audio/speech",
                body,
                timeout=self._timeout,
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(f"TTS 引擎 {self.engine} 不可达: {exc}") from exc
        code = int(getattr(resp, "status_code", 0))
        if code != 200:
            text_detail = str(getattr(resp, "text", ""))[:300]
            raise ProviderError(
                "tts_failed", f"TTS 引擎 {self.engine} 返回 HTTP {code}: {text_detail}", 502
            )
        audio = bytes(getattr(resp, "content", b"") or b"")
        if not audio:
            raise ProviderError("tts_empty_audio", "TTS 返回空音频", 502)
        return TTSAudio(audio=audio, engine=self.engine, voice=voice, speed=speed)

    # MediaProvider 统一接口：TTS 是同步能力，submit 直接完成合成，
    # 结果由 runtime 通过 _last_result 读取；query 返回 completed。
    _results: dict[str, ProviderExecution] = {}

    async def submit(self, req: ProviderRequest) -> ProviderSubmission:
        audio = await self.synthesize(req)
        eid = f"tts_{id(req)}"
        self._results[eid] = ProviderExecution(
            execution_id=eid, state=ExecutionState.COMPLETED,
            result={"audio": audio.audio, "engine": audio.engine,
                    "voice": audio.voice, "speed": audio.speed},
            provider_meta={"provider": self.id, "synchronous": True},
        )
        return ProviderSubmission(execution_id=eid, provider_meta={"provider": self.id, "synchronous": True})

    async def query(self, execution_id: str) -> ProviderExecution:
        return self._results.get(execution_id) or ProviderExecution(
            execution_id=execution_id, state=ExecutionState.SUBMISSION_UNKNOWN,
            error_code="tts_result_not_found",
        )

    async def cancel(self, execution_id: str):
        from .base import CancelResult
        return CancelResult(supported=False, requested=False, detail="TTS 为同步调用，不可取消")


class KokoroProvider(TTSHttpProvider):
    id = "kokoro"
    engine = "kokoro"
    capabilities = ProviderCapabilities(
        modes=("tts",), resource=ResourceClass.CPU, max_concurrency=2,
        supports_cancel=False, supports_recovery=False,
    )


class GSVProvider(TTSHttpProvider):
    id = "gsv"
    engine = "mobius"
    capabilities = ProviderCapabilities(
        modes=("tts", "voice_clone"), resource=ResourceClass.GPU_HEAVY, max_concurrency=1,
        supports_cancel=False, supports_recovery=False,
    )


class F5Provider(TTSHttpProvider):
    id = "f5"
    engine = "f5"
    capabilities = ProviderCapabilities(
        modes=("tts", "voice_clone"), resource=ResourceClass.GPU_HEAVY, max_concurrency=1,
        supports_cancel=False, supports_recovery=False,
    )

    async def synthesize(self, req: ProviderRequest) -> TTSAudio:
        # 兼容旧 engine=mobius/f5：F5 端点的 voice 固定为 mobius，除非显式给出。
        payload = dict(req.payload or {})
        payload.setdefault("voice", "mobius")
        return await super().synthesize(ProviderRequest(
            task_id=req.task_id, kind=req.kind, payload=payload, spec=req.spec,
            timeout_sec=req.timeout_sec,
        ))


def build_tts_registry(cfg: Any, post_fn: PostFn):
    """按配置构建 TTS Provider Registry（Kokoro/GSV/F5）。"""
    from .registry import ProviderRegistry

    reg = ProviderRegistry()
    reg.register(KokoroProvider(
        post_fn, cfg.kokoro_url,
        timeout=getattr(cfg, "tts_timeout", 120.0), default_voice="zf_xiaobei",
    ))
    reg.register(GSVProvider(
        post_fn, cfg.f5_url,
        timeout=getattr(cfg, "tts_timeout", 120.0), default_voice="mobius",
    ))
    reg.register(F5Provider(
        post_fn, cfg.f5_url,
        timeout=getattr(cfg, "tts_timeout", 120.0), default_voice="mobius",
    ))
    return reg


_ENGINE_TO_PROVIDER = {"kokoro": "kokoro", "mobius": "gsv", "f5": "f5"}


async def synthesize_with_fallback(
    registry: Any,
    engine: str,
    text: str,
    voice: str,
    speed: float,
) -> TTSAudio:
    """按 engine 选 Provider；非 kokoro 失败时回退 kokoro（与 legacy 语义一致）。"""
    engine = str(engine or "mobius").strip().lower()
    pid = _ENGINE_TO_PROVIDER.get(engine)
    if not pid:
        raise ProviderError("unknown_engine", f"未知 TTS 引擎 {engine!r}；可用: kokoro / mobius", 400)
    req = ProviderRequest(
        task_id="", kind="speech",
        payload={"text": text, "voice": voice, "speed": speed},
    )
    try:
        return await registry.select("speech", preferred=pid).synthesize(req)
    except ProviderError as exc:
        if engine == "kokoro":
            raise
        # 仅健康/服务端失败回退；业务错误（4xx）不掩盖
        if getattr(exc, "status", 0) < 500:
            raise
        try:
            return await registry.select("speech", preferred="kokoro").synthesize(req)
        except ProviderError:
            raise exc
