"""关闭 LangBot QQ 适配器的 token 级流式（改为整条发送），让图片 URL 桥接能工作。

用法（宿主）：
  python ops/patches/mobius_chat_streamoff_patch.py <chat.py 路径>
"""

from __future__ import annotations

import sys

OLD = "                    is_stream = await query.adapter.is_stream_output_supported()"
NEW = "                    is_stream = False  # Möbius: QQ 不做 token 级流式，整条发送以便图片 URL 桥接"


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: mobius_chat_streamoff_patch.py <chat.py>")
        return 2
    path = sys.argv[1]
    src = open(path, encoding="utf-8").read()
    if "Möbius: QQ 不做 token 级流式" in src:
        print("已打过补丁，跳过")
        return 0
    if OLD not in src:
        print("未找到流式判定，跳过")
        return 1
    try:
        open(path + ".mobius.bak", "x", encoding="utf-8").write(src)
        print("已备份原文件:", path + ".mobius.bak")
    except FileExistsError:
        pass
    open(path, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
    print("已关闭流式:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
