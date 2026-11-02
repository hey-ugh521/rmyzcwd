"""把 Möbius 的图片 URL 桥接函数写入 LangBot 的 respback.py（可重复执行）。

用法（宿主）：
  python ops/patches/mobius_respback_patch.py <respback.py 路径>

行为：把文件里 `async def _mobius_bridge_media_urls(` 之后的全部内容替换为新实现，
      原文件备份为 <path>.mobius.bak（首次）。
"""

from __future__ import annotations

import sys

NEW_FN = r'''async def _mobius_bridge_media_urls(logger, message_chain) -> None:
    """把回复文本里的图片 URL 下载成 Image 段（内网地址用户打不开）。"""
    import base64
    import re

    import httpx

    md_image = re.compile(r"!?\[[^\]]*\]\((https?://[^)\s]+)\)")
    bare = re.compile(r"https?://[^\s\"'<>\]\)]+", re.IGNORECASE)
    trailing = "，。、）)]},.;:!?*_`\"'"

    def _clean(url: str) -> str:
        return url.rstrip(trailing)

    urls: list[str] = []
    for seg in message_chain:
        if isinstance(seg, platform_message.Plain):
            text = seg.text or ""
            for m in md_image.finditer(text):
                urls.append(m.group(1))
                text = text.replace(m.group(0), "")
            for m in bare.finditer(text):
                urls.append(_clean(m.group(0)))
            seg.text = text
    if not urls:
        return

    images = []
    for url in dict.fromkeys(urls):
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 200 and ctype.startswith("image/"):
                images.append(
                    platform_message.Image(base64=base64.b64encode(resp.content).decode())
                )
                for seg in message_chain:
                    if isinstance(seg, platform_message.Plain) and url in (seg.text or ""):
                        seg.text = (seg.text or "").replace(url, "").strip()
            else:
                logger.warning(
                    "图片 URL 下载失败（保留原链接）: %s HTTP %s %s", url, resp.status_code, ctype
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("图片 URL 下载失败（保留原链接）: %s %s", url, exc)
    if images:
        message_chain.extend(images)
        logger.info("已把 %d 张图片 URL 转为图片消息", len(images))
'''


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: mobius_respback_patch.py <respback.py>")
        return 2
    path = sys.argv[1]
    src = open(path, encoding="utf-8").read()
    marker = "async def _mobius_bridge_media_urls("
    idx = src.find(marker)
    if idx < 0:
        print("未找到目标函数，跳过")
        return 1
    bak = path + ".mobius.bak"
    try:
        open(bak, "x", encoding="utf-8").write(src)
        print(f"已备份原文件: {bak}")
    except FileExistsError:
        pass
    open(path, "w", encoding="utf-8").write(src[:idx] + NEW_FN)
    print(f"已写入新桥接函数: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
