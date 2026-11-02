import asyncio
import sys
import urllib.request

sys.path.insert(0, "/app/src")

from langbot_plugin.api.entities.builtin.platform.message import Image, MessageChain, Plain, Voice

PATCH = "/app/src/langbot/pkg/pipeline/respback/respback.py"


class L:
    def info(self, *a):
        print("INFO", *a)

    def warning(self, *a):
        print("WARN", *a)


def load():
    src = open(PATCH, encoding="utf-8").read()
    i = src.find("async def _mobius_bridge_media_urls(")
    ns = {"platform_message": type("pm", (), {"Plain": Plain, "Image": Image, "Voice": Voice})}
    exec(src[i:], ns)  # noqa: S102
    return ns["_mobius_bridge_media_urls"]


async def main():
    url = "http://host.docker.internal:8899/audio?file=tts_1793640089_561.wav"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            print(" 网关音频:", r.status, r.headers.get("content-type"))
    except Exception as exc:  # noqa: BLE001
        print(" 网关音频失败:", type(exc).__name__, str(exc)[:100])

    fn = load()
    cases = [
        ("只有文件名", "duration: 18.82s / 903,644 bytes / tts_1793640089_561.wav"),
        ("文件名加粗", "**tts_1793640089_561.wav**"),
        ("完整音频URL", url),
        ("文件名+正文", "语音在这里：tts_1793640089_561.wav 请查收"),
    ]
    for name, text in cases:
        chain = MessageChain([Plain(text=text)])
        try:
            await fn(L(), chain)
            print(f"  {name}: {[type(c).__name__ for c in chain]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: 异常 {type(exc).__name__} {exc}")


asyncio.run(main())
