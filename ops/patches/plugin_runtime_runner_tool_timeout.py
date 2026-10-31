"""Runner 工具调用超时补丁（两处常量）。

完整链路（2026-10-31 实测，冷启动生图 230~305s 会连续被掐断）：

  1) 插件运行时 runner 代理 tools.call_tool 的 default=180
     -> /app/.venv/.../langbot_plugin/api/proxies/runner/resources.py
  2) 上面这层还会被 min(base_timeout, 剩余运行期限) 再夹一次
     -> 运行期限默认 300s，来自 LangBot 宿主 runner_config.timeout
     -> 宿主侧常量 DEFAULT_RUNNER_TIMEOUT_SECONDS = 300

本脚本在插件运行时容器内把 (1) 改成 600；宿主侧 (2) 由另一个脚本处理。
可重复执行；容器重建后需重新执行。
"""
import shutil
import sys
import time

RESOURCES = "/app/.venv/lib/python3.12/site-packages/langbot_plugin/api/proxies/runner/resources.py"
OLD = "timeout = self._bounded_timeout(default=180.0)"
NEW = "timeout = self._bounded_timeout(default=600.0)"

src = open(RESOURCES, encoding="utf-8").read()
if NEW in src:
    print("resources.py 已是 600s，无需修改")
    sys.exit(0)
if OLD not in src:
    print("未找到 tools.call_tool 的 default=180.0，放弃")
    sys.exit(1)

backup = f"{RESOURCES}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
shutil.copy2(RESOURCES, backup)
open(RESOURCES, "w", encoding="utf-8").write(src.replace(OLD, NEW, 1))
print(f"resources.py 补丁完成；备份: {backup}")
