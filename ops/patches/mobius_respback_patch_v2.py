"""Möbius 媒体桥接补丁 v2：图片→Image，音频 URL/文件名→Voice。

用法（宿主）：
  python ops/patches/mobius_respback_patch_v2.py <respback.py 路径>

修复：旧版在"只有音频文件名"时提前 return；且文件名会误伤 URL 里的文件名。
"""

from __future__ import annotations

import sys

NEW_FN = r'''async def _mobius_bridge_media_urls(logger, message_chain) -> None:
    """把回复文本里的图片/音频 URL 下载成 Image/Voice 段（内网地址用户打不开）。"""
    import base64
    import os
    import re

    import httpx

    md_media = re.compile(r"!?\[[^\]]*\]\((https?://[^)\s]+)\)")
    bare = re.compile(r"https?://[^\s\"'<>\]\)]+", re.IGNORECASE)
    audio_name = re.compile(r"(tts_[A-Za-z0-9_\-]+\.(?:wav|mp3|ogg|opus|silk))", re.IGNORECASE)
    audio_base = os.environ.get("MOBIUS_AUDIO_BASE", "http://host.docker.internal:8899/audio?file=")
    trailing = "，。、）)]},.;:!?*_`\"'"

    def _clean(url: str) -> str:
        return url.rstrip(trailing)

    candidates: list[str] = []
    for seg in message_chain:
        if not isinstance(seg, platform_message.Plain):
            continue
        text = seg.text or ""
        for m in list(md_media.finditer(text)):
            candidates.append(_clean(m.group(1)))
            text = text.replace(m.group(0), "")
        for m in list(bare.finditer(text)):
            candidates.append(_clean(m.group(0)))
            text = text.replace(m.group(0), "")
        for m in list(audio_name.finditer(text)):
            candidates.append(audio_base + m.group(1))
            text = text.replace(m.group(1), "")
        seg.text = text

    if not candidates:
        return

    media = []
    for url in dict.fromkeys(candidates):
        if not url or url.endswith("file="):
            continue
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(url)
            ctype = resp.headers.get("content-type", "")
            if resp.status_code != 200:
                logger.warning("媒体 URL 下载失败（保留原链接）: %s HTTP %s %s", url, resp.status_code, ctype)
                continue
            if ctype.startswith("image/"):
                media.append(platform_message.Image(base64=base64.b64encode(resp.content).decode()))
            elif ctype.startswith("audio/"):
                media.append(platform_message.Voice(base64=base64.b64encode(resp.content).decode()))
            else:
                logger.warning("媒体 URL 类型未知（保留原链接）: %s %s", url, ctype)
        except Exception as exc:  # noqa: BLE001
            logger.warning("媒体 URL 下载失败（保留原链接）: %s %s", url, exc)
    if media:
        message_chain.extend(media)
        logger.info("已把 %d 个媒体 URL 转为媒体消息（图片/语音）", len(media))
'''


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: mobius_respback_patch_v2.py <respback.py>")
        return 2
    path = sys.argv[1]
    src = open(path, encoding="utf-8").read()
    idx = src.find("async def _mobius_bridge_media_urls(")
    if idx < 0:
        print("未找到目标函数，跳过")
        return 1
    open(path, "w", encoding="utf-8").write(src[:idx] + NEW_FN)
    print(f"已写入新桥接函数: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
