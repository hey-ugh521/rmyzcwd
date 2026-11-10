"""Provider 注册表（M2a，ADR-008）。

只做能力/路由选择，不执行；不接触 Core 任务状态。
"""

from __future__ import annotations

from typing import Iterable

from .base import (
    CapabilityUnsupported,
    MediaProvider,
    ProviderCapabilities,
    ProviderKind,
)


class ProviderRegistry:
    def __init__(self, providers: Iterable[MediaProvider] | None = None) -> None:
        self._providers: dict[str, MediaProvider] = {}
        for p in providers or ():
            self.register(p)

    def register(self, provider: MediaProvider) -> None:
        if not provider.id:
            raise ValueError("Provider 必须有 id")
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> MediaProvider | None:
        return self._providers.get(provider_id)

    def all(self) -> list[MediaProvider]:
        return list(self._providers.values())

    def candidates(
        self,
        kind: ProviderKind | str,
        required_modes: Iterable[str] = (),
    ) -> list[MediaProvider]:
        k = str(kind.value if isinstance(kind, ProviderKind) else kind)
        need = set(required_modes)
        out = []
        for p in self._providers.values():
            if str(p.kind.value if isinstance(p.kind, ProviderKind) else p.kind) != k:
                continue
            caps: ProviderCapabilities = p.capabilities
            if need and not need.issubset(set(caps.modes)):
                continue
            out.append(p)
        return out

    def select(
        self,
        kind: ProviderKind | str,
        required_modes: Iterable[str] = (),
        preferred: str | None = None,
    ) -> MediaProvider:
        cands = self.candidates(kind, required_modes)
        if preferred:
            for p in cands:
                if p.id == preferred:
                    return p
        if not cands:
            raise CapabilityUnsupported(
                f"没有 Provider 支持 kind={kind} modes={list(required_modes)}"
            )
        return cands[0]

    def describe(self) -> list[dict]:
        rows = []
        for p in self._providers.values():
            caps = p.capabilities
            rows.append({
                "id": p.id,
                "kind": str(p.kind.value if isinstance(p.kind, ProviderKind) else p.kind),
                "modes": list(caps.modes),
                "resource": caps.resource.value,
                "max_concurrency": caps.max_concurrency,
                "supports_cancel": caps.supports_cancel,
                "supports_recovery": caps.supports_recovery,
            })
        return rows
