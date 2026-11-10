"""M2b TTS Provider 契约/路由测试（gateway venv，隔离，不连真实 TTS）。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from providers import (  # noqa: E402
    ProviderError,
    ProviderRequest,
    ProviderRegistry,
    ProviderUnavailable,
    ResourceClass,
)
from providers.tts import (  # noqa: E402
    F5Provider,
    GSVProvider,
    KokoroProvider,
    build_tts_registry,
    synthesize_with_fallback,
)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def run(coro):
    return asyncio.run(coro)


class Resp:
    def __init__(self, status=200, content=b"RIFFxxxxWAVE"):
        self.status_code = status
        self.content = content
        self.text = "" if status == 200 else f"http {status}"


def make_post(behavior):
    calls = []

    async def post(url, payload, timeout=120.0):
        calls.append((url, dict(payload)))
        result = behavior(url, payload)
        if isinstance(result, Exception):
            raise result
        return result

    return post, calls


def test_providers() -> None:
    print("\n[providers]")
    post, calls = make_post(lambda url, payload: Resp(200, b"RIFFkokoro"))
    p = KokoroProvider(post, "http://kokoro:7870", default_voice="zf_xiaobei")
    check("kokoro 能力 CPU/tts", p.capabilities.resource == ResourceClass.CPU
          and p.capabilities.modes == ("tts",) and p.capabilities.max_concurrency == 2)
    a = run(p.synthesize(ProviderRequest(task_id="t1", kind="speech",
                                         payload={"text": "你好", "speed": 1.2})))
    check("kokoro 合成返回音频/默认 voice", a.audio.startswith(b"RIFF") and a.engine == "kokoro"
          and a.voice == "zf_xiaobei" and a.speed == 1.2)
    check("kokoro 请求 URL/参数", calls and calls[0][0].endswith("/v1/audio/speech")
          and calls[0][1]["input"] == "你好" and calls[0][1]["response_format"] == "wav")

    post2, calls2 = make_post(lambda url, payload: Resp(200, b"RIFFgsv"))
    g = GSVProvider(post2, "http://gsv:7871", default_voice="mobius")
    check("gsv 能力 GPU heavy/voice_clone", g.capabilities.resource == ResourceClass.GPU_HEAVY
          and "voice_clone" in g.capabilities.modes and g.capabilities.max_concurrency == 1)
    a2 = run(g.synthesize(ProviderRequest(task_id="t2", kind="speech",
                                          payload={"text": "hi", "voice": "yae"})))
    check("gsv 合成返回 engine=mobius / voice", a2.engine == "mobius" and a2.voice == "yae")

    f = F5Provider(post2, "http://gsv:7871")
    a3 = run(f.synthesize(ProviderRequest(task_id="t3", kind="speech", payload={"text": "hi"})))
    check("f5 默认 voice=mobius / engine=f5", a3.voice == "mobius" and a3.engine == "f5")

    post3, _ = make_post(lambda url, payload: Resp(500))
    p3 = KokoroProvider(post3, "http://kokoro:7870")
    try:
        run(p3.synthesize(ProviderRequest(task_id="t", kind="speech", payload={"text": "x"})))
        check("HTTP 500 抛 ProviderError", False)
    except ProviderError as exc:
        check("HTTP 500 抛 ProviderError", exc.error_code == "tts_failed" and exc.status == 502)


def test_fallback() -> None:
    print("\n[fallback/routing]")
    def behavior(url, payload):
        if "gsv" in url:
            return Resp(500)
        return Resp(200, b"RIFFkokoro")

    cfg = SimpleNamespace(kokoro_url="http://kokoro:7870", f5_url="http://gsv:7871", tts_timeout=10.0)
    post, calls = make_post(behavior)
    reg = build_tts_registry(cfg, post)
    check("registry 含 kokoro/gsv/f5", {p.id for p in reg.all()} == {"kokoro", "gsv", "f5"})
    a = run(synthesize_with_fallback(reg, "mobius", "hi", "", 1.0))
    check("mobius 失败回退 kokoro", a.engine == "kokoro" and any("gsv" in u for u, _ in calls)
          and any("kokoro" in u for u, _ in calls))
    a2 = run(synthesize_with_fallback(reg, "kokoro", "hi", "", 1.0))
    check("kokoro 直接成功", a2.engine == "kokoro")

    # gsv 4xx -> ProviderError(tts_failed 502) 仍按 legacy 回退；业务错误在 Core/API 层通过参数校验处理
    def behavior_ok(url, payload):
        if "gsv" in url:
            return Resp(503)
        return Resp(200, b"RIFFkokoro")
    post2, _ = make_post(behavior_ok)
    reg2 = build_tts_registry(cfg, post2)
    a3 = run(synthesize_with_fallback(reg2, "f5", "hi", "", 1.0))
    check("f5 失败回退 kokoro", a3.engine == "kokoro")

    try:
        run(synthesize_with_fallback(reg, "unknown", "hi", "", 1.0))
        check("未知引擎拒绝", False)
    except ProviderError as exc:
        check("未知引擎拒绝", exc.error_code == "unknown_engine" and exc.status == 400)

    # kokoro 自身失败不无限回退
    def behavior_fail(url, payload):
        return Resp(500)
    post3, _ = make_post(behavior_fail)
    reg3 = build_tts_registry(cfg, post3)
    try:
        run(synthesize_with_fallback(reg3, "kokoro", "hi", "", 1.0))
        check("kokoro 失败不回退", False)
    except ProviderError:
        check("kokoro 失败不回退", True)


def test_synth_tts_paths() -> None:
    """legacy 与 adapters 两条 synth_tts 路径都实际落盘。"""
    print("\n[synth paths]")
    import server

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = b"RIFF" + b"0" * 200
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    tmp = Path(tempfile.mkdtemp(prefix="m2b-synth-"))
    cfg = SimpleNamespace(kokoro_url=f"http://127.0.0.1:{port}", f5_url=f"http://127.0.0.1:{port}",
                          tts_timeout=5.0, max_voice_bytes=8 * 1024 * 1024,
                          tts_dir=str(tmp), public_base="http://x")
    old_mode, old_reg = server.media_provider_mode, server.tts_registry
    old_env = os.environ.get("MOBIUS_MEDIA_PROVIDER")
    try:
        os.environ["MOBIUS_MEDIA_PROVIDER"] = "legacy"
        out = run(server.synth_tts(cfg, "你好", engine="kokoro", speed=1.1))
        check("legacy synth 落盘/URL", out.get("ok") is True and out.get("bytes") == 204
              and Path(out.get("local_path", "")).is_file(), str(out)[:120])

        from providers.tts import TTSAudio

        class FakeProvider:
            async def synthesize(self, req):
                return TTSAudio(audio=b"RIFF" + b"1" * 100, engine="kokoro",
                                voice="zf_xiaobei", speed=1.0)

        class FakeReg:
            def select(self, kind, preferred=None):
                return FakeProvider()

        server.media_provider_mode = lambda: "adapters"
        server.tts_registry = lambda cfg: FakeReg()
        out2 = run(server.synth_tts(cfg, "你好", engine="kokoro"))
        check("adapters synth 落盘/URL", out2.get("ok") is True and out2.get("bytes") == 104
              and Path(out2.get("local_path", "")).is_file(), str(out2)[:120])
    finally:
        server.media_provider_mode, server.tts_registry = old_mode, old_reg
        if old_env is None:
            os.environ.pop("MOBIUS_MEDIA_PROVIDER", None)
        else:
            os.environ["MOBIUS_MEDIA_PROVIDER"] = old_env
        httpd.shutdown()
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_static() -> None:
    print("\n[static wiring]")
    srv = (HERE / "server.py").read_text(encoding="utf-8")
    check("synth_tts 有 adapters 分支", 'media_provider_mode() == "adapters"' in srv)
    check("有 tts_registry", "def tts_registry(" in srv)
    check("公共收尾函数存在", "def _finalize_tts(" in srv)
    cfg = json.loads((HERE.parent / "config.example.json").read_text(encoding="utf-8"))
    check("config.example 有 speech 路由", (cfg.get("media") or {}).get("routing", {}).get("speech")
          == ["kokoro", "gsv"])


def main() -> int:
    test_providers()
    test_fallback()
    test_synth_tts_paths()
    test_static()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
