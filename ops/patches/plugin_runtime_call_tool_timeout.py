"""插件运行时 SDK 代理的 call_tool 超时补丁（180s -> 600s）。

背景（2026-10-31 实测）：
  冷启动生图需要 263~305s（ComfyUI 冷启动 ~110s + 出图 60~140s + 排队），
  但插件运行时里的 SDK 代理 langbot_api.py 把 call_tool 的 timeout 硬编码成 180s，
  于是工具调用在 180s 被掐断，报 "Action call_tool call timed out"，
  随后 MCP 会话清理失败（BlockingWorkCapacityError），后续请求全挂。

  注意：LangBot 宿主侧 handler.py 也有一个 timeout=180（不同一跳），
  两处都要放开，否则仍会在某一跳被掐断。

做法：只改 PluginToRuntimeAction.CALL_TOOL 那一处。可重复执行。
容器重建（镜像更新 / 插件运行时重装）后需要重新执行。
"""
import shutil
import sys
import time

PATH = "/app/.venv/lib/python3.12/site-packages/langbot_plugin/api/proxies/langbot_api.py"
MARK = "PluginToRuntimeAction.CALL_TOOL,"
OLD = "timeout=180,"
NEW = "timeout=600,"

src = open(PATH, encoding="utf-8").read()
idx = src.find(MARK)
if idx < 0:
    print("未找到 CALL_TOOL 代理，放弃")
    sys.exit(1)

tail = src[idx:]
pos = tail.find(OLD)
if pos < 0:
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
print("上下文:", patched[abs_pos - 100:abs_pos + len(NEW) + 10].replace("\n", " | "))
