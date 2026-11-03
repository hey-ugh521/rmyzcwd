"""把 LangBot 回复中的语音段拆成独立消息发送（QQ 对"文本+语音"混合消息只显示文字）。

用法（宿主）：
  python ops/patches/mobius_respback_split_voice.py <respback.py 路径>

幂等：已打过补丁则跳过。
"""

from __future__ import annotations

import sys

ANCHOR = "        await _mobius_bridge_media_urls(self.ap.logger, message_chain)\n"

BLOCK = '''        await _mobius_bridge_media_urls(self.ap.logger, message_chain)

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
'''


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: mobius_respback_split_voice.py <respback.py>")
        return 2
    path = sys.argv[1]
    src = open(path, encoding="utf-8").read()
    if "voice_segments = [seg for seg in message_chain" in src:
        print("已打过补丁，跳过")
        return 0
    if ANCHOR not in src:
        print("未找到桥接调用锚点，跳过")
        return 1
    open(path, "w", encoding="utf-8").write(src.replace(ANCHOR, BLOCK, 1))
    print(f"已写入语音拆分逻辑: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
