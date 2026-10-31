"""LangBot 回复里的图片 URL 桥接为图片消息。

背景（2026-10-31 23:24 实测）：
  MCP 网关返回的是内网地址（http://host.docker.internal:8899/img?filename=...），
  用户在 QQ 里打不开；LangBot 原样把链接当文本发出去，用户看到的是 "你把图放错地方了"。

做法（热补丁，可重复执行）：
  在 respback.py 里，把 resp_message_chain 交给适配器发送之前，
  扫描 Plain 段里的 URL，下载成功的图片转成 platform_message.Image(base64=...) 追加到链尾，
  并从文本里去掉该 URL。下载失败的保留原链接（不吞信息）。

容器重建后需要重新执行；watchdog 的 ensure_langbot_timeout_patches 会一并重打。
"""
import shutil
import sys
import time

PATH = "/app/src/langbot/pkg/pipeline/respback/respback.py"
HOOK_ANCHOR = "        message_chain = query.resp_message_chain[-1]\n"
HOOK_LINE = (
    "        message_chain = query.resp_message_chain[-1]\n"
    "        await _mobius_bridge_media_urls(self.ap.logger, message_chain)\n"
)

HELPER = '''

async def _mobius_bridge_media_urls(logger, message_chain) -> None:
    """把回复文本里的图片 URL 下载成 Image 段（内网地址用户打不开）。"""
    import base64
    import re

    import httpx

    pattern = re.compile(r"https?://[^\\s\\"'<>]+", re.IGNORECASE)
    urls: list[str] = []
    for seg in message_chain:
        if isinstance(seg, platform_message.Plain):
            for match in pattern.finditer(seg.text or ""):
                urls.append(match.group(0).rstrip("，。、）),.;"))
    if not urls:
        return

    images = []
    for url in urls:
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
        except Exception as exc:  # noqa: BLE001
            logger.warning("图片 URL 下载失败（保留原链接）: %s %s", url, exc)

    if images:
        message_chain.extend(images)
        logger.info("已把 %d 张图片 URL 转为图片消息", len(images))
'''

src = open(PATH, encoding="utf-8").read()
if "_mobius_bridge_media_urls" in src:
    print("桥接补丁已存在，无需修改")
    sys.exit(0)
if HOOK_ANCHOR not in src:
    print("未找到 message_chain 锚点，放弃")
    sys.exit(1)

backup = f"{PATH}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(PATH, backup)
patched = src.replace(HOOK_ANCHOR, HOOK_LINE, 1) + HELPER
open(PATH, "w", encoding="utf-8").write(patched)
print(f"桥接补丁完成；备份: {backup}")
