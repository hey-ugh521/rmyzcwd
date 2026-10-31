"""插件运行时「长操作」超时常量补丁（180s -> 600s）。

背景（2026-10-31 实测）：
  冷启动生图 230~305s，但插件运行时把一系列「长操作」（含 call_tool 转发）
  统一用 LONG_RUNNING_OPERATION_TIMEOUT = 180.0 卡死，
  于是 agent 的工具调用在 180s 被掐断，报 "Action call_tool call timed out"。

  完整链路需要同时放开三处（缺一不可）：
    1) LangBot 宿主 handler.py 的 CALL_TOOL        -> timeout=180
    2) 插件运行时 SDK 代理 langbot_api.py          -> timeout=180
    3) 插件运行时 plugin.py 的 LONG_RUNNING_OPERATION_TIMEOUT -> 180.0

可重复执行；容器重建后需要重新执行。
"""
import shutil
import sys
import time

PATH = "/app/.venv/lib/python3.12/site-packages/langbot_plugin/runtime/io/handlers/plugin.py"
OLD = "LONG_RUNNING_OPERATION_TIMEOUT = 180.0"
NEW = "LONG_RUNNING_OPERATION_TIMEOUT = 600.0"

src = open(PATH, encoding="utf-8").read()
if NEW in src:
    print("已经是 600s，无需修改")
    sys.exit(0)
if OLD not in src:
    print("未找到 LONG_RUNNING_OPERATION_TIMEOUT = 180.0，放弃")
    sys.exit(1)

backup = f"{PATH}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(PATH, backup)
open(PATH, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
print(f"补丁完成；备份: {backup}")
