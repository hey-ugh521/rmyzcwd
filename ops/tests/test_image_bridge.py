"""容器内：图片 URL 桥接函数边际测试（从运行中的 respback.py 提取真实函数并执行）。

运行：docker exec langbot /app/.venv/bin/python /tmp/test_image_bridge.py
"""

import asyncio
import builtins
import re
import sys

sys.path.insert(0, "/app/src")

from langbot_plugin.api.entities.builtin.platform.message import Image, MessageChain, Plain

PATCH = "/app/src/langbot/pkg/pipeline/respback/respback.py"
BASE = "http://host.docker.internal:8899/img?filename={}&subfolder=&type=output"
GOOD = BASE.format("mobius_qwen_00035_.png")
MISSING = BASE.format("definitely_missing_file.png")


class Logger:
    def __init__(self):
        self.lines = []

    def info(self, *a):
        self.lines.append(("INFO", a))

    def warning(self, *a):
        self.lines.append(("WARN", a))


def load_fn():
    src = open(PATCH, encoding="utf-8").read()
    idx = src.find("async def _mobius_bridge_media_urls(")
    assert idx >= 0, "函数不存在"
    ns = {
        "platform_message": type("pm", (), {"Plain": Plain, "Image": Image}),
        "re": re,
        "builtins": builtins,
    }
    exec(src[idx:], ns)  # noqa: S102
    return ns["_mobius_bridge_media_urls"]


async def case(name, fn, text, expect_images, expect_url_removed=True):
    chain = MessageChain([Plain(text=text)])
    log = Logger()
    try:
        await fn(log, chain)
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ {name}: 异常 {type(exc).__name__}: {exc}")
        return False
    n_img = sum(1 for c in chain if isinstance(c, Image))
    plain = "".join(c.text or "" for c in chain if isinstance(c, Plain))
    ok_img = n_img == expect_images
    ok_txt = ("http" not in plain) if (expect_images and expect_url_removed) else True
    ok = ok_img and ok_txt
    print(f"  {'✓' if ok else '✗'} {name}: images={n_img}(期望{expect_images}) 文本残留URL={'http' in plain}")
    return ok


async def main():
    fn = load_fn()
    results = []
    results.append(await case("裸 URL", fn, f"画好了 {GOOD}", 1))
    results.append(await case("加粗 **URL**", fn, f"**{GOOD}**", 1))
    results.append(await case("反引号 `URL`", fn, f"`{GOOD}`", 1))
    results.append(await case("Markdown 图片 ![x](url)", fn, f"![画]({GOOD})", 1))
    results.append(await case("Markdown 链接 [x](url)", fn, f"[点我]({GOOD})", 1))
    results.append(await case("中文标点结尾", fn, f"看这个：{GOOD}。", 1))
    GOOD2 = BASE.format("mobius_qwen_00033_.png")
    results.append(await case("两个不同 URL", fn, f"{GOOD}\n{GOOD2}", 2))
    results.append(await case("重复 URL 去重", fn, f"{GOOD} 和 {GOOD}", 1))
    results.append(await case("非图片 URL", fn, "http://host.docker.internal:8899/api/pulse", 0))
    results.append(await case("404 图片 URL", fn, MISSING, 0))
    results.append(await case("纯文本", fn, "今天天气不错，没有图。", 0))
    results.append(await case("URL 在正文中", fn, f"前面的话 {GOOD} 后面的话", 1))
    print("RESULT:", "PASS" if all(results) else "FAIL")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
