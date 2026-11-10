"""媒体 Provider 契约（M2a，ADR-008）。

Provider 负责具体执行（ComfyUI/TTS 等），不得把厂商字段泄漏到 Core/MCP。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol


class ProviderKind(str, Enum):
    IMAGE = "image"
    SPEECH = "speech"


class ResourceClass(str, Enum):
    GPU_HEAVY = "gpu_heavy"   # 独占 GPU（ComfyUI / GSV）
    GPU_LIGHT = "gpu_light"
    CPU = "cpu"               # 可共享（Kokoro）


class ExecutionState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUBMISSION_UNKNOWN = "submission_unknown"


@dataclass(frozen=True)
class ProviderCapabilities:
    modes: tuple[str, ...] = ()
    resource: ResourceClass = ResourceClass.CPU
    max_concurrency: int = 1
    supports_cancel: bool = False
    supports_recovery: bool = False

    def supports(self, mode: str) -> bool:
        return mode in self.modes


@dataclass
class ProviderHealth:
    healthy: bool
    state: str = "unknown"    # ready|starting|down|error|unknown
    detail: str = ""
    latency_ms: float | None = None


@dataclass
class ProviderRequest:
    task_id: str
    kind: ProviderKind | str
    payload: Mapping[str, Any] = field(default_factory=dict)
    spec: Mapping[str, Any] = field(default_factory=dict)
    timeout_sec: float | None = None


@dataclass
class ProviderSubmission:
    execution_id: str
    accepted: bool = True
    provider_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderExecution:
    execution_id: str
    state: ExecutionState
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    provider_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class CancelResult:
    supported: bool
    requested: bool
    detail: str = ""


class ProviderError(RuntimeError):
    """Provider 层统一错误；不向上泄漏厂商字段。"""

    def __init__(self, error_code: str, message: str = "", status: int = 503):
        self.error_code = error_code
        self.status = status
        super().__init__(message or error_code)


class ProviderUnavailable(ProviderError):
    def __init__(self, message: str = ""):
        super().__init__("provider_unavailable", message, 503)


class CapabilityUnsupported(ProviderError):
    def __init__(self, message: str = ""):
        super().__init__("capability_unsupported", message, 400)


class ProviderTimeout(ProviderError):
    def __init__(self, message: str = ""):
        super().__init__("provider_timeout", message, 504)


class MediaProvider(Protocol):
    id: str
    kind: ProviderKind | str
    capabilities: ProviderCapabilities

    async def health(self) -> ProviderHealth: ...
    async def submit(self, req: ProviderRequest) -> ProviderSubmission: ...
    async def query(self, execution_id: str) -> ProviderExecution: ...
    async def cancel(self, execution_id: str) -> CancelResult: ...
