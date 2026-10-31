"""LangBot 宿主侧运行期限补丁（300s -> 900s）。

run 的总期限来自 context_builder 里的 DEFAULT_RUNNER_TIMEOUT_SECONDS，
而工具调用超时会被 min(工具超时, 剩余期限) 再夹一次。
冷启动生图 230~305s，默认 300s 期限太贴边，需放宽到 900s。

作用位置：/app/src/langbot/pkg/agent/runner/context_builder.py
可重复执行；容器重建（镜像更新）后需重新执行。
"""
import shutil
import sys
import time

PATH = "/app/src/langbot/pkg/agent/runner/context_builder.py"
OLD = "DEFAULT_RUNNER_TIMEOUT_SECONDS = 300"
NEW = "DEFAULT_RUNNER_TIMEOUT_SECONDS = 900"

src = open(PATH, encoding="utf-8").read()
if NEW in src:
    print("已是 900s，无需修改")
    sys.exit(0)
if OLD not in src:
    print("未找到 DEFAULT_RUNNER_TIMEOUT_SECONDS = 300，放弃")
    sys.exit(1)

backup = f"{PATH}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(PATH, backup)
open(PATH, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
print(f"补丁完成；备份: {backup}")
