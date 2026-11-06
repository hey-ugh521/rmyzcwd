from __future__ import annotations

import random
import asyncio


import langbot_plugin.api.entities.builtin.platform.events as platform_events
import langbot_plugin.api.entities.builtin.platform.message as platform_message
import langbot_plugin.api.entities.builtin.provider.message as provider_message

from .. import stage, entities
from .. import plugin_diagnostics
import langbot_plugin.api.entities.builtin.pipeline.query as pipeline_query


@stage.stage_class('SendResponseBackStage')
class SendResponseBackStage(stage.PipelineStage):
    """发送响应消息"""

    async def process(self, query: pipeline_query.Query, stage_inst_name: str) -> entities.StageProcessResult:
        """处理"""

        random_range = (
            query.pipeline_config['output']['force-delay']['min'],
            query.pipeline_config['output']['force-delay']['max'],
        )

        random_delay = random.uniform(*random_range)

        self.ap.logger.debug('根据规则强制延迟回复: %s s', random_delay)

        await asyncio.sleep(random_delay)

        if query.pipeline_config['output']['misc']['at-sender'] and isinstance(
            query.message_event, platform_events.GroupMessage
        ):
            query.resp_message_chain[-1].insert(0, platform_message.At(target=query.message_event.sender.id))

        quote_origin = query.pipeline_config['output']['misc']['quote-origin']

        has_chunks = any(isinstance(msg, provider_message.MessageChunk) for msg in query.resp_messages)
        # TODO 命令与流式的兼容性问题
        response_index = len(query.resp_message_chain) - 1
        message_chain = query.resp_message_chain[-1]

        # Möbius: 群聊自动发言判断——模型不想回应时只输出 <pass/>，这里直接丢弃。
        _pass_markers = {"<pass/>", "<pass>", "</pass>", "pass", "[pass]", "（不回复）"}
        if message_chain and all(
            isinstance(seg, platform_message.Plain) for seg in message_chain
        ):
            _joined = " ".join((seg.text or "").strip() for seg in message_chain).strip().lower()
            if _joined in _pass_markers or _joined == "":
                self.ap.logger.info("mobius: 群聊发言判断为不回应，已跳过发送")
                return entities.StageProcessResult(
                    result_type=entities.ResultType.CONTINUE, new_query=query
                )

        await _mobius_bridge_media_urls(self.ap.logger, message_chain)

        # Möbius: 语音段单独成条发送。QQ 手机端对「文本+语音」混合消息只渲染文本，
        # 语音会被吞掉；拆成独立消息后可以正常显示为语音气泡。
        voice_segments = [seg for seg in message_chain if isinstance(seg, platform_message.Voice)]
        if voice_segments:
            for seg in list(voice_segments):
                message_chain.remove(seg)
            try:
                for seg in voice_segments:
                    await query.adapter.reply_message(
                        message_source=query.message_event,
                        message=[seg],
                        quote_origin=False,
                    )
            except Exception as exc:  # noqa: BLE001
                self.ap.logger.warning('mobius voice segment delivery failed: %s', exc)
                message_chain.extend(voice_segments)
            if not message_chain:
                return entities.StageProcessResult(
                    result_type=entities.ResultType.CONTINUE, new_query=query
                )

        is_streaming_response = False
        is_final = False
        try:
            is_streaming_response = await query.adapter.is_stream_output_supported() and has_chunks
            if is_streaming_response:
                is_final = query.resp_messages[-1].is_final
                await query.adapter.reply_message_chunk(
                    message_source=query.message_event,
                    bot_message=query.resp_messages[-1],
                    message=message_chain,
                    quote_origin=quote_origin,
                    is_final=is_final,
                )
            else:
                await query.adapter.reply_message(
                    message_source=query.message_event,
                    message=message_chain,
                    quote_origin=quote_origin,
                )
        except Exception as e:
            await plugin_diagnostics.notify_response_delivery_failure(
                self.ap,
                query,
                response_index,
                message_chain,
                e,
            )
            plugin_diagnostics.clear_response_source(query, response_index)
            if is_streaming_response:
                self.ap.logger.warning(
                    'Streaming response delivery failed; continuing runner event consumption: %s',
                    e,
                )
                if is_final:
                    try:
                        await query.adapter.reply_message(
                            message_source=query.message_event,
                            message=message_chain,
                            quote_origin=quote_origin,
                        )
                    except Exception as fallback_error:
                        self.ap.logger.warning(
                            'Final non-streaming response fallback also failed: %s',
                            fallback_error,
                        )
                return entities.StageProcessResult(result_type=entities.ResultType.CONTINUE, new_query=query)
            raise
        plugin_diagnostics.clear_response_source(query, response_index)

        return entities.StageProcessResult(result_type=entities.ResultType.CONTINUE, new_query=query)


async def _mobius_bridge_media_urls(logger, message_chain) -> None:
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
