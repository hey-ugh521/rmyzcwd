"""媒体资源调度器（M2c，ADR-008）。

- `gpu_heavy` 独占（ComfyUI / GSV）；`cpu` 可共享（Kokoro，默认并发 2）。
- acquire/release + 等待队列 + 租约；取消/超时/崩溃路径释放。
- 进程内 asyncio 锁；不做跨进程分布式锁（单机 Windows 首版边界）。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Mapping

DEFAULT_CAPACITY: dict[str, int] = {"gpu_heavy": 1, "gpu_light": 1, "cpu": 2}
DEFAULT_LEASE_SEC = 3600.0


class ResourceError(RuntimeError):
    pass


class ResourceTimeout(ResourceError):
    def __init__(self, message: str = ""):
        super().__init__(message or "resource_wait_timeout")


class ResourceCanceled(ResourceError):
    def __init__(self, message: str = ""):
        super().__init__(message or "resource_wait_canceled")


class UnknownResource(ResourceError):
    pass


@dataclass
class Lease:
    id: str
    resource: str
    owner: str
    acquired_at: float
    expires_at: float
    renewed_at: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "resource": self.resource,
            "owner": self.owner,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
        }


@dataclass
class _Waiter:
    owner: str
    future: asyncio.Future
    cancel_check: Callable[[], bool] | None = None


@dataclass
class ResourceManager:
    capacity: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_CAPACITY))
    default_ttl: float = DEFAULT_LEASE_SEC

    def __post_init__(self) -> None:
        self.capacity = {k: max(1, int(v)) for k, v in dict(self.capacity).items()}
        self._active: dict[str, list[Lease]] = {r: [] for r in self.capacity}
        self._queues: dict[str, deque[_Waiter]] = {r: deque() for r in self.capacity}
        self._lock = asyncio.Lock()

    # -- 内部 ---------------------------------------------------------------
    def _new_lease(self, resource: str, owner: str, ttl: float | None) -> Lease:
        now = time.monotonic()
        return Lease(
            id="lease_" + uuid.uuid4().hex[:12],
            resource=resource,
            owner=owner,
            acquired_at=now,
            expires_at=now + float(ttl or self.default_ttl),
            renewed_at=now,
        )

    def _dispatch_locked(self, resource: str) -> None:
        leases = self._active[resource]
        queue = self._queues[resource]
        while queue and len(leases) < self.capacity[resource]:
            waiter = queue.popleft()
            if waiter.future.done():
                continue
            lease = self._new_lease(resource, waiter.owner, None)
            leases.append(lease)
            waiter.future.set_result(lease)

    def _remove_waiter_locked(self, resource: str, waiter: _Waiter) -> None:
        try:
            self._queues[resource].remove(waiter)
        except ValueError:
            pass

    def _reap_locked(self) -> list[Lease]:
        now = time.monotonic()
        expired: list[Lease] = []
        for resource, leases in self._active.items():
            keep = []
            for lease in leases:
                if lease.expires_at <= now:
                    expired.append(lease)
                else:
                    keep.append(lease)
            if len(keep) != len(leases):
                self._active[resource] = keep
                self._dispatch_locked(resource)
        return expired

    # -- 公共 API -----------------------------------------------------------
    async def acquire(
        self,
        resource: str,
        owner: str,
        *,
        timeout: float = 300.0,
        ttl: float | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> Lease:
        if resource not in self.capacity:
            raise UnknownResource(f"未知资源 {resource!r}，可用: {list(self.capacity)}")
        deadline = time.monotonic() + max(0.0, float(timeout))
        waiter: _Waiter | None = None
        while True:
            if waiter is None:
                async with self._lock:
                    self._reap_locked()
                    if len(self._active[resource]) < self.capacity[resource]:
                        lease = self._new_lease(resource, owner, ttl)
                        self._active[resource].append(lease)
                        return lease
                    loop = asyncio.get_running_loop()
                    waiter = _Waiter(owner=owner, future=loop.create_future(), cancel_check=cancel_check)
                    self._queues[resource].append(waiter)
            try:
                await asyncio.wait_for(asyncio.shield(waiter.future), timeout=0.1)
            except asyncio.TimeoutError:
                if waiter.future.done():
                    return waiter.future.result()
                if cancel_check is not None and cancel_check():
                    async with self._lock:
                        self._remove_waiter_locked(resource, waiter)
                    raise ResourceCanceled()
                if time.monotonic() >= deadline:
                    async with self._lock:
                        self._remove_waiter_locked(resource, waiter)
                    raise ResourceTimeout(f"等待 {resource} 超时（{timeout:.1f}s）")
                continue
            except asyncio.CancelledError:
                async with self._lock:
                    self._remove_waiter_locked(resource, waiter)
                raise
            if waiter.future.done():
                return waiter.future.result()
            # 被唤醒但未拿到 lease（理论上不会发生）：重新排队
            waiter = None

    def release(self, lease: Lease | None) -> None:
        if lease is None:
            return
        # release 必须同步可调用（worker finally 中）；asyncio.Lock 不能在这里 await。
        # 单事件循环下，列表操作为原子；直接同步释放并唤醒等待者。
        leases = self._active.get(lease.resource)
        if leases is None:
            return
        try:
            leases.remove(lease)
        except ValueError:
            return
        self._dispatch_locked_sync(lease.resource)

    def _dispatch_locked_sync(self, resource: str) -> None:
        # release 在事件循环线程内调用；与 acquire 的 lock 竞争窗口由单线程保证。
        leases = self._active[resource]
        queue = self._queues[resource]
        while queue and len(leases) < self.capacity[resource]:
            waiter = queue.popleft()
            if waiter.future.done():
                continue
            lease = self._new_lease(resource, waiter.owner, None)
            leases.append(lease)
            waiter.future.set_result(lease)

    def renew(self, lease: Lease | None, ttl: float | None = None) -> None:
        if lease is None:
            return
        now = time.monotonic()
        lease.renewed_at = now
        lease.expires_at = now + float(ttl or self.default_ttl)

    def reap_expired(self) -> list[dict]:
        expired = self._reap_locked()
        return [lease.to_dict() for lease in expired]

    def snapshot(self) -> dict:
        return {
            "resources": {
                resource: {
                    "capacity": self.capacity[resource],
                    "active": len(self._active[resource]),
                    "queued": len(self._queues[resource]),
                    "leases": [lease.to_dict() for lease in self._active[resource]],
                }
                for resource in self.capacity
            }
        }

    def busy(self, resource: str = "gpu_heavy") -> bool:
        return bool(self._active.get(resource)) or bool(self._queues.get(resource))
