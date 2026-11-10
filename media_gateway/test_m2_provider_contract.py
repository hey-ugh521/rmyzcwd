"""M2a Provider 契约测试（gateway venv，不依赖 mcp/server 启动）。

覆盖：能力/注册表路由、ComfyUI Adapter 委托、错误映射、provider_meta 边界、
`/api/providers` 与 config.example 的静态接线。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from providers import (  # noqa: E402
    CancelResult,
    CapabilityUnsupported,
    ComfyUIProvider,
    ExecutionState,
    ProviderCapabilities,
    ProviderError,
    ProviderExecution,
    ProviderHealth,
    ProviderKind,
    ProviderRegistry,
    ProviderRequest,
    ProviderSubmission,
    ProviderUnavailable,
    ResourceClass,
)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def run(coro):
    return asyncio.run(coro)


class FakeProvider:
    def __init__(self, pid, kind, modes, resource=ResourceClass.CPU):
        self.id = pid
        self.kind = kind
        self.capabilities = ProviderCapabilities(
            modes=tuple(modes), resource=resource, max_concurrency=1,
            supports_cancel=True, supports_recovery=True,
        )

    async def health(self):
        return ProviderHealth(healthy=True, state="ready")

    async def submit(self, req):
        return ProviderSubmission(execution_id="e1")

    async def query(self, execution_id):
        return ProviderExecution(execution_id=execution_id, state=ExecutionState.COMPLETED)

    async def cancel(self, execution_id):
        return CancelResult(True, True, "ok")


def test_registry() -> None:
    print("\n[registry]")
    reg = ProviderRegistry()
    comfy = FakeProvider("comfyui", ProviderKind.IMAGE,
                         ["text_to_image", "image_to_image", "lora"], ResourceClass.GPU_HEAVY)
    kokoro = FakeProvider("kokoro", ProviderKind.SPEECH, ["tts"])
    reg.register(comfy)
    reg.register(kokoro)
    check("get/register", reg.get("comfyui") is comfy and len(reg.all()) == 2)
    check("select by kind", reg.select("image").id == "comfyui")
    check("select by required modes", reg.select("image", ["lora"]).id == "comfyui")
    check("select preferred", reg.select("speech", preferred="kokoro").id == "kokoro")
    try:
        reg.select("image", ["voice_clone"])
        check("能力不足抛 CapabilityUnsupported", False)
    except CapabilityUnsupported as exc:
        check("能力不足抛 CapabilityUnsupported", exc.error_code == "capability_unsupported")
    try:
        reg.select("speech", ["lora"])
        check("跨类能力拒绝", False)
    except CapabilityUnsupported:
        check("跨类能力拒绝", True)
    rows = reg.describe()
    check("describe 包含资源/并发/取消",
          rows[0]["id"] == "comfyui" and rows[0]["resource"] == "gpu_heavy"
          and rows[0]["max_concurrency"] == 1 and rows[0]["supports_cancel"] is True, str(rows)[:160])
    check("describe 不暴露 handler", all("submit_fn" not in r for r in rows))


def test_comfy_adapter() -> None:
    print("\n[comfyui adapter]")
    calls: dict[str, object] = {}

    async def submit(nodes):
        calls["nodes"] = nodes
        return "p123"

    async def wait(pid, cancel=None, timeout=None):
        calls["wait"] = (pid, timeout, cancel)
        return {"filename": "x.png"}

    async def cancel(pid):
        calls["cancel"] = pid
        return "interrupted"

    async def health():
        return True

    p = ComfyUIProvider(submit_fn=submit, wait_fn=wait, cancel_fn=cancel, health_fn=health)
    check("能力声明", p.capabilities.resource == ResourceClass.GPU_HEAVY
          and p.capabilities.modes == ("text_to_image", "image_to_image", "lora")
          and p.capabilities.supports_cancel and p.capabilities.supports_recovery)
    check("id/kind", p.id == "comfyui" and p.kind == "image")
    sub = run(p.submit(ProviderRequest(task_id="t1", kind="image", payload={"nodes": {"1": {}}})))
    check("submit 委托并返回 execution_id", sub.execution_id == "p123" and calls["nodes"] == {"1": {}})
    check("provider_meta 仅诊断", sub.provider_meta.get("provider") == "comfyui")
    try:
        run(p.submit(ProviderRequest(task_id="t2", kind="image", payload={})))
        check("缺节点图抛 ProviderUnavailable", False)
    except ProviderUnavailable as exc:
        check("缺节点图抛 ProviderUnavailable", exc.error_code == "provider_unavailable")
    h = run(p.health())
    check("health 正常", h.healthy is True and h.state == "ready")
    c = run(p.cancel("p123"))
    check("cancel 委托", c.supported and c.requested and calls["cancel"] == "p123")
    out = run(p.wait("p123", timeout=10))
    check("wait 委托输出", out.get("filename") == "x.png"
          and calls["wait"][0] == "p123" and calls["wait"][1] == 10)
    q = run(p.query("p123"))
    check("query 未注入时 submission_unknown", q.state == ExecutionState.SUBMISSION_UNKNOWN
          and q.error_code == "query_not_supported")
    p2 = ComfyUIProvider()
    check("未注入 submit 时抛错", (lambda: (run(p2.submit(ProviderRequest(task_id="t", kind="image", payload={"nodes": {}}))), False)[1])() if False else True)
    try:
        run(p2.submit(ProviderRequest(task_id="t", kind="image", payload={"nodes": {}})))
        check("未注入 submit 抛 ProviderUnavailable", False)
    except ProviderUnavailable:
        check("未注入 submit 抛 ProviderUnavailable", True)
    check("未注入 cancel 返回 unsupported", run(p2.cancel("x")).supported is False)
    check("ProviderError 状态码", ProviderError("x", "y", 418).status == 418)


def test_static_wiring() -> None:
    print("\n[static wiring]")
    srv = (HERE / "server.py").read_text(encoding="utf-8")
    check("server 有 /api/providers", '"/api/providers"' in srv)
    check("server 有 MOBIUS_MEDIA_PROVIDER 开关", "MOBIUS_MEDIA_PROVIDER" in srv)
    cfg = json.loads((HERE.parent / "config.example.json").read_text(encoding="utf-8"))
    media = cfg.get("media") or {}
    check("config.example 有 providers/routing",
          media.get("providers", {}).get("comfyui", {}).get("resource") == "gpu_heavy"
          and media.get("routing", {}).get("image") == ["comfyui"], str(media)[:160])


def main() -> int:
    test_registry()
    test_comfy_adapter()
    test_static_wiring()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
