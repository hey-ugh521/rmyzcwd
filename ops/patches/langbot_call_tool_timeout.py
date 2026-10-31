"""LangBot CALL_TOOL 超时补丁（180s -> 600s）。

背景（2026-10-31 实测）：
  LangBot 调工具时 handler.py 里 CALL_TOOL 分支硬编码 timeout=180，
  而冷启动生图需要 263~296s（ComfyUI 冷启动 ~112s + 出图 60~140s + 排队），
  于是工具调用在 180s 被掐断 -> 运行失败 -> MCP 会话留在坏状态 -> 后续请求抱死。
  两次失败运行的时长都精确是 193.4s（180 + 13s 前置），与该硬编码吻合。

做法：只改 CALL_TOOL 那一处，不碰 emit_event / execute_command / retrieve_knowledge。
可重复执行（已改过则跳过）。容器重建（镜像更新）后需要重新执行本脚本。
"""
import shutil
import sys
import time

PATH = "/app/src/langbot/pkg/plugin/handler.py"
MARK = "LangBotToRuntimeAction.CALL_TOOL,"
OLD = "timeout=180,"
NEW = "timeout=600,"

src = open(PATH, encoding="utf-8").read()
idx = src.find(MARK)
if idx < 0:
    print("未找到 CALL_TOOL 分支，放弃")
    sys.exit(1)

tail = src[idx:]
pos = tail.find(OLD)
if pos < 0:
    # 检查是否已经打过补丁
    if tail.find(NEW) >= 0:
        print("已经是 600s，无需修改")
        sys.exit(0)
    print("CALL_TOOL 后未找到 timeout=180，放弃")
    sys.exit(1)

abs_pos = idx + pos
backup = f"{PATH}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(PATH, backup)
patched = src[:abs_pos] + NEW + src[abs_pos + len(OLD):]
open(PATH, "w", encoding="utf-8").write(patched)

print(f"补丁完成；备份: {backup}")
print(f"上下文: ...{patched[abs_pos - 120:abs_pos + len(NEW) + 20]}...")
