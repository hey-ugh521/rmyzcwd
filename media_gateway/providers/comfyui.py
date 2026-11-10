"""ComfyUI Provider Adapter（M2a，ADR-008）。

薄包装现有 ComfyUI 原语，不复制节点/工作流构造逻辑：
- submit_fn(nodes) -> prompt_id
- wait_fn(prompt_id, timeout) -> output
- cancel_fn(prompt_id) -> str
- health_fn() -> bool（可选）
- query_fn(prompt_id) -> 状态（可选；M2a 默认 unknown，由 runtime 恢复逻辑接管）
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from .base import (
    CancelResult,
    ExecutionState,
    MediaProvider,
    ProviderCapabilities,
    ProviderExecution,
    ProviderHealth,
    ProviderRequest,
    ProviderSubmission,
    ProviderUnavailable,
    ResourceClass,
)

AsyncCall = Callable[..., Awaitable[Any]]


class ComfyUIProvider:
    id = "comfyui"
    kind = "image"
    capabilities = ProviderCapabilities(
        modes=("text_to_image", "image_to_image", "lora"),
        resource=ResourceClass.GPU_HEAVY,
        max_concurrency=1,
        supports_cancel=True,
        supports_recovery=True,
    )

    def __init__(
        self,
        submit_fn: AsyncCall | None = None,
        wait_fn: AsyncCall | None = None,
        cancel_fn: AsyncCall | None = None,
        health_fn: AsyncCall | None = None,
        query_fn: AsyncCall | None = None,
    ) -> None:
        self._submit = submit_fn
        self._wait = wait_fn
        self._cancel = cancel_fn
        self._health = health_fn
        self._query = query_fn

    @staticmethod
    def _nodes(req: ProviderRequest) -> dict[str, Any]:
        payload = req.payload or {}
        nodes = payload.get("nodes") if isinstance(payload, dict) else None
        if not isinstance(nodes, dict):
            raise ProviderUnavailable("ComfyUIProvider.submit 需要 payload.nodes（已构建的节点图）")
        return nodes

    async def health(self) -> ProviderHealth:
        if self._health is None:
            return ProviderHealth(healthy=False, state="unknown", detail="health_fn 未注入")
        try:
            ok = bool(await self._health())
            return ProviderHealth(healthy=ok, state="ready" if ok else "down")
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(healthy=False, state="error", detail=type(exc).__name__)

    async def submit(self, req: ProviderRequest) -> ProviderSubmission:
        if self._submit is None:
            raise ProviderUnavailable("ComfyUIProvider.submit 未注入")
        nodes = self._nodes(req)
        try:
            prompt_id = await self._submit(nodes)
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(f"ComfyUI 提交失败: {exc}") from exc
        if not prompt_id:
            raise ProviderUnavailable("ComfyUI 未返回 prompt_id")
        return ProviderSubmission(execution_id=str(prompt_id), provider_meta={"provider": self.id})

    async def query(self, execution_id: str) -> ProviderExecution:
        if self._query is None:
            return ProviderExecution(
                execution_id=execution_id,
                state=ExecutionState.SUBMISSION_UNKNOWN,
                error_code="query_not_supported",
                error_message="M2a 查询由 runtime 恢复逻辑接管",
            )
        try:
            state = await self._query(execution_id)
        except Exception as exc:  # noqa: BLE001
            return ProviderExecution(
                execution_id=execution_id,
                state=ExecutionState.SUBMISSION_UNKNOWN,
                error_code="provider_query_failed",
                error_message=str(exc),
            )
        return ProviderExecution(execution_id=execution_id, state=ExecutionState(state))

    async def cancel(self, execution_id: str) -> CancelResult:
        if self._cancel is None or not self.capabilities.supports_cancel:
            return CancelResult(supported=False, requested=False, detail="cancel_fn 未注入")
        try:
            detail = await self._cancel(execution_id)
        except Exception as exc:  # noqa: BLE001
            return CancelResult(supported=True, requested=True, detail=f"cancel failed: {exc}")
        return CancelResult(supported=True, requested=True, detail=str(detail or ""))
