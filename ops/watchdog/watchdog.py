"""LangBot + NapCat + ComfyUI MCP 看门狗：检测静默失效并自动重启。

覆盖三类已实际发生的故障：

A. LangBot 流水线挂起
   事件已经投递进流水线，但既不调用模型也不产生回复，后续消息全部堵塞。
   判定：最后一条「delivered to Pipeline」事件距今 > STUCK_SECONDS，
         且它的时间晚于最近一次 LLM 调用（说明它没被处理）。

B. NapCat 静默假死
   容器活着、WebSocket 连着、get_login_info 还能返回缓存数据，
   但推送钩子已死，一条消息事件都不再发出；或 HTTP API 直接超时。
   判定：get_status 连续 FAIL_THRESHOLD 次超时/失败。

C. ComfyUI MCP Server 未运行
   生图工具依赖宿主机 8899 端口的 MCP Server，进程退出后机器人再也调不到生图。
   判定：本地 8899 端口无法建立 TCP 连接，则后台拉起进程。

用法：
    python watchdog.py                # 前台持续运行
    python watchdog.py --once         # 只检查一次（适合计划任务）
    python watchdog.py --dry-run      # 只报告不重启
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request

NAPCAT_API = "http://127.0.0.1:13000"   # 容器 3000 已挪到宿主机 13000，避开 Open WebUI 的 3000
LANGBOT_API = "http://127.0.0.1:5300"
API_KEY = "lbk-ROTATED-REMOVED"
BOT_ID = "f128506b-a8aa-417c-9b55-39bc2fc99894"
DB = r"C:\Users\lenovo\Desktop\_说明文档\qqbot\langbot\data\langbot.db"

MCP_PORT = 8899
MCP_PYTHON = r"D:\AI\qqbot-mcp\Scripts\python.exe"
MCP_SCRIPT = r"D:\AI\qqbot-mcp\comfyui_mcp.py"
MCP_LOG = r"D:\AI\qqbot-mcp\mcp_server.log"

# Möbius Core：宿主媒体服务的唯一监管者（8900）。
# Core 活着时，8899/7870/8188 的启停交给它，本看门狗不再重复接管；
# Core 挂掉时回退到旧行为，保证「重启就废」不会重演。
CORE_PORT = 8900
CORE_PYTHON = r"D:\Anaconda3\envs\open-webui\python.exe"
CORE_CWD = r"C:\Users\lenovo\Desktop\_说明文档\mobius"
CORE_LOG = r"C:\Users\lenovo\Desktop\_说明文档\mobius\logs\core.watchdog.log"

TTS_PORT = 7870
TTS_PYTHON = r"D:\Anaconda3\envs\open-webui\python.exe"
TTS_SCRIPT = r"D:\TTS_Workspace\kokoro_openai_api.py"
TTS_LOG = r"D:\TTS_Workspace\kokoro_api.log"
TTS_ARGS = ["--port", str(TTS_PORT)]

STUCK_SECONDS = 150      # 消息投递后多久没被处理算卡死
STUCK_MAX_SECONDS = 900  # 超过这个时长还没处理，说明读数已过期（不是真卡死），不要重启
FAIL_THRESHOLD = 2       # NapCat 连续失败几次判定假死
COOLDOWN_SECONDS = 300   # 两次重启同一容器之间的最小间隔

# 【D. MCP 会话失效】
# Media Gateway(8899) 是「有状态」的 MCP 服务：一个进程对应一组会话。
# 网关重启后，LangBot 手里的 MCP SSE 流会变成死流，而且它**不会重连**——
# 工具调用挂在死流上，直到 300s 超时才报 "call_tool call timed out"，
# 表现就是「让它用 LoRA 生图，Agent 报错」，而网关日志里一条请求都没有。
# 唯一可靠的自愈点：网关启动时间晚于 LangBot 容器启动时间 ⇒ 重启 LangBot 重建会话。
GATEWAY_STATUS = "http://127.0.0.1:8899/api/pulse"   # 轻量心跳，不探测后端（/api/status 最坏 9s）
SESSION_SKEW_SECONDS = 20   # 允许的启动时间误差（网关先起、LangBot 后起是正常顺序）

_state = {
    "napcat_fail": 0,
    "last_restart": {"napcat": 0.0, "langbot": 0.0},
    "last_start": {"mcp": 0.0, "tts": 0.0, "core": 0.0},
}

# ---------------------------------------------------------------- QQ 登录态三级降级
# 背景：QQ 被风控踢下线后，NapCat 会自己重启 QQ worker，但 worker 重启**不带 -q**
# （entrypoint 只在容器启动时传 `-q $ACCOUNT`），于是退回二维码，机器人静默离线。
# 实测本机 WebUI 的 HTTP 接口能一步快速登录，等价于人工点「已有账号」，无需重启容器。
#
# 降级链：L1 快速登录 → L2 重启容器 → L3 停手告警（30 分钟内不再自动动作）
# 设计原则：每级都有"成功判据"（复查状态），绝不"发出请求就当成功"。
NAPCAT_WEBUI = "http://127.0.0.1:6099"
NAPCAT_WEBUI_PORT = 6099
NAPCAT_WEBUI_CONFIG = r"C:\Users\lenovo\Desktop\_说明文档\qqbot\napcat\napcat\config\webui.json"
NAPCAT_ACCOUNT = "3947356403"
QQ_STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "state", "qq_login.json"
)
QQ_GRACE_SECONDS = 90      # 掉线后先给 NapCat 自己恢复的时间，别抢它的流程
QQ_L1_MAX_FAILS = 3        # 快速登录连续失败 N 次 → 升级 L2
QQ_L2_MAX_FAILS = 2        # 容器重启连续失败 N 次 → 升级 L3
QQ_L1_COOLDOWN = 300       # 两次 L1 尝试的最小间隔
QQ_L2_COOLDOWN = 900       # 两次 L2 尝试的最小间隔
QQ_L3_HOLD = 1800          # L3 之后多久内不再自动动作
QQ_HTTP_TIMEOUT = 6

_qq_cred = {"value": "", "at": 0.0}

# ---------------------------------------------------------------- LangBot MCP 工具故障自愈
# 2026-10-31 02:58 群聊实测：请求进来时 MCP 会话需要重建，但清理动作抢不到
# 「工作区阻塞线程池」名额（BlockingWorkCapacityError），清理失败后会话停在重连态，
# 工具调用一直等 → 约 180s 运行期限被砍，报 "call_tool call timed out"。
# 更隐蔽的是**之后 comfyui 工具集静默消失**（网关再无新 transport），
# Agent 只能改用 visit_web 之类工具，表现为「生图不工作但也不报错」。
# 这里在失败签名出现后重建 LangBot（重建所有 MCP 会话），并用冷却+已处理水位防止风暴。
LANGBOT_LOG_DIR = r"C:\Users\lenovo\Desktop\_说明文档\qqbot\langbot\data\logs"
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
LANG_TOOL_FAILURE_PATTERNS = (
    "BlockingWorkCapacityError",
    "call_tool call timed out",
    "Error cleaning up MCP transport",
)
LANG_TOOL_FAILURE_WINDOW = 900        # 只认最近 15 分钟内的失败
LANG_TOOL_LOG_TAIL_BYTES = 500_000    # 只读日志尾部，避免整文件扫描
_state["last_tool_failure_handled"] = 0.0


def _parse_log_ts(line):
    """从 '[10-31 03:07:35.828] ...' 解析本地时间戳（日志不含年份，补当年）。

    注意：LangBot 的 WARNING/ERROR 行前面带 ANSI 颜色码（\\x1b[33m 等），
    必须先剥掉，否则 startswith("[") 判定失败、时间戳解析失败，
    整类故障会被静默漏检（2026-11-01 实测踩到）。
    """
    line = _ANSI_RE.sub("", line)
    if not line.startswith("[") or "]" not in line:
        return None
    stamp = line[1:line.index("]")].strip().split(".")[0]
    try:
        # Windows 的 mktime 不接受 1900 年，必须补年份
        return time.mktime(time.strptime(f"{time.strftime('%Y')}-{stamp}", "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def langbot_recent_tool_failures(window=LANG_TOOL_FAILURE_WINDOW):
    """返回窗口内的 (时间戳, 内容) 列表。"""
    path = os.path.join(LANGBOT_LOG_DIR, f"langbot-{time.strftime('%Y-%m-%d')}.log")
    if not os.path.exists(path):
        return []
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if size > LANG_TOOL_LOG_TAIL_BYTES:
            f.seek(size - LANG_TOOL_LOG_TAIL_BYTES)
        text = f.read().decode("utf-8", "replace")
    cutoff = time.time() - window
    hits = []
    for line in text.splitlines():
        if not any(p in line for p in LANG_TOOL_FAILURE_PATTERNS):
            continue
        ts = _parse_log_ts(line)
        if ts is not None and ts >= cutoff:
            hits.append((ts, line.strip()[:160]))
    return hits


# ---------------------------------------------------------------- LangBot 超时补丁自愈
# 2026-10-31 事故：冷启动生图要 230~305s，但链路上有 5 层 180s/300s 超时，
# 任意一层都会把工具调用掐断，而工具调用长于上限时会留下坏会话 → 抱死。
# 五处补丁（详见 ops/patches/）：
#   langbot             : handler.py CALL_TOOL 180->600
#   langbot             : context_builder.py DEFAULT_RUNNER_TIMEOUT_SECONDS 300->900
#   langbot_plugin_runtime: langbot_api.py CALL_TOOL 180->600
#   langbot_plugin_runtime: plugin.py LONG_RUNNING_OPERATION_TIMEOUT 180->600
#   langbot_plugin_runtime: runner/resources.py tools.call_tool 180->600
# 这些补丁写在容器文件系统里，容器被重建（compose up --force-recreate / 镜像更新）就会丢失，
# 所以每轮检查一次，缺了就补。
PATCH_DIR = r"C:\Users\lenovo\Desktop\_说明文档\mobius\ops\patches"
PATCH_MARKERS = (
    ("langbot", "/app/src/langbot/pkg/plugin/handler.py", "timeout=600,", "langbot_call_tool_timeout.py"),
    ("langbot", "/app/src/langbot/pkg/agent/runner/context_builder.py",
     "DEFAULT_RUNNER_TIMEOUT_SECONDS = 900", "langbot_runner_deadline.py"),
    ("langbot_plugin_runtime", "/app/.venv/lib/python3.12/site-packages/langbot_plugin/api/proxies/langbot_api.py",
     "timeout=600,", "plugin_runtime_call_tool_timeout.py"),
    ("langbot_plugin_runtime", "/app/.venv/lib/python3.12/site-packages/langbot_plugin/runtime/io/handlers/plugin.py",
     "LONG_RUNNING_OPERATION_TIMEOUT = 600.0", "plugin_runtime_long_ops_timeout.py"),
    ("langbot_plugin_runtime", "/app/.venv/lib/python3.12/site-packages/langbot_plugin/api/proxies/runner/resources.py",
     "default=600.0", "plugin_runtime_runner_tool_timeout.py"),
    ("langbot", "/app/src/langbot/pkg/pipeline/respback/respback.py",
     "_mobius_bridge_media_urls", "langbot_image_url_bridge.py"),
)


def ensure_langbot_timeout_patches(dry_run=False):
    missing = []
    for container, path, marker, script in PATCH_MARKERS:
        try:
            r = subprocess.run(
                ["docker", "exec", container, "grep", "-c", marker, path],
                capture_output=True, text=True, timeout=25,
            )
            if r.returncode != 0 or (r.stdout or "").strip() in ("", "0"):
                missing.append((container, script))
        except Exception as exc:
            log(f"超时补丁检查失败：{type(exc).__name__}: {exc}")
            return
    if not missing:
        return
    log(f"超时补丁缺失 {len(missing)} 处（容器可能被重建过）：{[s for _, s in missing]}")
    if dry_run:
        log("超时补丁：[dry-run] 不执行")
        return
    seen = set()
    for container, script in missing:
        src = os.path.join(PATCH_DIR, script)
        if not os.path.exists(src):
            log(f"超时补丁：找不到脚本 {src}")
            continue
        dst = f"/tmp/{script}"
        subprocess.run(["docker", "cp", src, f"{container}:{dst}"], capture_output=True, timeout=60)
        interp = "/app/.venv/bin/python" if container == "langbot" else "python"
        r = subprocess.run(["docker", "exec", container, interp, dst], capture_output=True, text=True, timeout=120)
        log(f"  重打 {script}：{(r.stdout or r.stderr or '').strip()[:120]}")
        seen.add(container)
    if seen:
        for c in seen:
            restart(c, False, "超时补丁已重打，重启使补丁生效")


def check_langbot_tool_failure(dry_run):
    hits = langbot_recent_tool_failures()
    if not hits:
        return
    newest_ts, newest_line = max(hits, key=lambda x: x[0])
    if newest_ts <= _state["last_tool_failure_handled"]:
        return  # 同一批失败已处理过
    why = f"MCP 工具调用失败（{len(hits)} 条，最近：{newest_line[:90]}），重建 MCP 会话"
    started = restart("langbot", dry_run, why)
    if started or dry_run:
        _state["last_tool_failure_handled"] = newest_ts

# ---------------------------------------------------------------- 会话图片中毒清理
# 背景（2026-10-30 实测）：用户在 QQ 里发一张图，LangBot 会把图片写进会话历史，
# content_json 形如 {"type":"image_url","image_url":{"url":"https://multimedia.nt.qq.com.cn/..."}}。
# 该 URL 是腾讯 CDN 带 rkey 的临时地址，很快失效；DeepSeek 每轮都会尝试下载历史里的图片，
# 下载失败直接返回 HTTP 400 → LangBot 报 "Invalid request parameters"，
# 症状是「这个会话从此每条消息都失败，而新建会话正常」。
# 这里每轮清理这类历史行；只匹配真实图片成分（"image_url": {），不会误伤纯文本消息
# （纯文本消息里是 "image_url": null）。监控/审计表不受影响。
IMAGE_POISON_PATTERN = '%"image_url": {%'

# ---------------------------------------------------------------- 信息探针（只读）
# 每 5 分钟把「消息 -> run -> 工具调用 -> 任务」的状态轨迹写进 ops/probe/state.db，
# 每个节点带日志同源时间戳，方便建过程树排错。
# 纯只读旁路：不参与重启决策，不改生产数据。
PROBE_SCRIPT = r"C:\Users\lenovo\Desktop\_说明文档\mobius\ops\probe\probe_state.py"
_probe_state = {"last": 0.0}


def check_state_probe(dry_run=False):
    now = time.time()
    if now - _probe_state["last"] < 300:  # 5 分钟一次
        return
    _probe_state["last"] = now
    if not os.path.exists(PROBE_SCRIPT):
        return
    if dry_run:
        log("信息探针：[dry-run] 跳过采集")
        return
    try:
        r = subprocess.run(
            [sys.executable, PROBE_SCRIPT, "--once"],
            capture_output=True, text=True, timeout=120,
        )
        tail = (r.stdout or "").strip().splitlines()
        log(f"信息探针：{tail[-1] if tail else '无输出'}")
    except Exception as exc:
        log(f"信息探针失败：{type(exc).__name__}: {exc}")







# ---------------------------------------------------------------- DSH 两段式自愈（D2）
# 规则：同一类故障 30 分钟内第 2 次 → 唤起 DSH 只读诊断（read-only profile），
#       产出 logs/incidents/*.md；不自动执行任何处置动作。
DSH_NODE = r"D:\Program Files\nodejs\node.exe"
DSH_BIN = r"D:\AI\tools\npm-global\node_modules\@deepseek-ai\dsh\lib\bin.js"
DSH_PATCH = r"C:\Users\lenovo\Desktop\_说明文档\mobius\ops\dsh\mobius-ops.yml"
INCIDENT_DIR = r"C:\Users\lenovo\Desktop\_说明文档\mobius\logs\incidents"
INCIDENT_STATE = r"C:\Users\lenovo\Desktop\_说明文档\qqbot\.incident_state.json"
INCIDENT_WINDOW_SEC = 1800
INCIDENT_REPEAT = 2
INCIDENT_COOLDOWN_SEC = 21600  # 同因故障 6 小时内只诊断一次


def _incident_key(why):
    import re
    return re.sub(r"[0-9]+", "N", str(why)).strip()[:80]


def _incident_state_load():
    try:
        return json.loads(open(INCIDENT_STATE, encoding="utf-8").read())
    except Exception:
        return {}


def _incident_state_save(st):
    try:
        open(INCIDENT_STATE, "w", encoding="utf-8").write(json.dumps(st, ensure_ascii=False))
    except Exception:
        pass


def _log_tail_for_incident(n=60):
    try:
        return "\n".join(open(LOG_FILE, encoding="utf-8", errors="replace").read().splitlines()[-n:])
    except Exception:
        return ""


def _dsh_available():
    return os.path.exists(DSH_NODE) and os.path.exists(DSH_BIN) and os.path.exists(DSH_PATCH)


def _escalate_incident(why, dry_run=False):
    """同因故障重复出现时唤起 DSH 只读诊断，写 incident 报告。返回是否已升级。"""
    if dry_run:
        log(f"DSH 升级：[dry-run] 将诊断：{why}")
        return True
    if not _dsh_available():
        log("DSH 升级：dsh 或 patch 不存在，跳过（降级为纯规则）")
        return False
    state = _incident_state_load()
    key = _incident_key(why)
    now_ts = time.time()
    rec = state.get(key) or {"count": 0, "first_ts": now_ts, "last_diag_ts": 0}
    if now_ts - float(rec.get("last_diag_ts") or 0) < INCIDENT_COOLDOWN_SEC:
        return False
    os.makedirs(INCIDENT_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = "".join(ch if ch.isalnum() else "-" for ch in key)[:40].strip("-")
    report = os.path.join(INCIDENT_DIR, f"incident-{stamp}-{slug}.md")
    prompt = (
        "你是 Möbius 的只读运维诊断 agent。请用 mobius MCP 工具（mobius_status / task_list / "
        "message_state / log_tail / pg_health）诊断以下故障，禁止提出或执行任何写操作。\n\n"
        f"【故障原因】{why}\n\n"
        f"【watchdog 最近日志】\n{_log_tail_for_incident(60)}\n\n"
        "输出 Markdown 报告：1) 现象与证据 2) 可能根因（按可能性排序）3) 建议的处置动作"
        "（仅供人工确认）4) 复现条件。不要臆造数据，读不到就如实说明。"
    )
    try:
        fh = open(report, "w", encoding="utf-8")
        fh.write(f"# Incident: {why}\n\n- 触发时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                 f"- watchdog 判断：同类故障 30 分钟内重复出现，升级 DSH 只读诊断\n\n---\n\n")
        fh.flush()
        errfh = open(report + ".stderr.log", "w", encoding="utf-8")
        subprocess.Popen(
            [DSH_NODE, DSH_BIN, "--profile", "headless", "--patch", DSH_PATCH, prompt],
            stdout=fh, stderr=errfh,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            cwd=r"C:\Users\lenovo\Desktop\_说明文档\mobius",
        )
        rec.update({"count": int(rec.get("count") or 0) + 1, "last_ts": now_ts,
                    "last_diag_ts": now_ts, "report": report})
        state[key] = rec
        _incident_state_save(state)
        log(f"DSH 升级：已唤起只读诊断，报告将写入 {report}")
        return True
    except Exception as exc:
        log(f"DSH 升级失败：{type(exc).__name__}: {exc}")
        return False


def check_incident_escalation(why, dry_run=False):
    """在 restart() 记录故障时调用：同因第 2 次触发 DSH 诊断。"""
    state = _incident_state_load()
    key = _incident_key(why)
    now_ts = time.time()
    rec = state.get(key) or {"count": 0, "first_ts": now_ts, "last_diag_ts": 0}
    if now_ts - float(rec.get("first_ts") or now_ts) > INCIDENT_WINDOW_SEC:
        rec = {"count": 0, "first_ts": now_ts, "last_diag_ts": rec.get("last_diag_ts") or 0}
    rec["count"] = int(rec.get("count") or 0) + 1
    rec["last_ts"] = now_ts
    state[key] = rec
    _incident_state_save(state)
    if rec["count"] >= INCIDENT_REPEAT:
        return _escalate_incident(why, dry_run)
    return False

# ---------------------------------------------------------------- NapCat 重启后重建 OneBot 连接
# 背景（2026-11-02）：断网 → NapCat 重登/容器重启 → LangBot 的 forward_ws 连接变僵死，
# 内核仍显示 ESTABLISHED，但 LangBot 收不到任何事件（静默丢消息），只能手动重启 LangBot。
NAPCAT_STATE_FILE = r"C:\Users\lenovo\Desktop\_说明文档\qqbot\.napcat_started_at"


def _container_started_at(name):
    try:
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}}", name],
                           capture_output=True, text=True, timeout=15)
        return (r.stdout or "").strip() or None
    except Exception:
        return None


def _napcat_online():
    token = _napcat_token()
    try:
        data = http_get(f"{NAPCAT_API}/get_status", timeout=6,
                        headers={"Authorization": f"Bearer {token}"})
        return bool((data.get("data") or {}).get("online"))
    except Exception:
        return False


def check_napcat_restart_relink(dry_run=False):
    """NapCat 容器重启后，等它恢复在线，主动重启 LangBot 重建 OneBot 连接。"""
    started = _container_started_at("napcat")
    if not started:
        return
    try:
        previous = open(NAPCAT_STATE_FILE, encoding="utf-8").read().strip()
    except OSError:
        previous = ""
    if previous == started:
        return
    if not previous:
        open(NAPCAT_STATE_FILE, "w", encoding="utf-8").write(started)
        return
    # 容器重启了：先记下新值，避免重复触发
    open(NAPCAT_STATE_FILE, "w", encoding="utf-8").write(started)
    if not _napcat_online():
        log("NapCat 容器重启，但当前不在线，跳过重连（等下一轮 QQ 恢复流程）")
        return
    if dry_run:
        log("NapCat 容器重启 → [dry-run] 跳过重启 LangBot")
        return
    restart("langbot", False, "NapCat 容器重启导致 OneBot 连接僵死，重建连接")



# ---------------------------------------------------------------- Kokoro 语音保活
KOKORO_PORT = 7870
KOKORO_CFG = r"C:\Users\lenovo\Desktop\_说明文档\mobius\config.json"
_kokoro_last_start = {"ts": 0.0}


def check_kokoro(dry_run=False):
    """7870（Kokoro TTS）未监听时按 Core 配置拉起；冷启动约 150 秒，冷却 300 秒。"""
    if port_alive(KOKORO_PORT):
        return
    if time.time() - _kokoro_last_start["ts"] < 300:
        return
    if dry_run:
        log("Kokoro：7870 未监听（[dry-run] 跳过拉起）")
        return
    try:
        cfg = json.loads(open(KOKORO_CFG, encoding="utf-8").read())
        svc = (cfg.get("services") or {}).get("kokoro") or {}
        exe = svc.get("python")
        cwd = svc.get("cwd")
        args = svc.get("args") or []
        if not exe or not cwd or not args:
            log("Kokoro：配置不完整，跳过拉起")
            return
        if not os.path.exists(exe) or not os.path.isdir(cwd):
            log("Kokoro：解释器或工作目录不存在，跳过拉起")
            return
        subprocess.Popen(
            [exe, *args], cwd=cwd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        _kokoro_last_start["ts"] = time.time()
        log("Kokoro：7870 未监听，已按配置拉起（冷启动约 150s）")
    except Exception as exc:
        log(f"Kokoro 拉起失败：{type(exc).__name__}: {exc}")



# ---------------------------------------------------------------- 记忆嵌入服务保活（bge-m3 :8903）
EMBED_SCRIPT = r"C:\Users\lenovo\Desktop\_说明文档\mobius\ops\memory\embed_server.py"
EMBED_PYTHON = r"D:\AI\mobius-memory\Scripts\python.exe"
EMBED_MODEL_PATH = r"D:\AI\models\bge-m3-st\models--BAAI--bge-m3\snapshots\5617a9f61b028005a4858fdac845db406aefb181"


def check_embed_server(dry_run=False):
    """8903（记忆嵌入）未监听时拉起，供 LongTermMemory / Graphiti 使用。"""
    if port_alive(8903):
        return
    if dry_run:
        log("记忆嵌入：8903 未监听（[dry-run] 跳过拉起）")
        return
    if not os.path.exists(EMBED_SCRIPT) or not os.path.exists(EMBED_PYTHON):
        return
    env = dict(os.environ)
    env["MOBIUS_EMBED_MODEL"] = EMBED_MODEL_PATH
    env["MOBIUS_EMBED_PATH"] = r"D:\AI\models\bge-m3-st"
    env["HF_HUB_OFFLINE"] = "1"
    try:
        subprocess.Popen(
            [EMBED_PYTHON, EMBED_SCRIPT, "--port", "8903"],
            cwd=os.path.dirname(os.path.dirname(EMBED_SCRIPT)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env=env,
        )
        log("记忆嵌入：8903 未监听，已拉起（冷启动约 60-150s）")
    except Exception as exc:
        log(f"记忆嵌入拉起失败：{type(exc).__name__}: {exc}")



# ---------------------------------------------------------------- 八重樱 TTS 服务保活（7873 原生 + 7871 适配层）
GSV_START_PS1 = r"C:\Users\lenovo\Desktop\_说明文档\mobius\ops\tts\start-gsv.ps1"
GSV_PWSH = r"D:\Tools\PowerShell7\pwsh.exe"


def check_gsv_tts(dry_run=False):
    """7871（OpenAI 兼容适配层）未监听时，整套拉起 GPT-SoVITS 语音服务。"""
    if port_alive(7871):
        return
    if dry_run:
        log("八重樱 TTS：7871 未监听（[dry-run] 跳过拉起）")
        return
    if not os.path.exists(GSV_START_PS1):
        return
    try:
        subprocess.Popen(
            [GSV_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", GSV_START_PS1],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log("八重樱 TTS：7871 未监听，已拉起（冷启动约 60-120s）")
    except Exception as exc:
        log(f"八重樱 TTS 拉起失败：{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- ComfyUI 预热（消除冷启动代价）
WARMUP_SCRIPT = r"C:\Users\lenovo\Desktop\_说明文档\mobius\ops\warmup_media.py"
WARMUP_PYTHON = r"D:\AI\qqbot-mcp\Scripts\python.exe"
WARMUP_MARKER = r"C:\Users\lenovo\Desktop\_说明文档\mobius\logs\.comfyui_warm_pid"


def _comfyui_pid():
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=15)
        for line in (r.stdout or "").splitlines():
            if ":8188" in line and "LISTENING" in line.upper():
                return line.split()[-1]
    except Exception:
        return None
    return None


def check_comfy_warmup(dry_run=False):
    """ComfyUI 就绪且是新进程时，后台跑一次千问预热（消除模型初始化/编译的首次代价）。"""
    pid = _comfyui_pid()
    if not pid:
        return
    try:
        warm_pid = open(WARMUP_MARKER, encoding="utf-8").read().strip()
    except OSError:
        warm_pid = ""
    if warm_pid == pid:
        return
    if dry_run:
        log(f"ComfyUI 预热：[dry-run] 新进程 pid={pid}，跳过")
        return
    if not os.path.exists(WARMUP_SCRIPT):
        return
    try:
        subprocess.Popen(
            [WARMUP_PYTHON, WARMUP_SCRIPT, "--model", "qwen_image_2_1"],
            cwd=os.path.dirname(os.path.dirname(WARMUP_SCRIPT)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        open(WARMUP_MARKER, "w", encoding="utf-8").write(pid)
        log(f"ComfyUI 预热：检测到新进程 pid={pid}，已在后台提交预热任务")
    except Exception as exc:
        log(f"ComfyUI 预热失败：{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- 只读运维 MCP（DSH 用）
OPS_MCP_SCRIPT = r"C:\Users\lenovo\Desktop\_说明文档\mobius\ops\mcp_ops_server.py"
OPS_MCP_PYTHON = r"D:\AI\qqbot-mcp\Scripts\python.exe"


def check_ops_mcp(dry_run=False):
    """8901（只读运维 MCP）未监听时拉起，供 DSH 诊断使用。"""
    if port_alive(8901):
        return
    if dry_run:
        log("ops MCP：8901 未监听（[dry-run] 跳过拉起）")
        return
    if not os.path.exists(OPS_MCP_SCRIPT):
        log(f"ops MCP：脚本缺失 {OPS_MCP_SCRIPT}")
        return
    try:
        subprocess.Popen(
            [OPS_MCP_PYTHON, OPS_MCP_SCRIPT, "--port", "8901"],
            cwd=os.path.dirname(os.path.dirname(OPS_MCP_SCRIPT)),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        log("ops MCP：8901 未监听，已拉起")
    except Exception as exc:
        log(f"ops MCP 拉起失败：{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- Pipeline 提示词守卫
GUARD_SCRIPT = r"C:\\Users\\lenovo\\Desktop\\_说明文档\\mobius\\ops\\pipeline_guard.py"
_guard_state = {"last": 0.0}


def check_pipeline_guard(dry_run=False):
    """每 30 分钟校验 pipeline 提示词；漂移时按快照自动恢复并重载 LangBot。"""
    now = time.time()
    if now - _guard_state["last"] < 1800:
        return
    _guard_state["last"] = now
    if dry_run or not os.path.exists(GUARD_SCRIPT):
        return
    try:
        env = dict(os.environ)
        # 从 .env 读取 PG 密码
        env_file = "D:/AI/mobius/.env"
        if os.path.exists(env_file):
            for line in open(env_file, encoding="utf-8"):
                if line.startswith("PG_PASSWORD="):
                    env["PG_PASSWORD"] = line.split("=", 1)[1].strip()
        r = subprocess.run(
            [sys.executable, GUARD_SCRIPT, "--check"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        out = (r.stdout or "").strip()
        if "漂移" in out:
            log("提示词守卫：检测到漂移，尝试自动恢复 …")
            rr = subprocess.run([sys.executable, GUARD_SCRIPT, "--restore"],
                                capture_output=True, text=True, timeout=60, env=env)
            r2 = subprocess.run([sys.executable, GUARD_SCRIPT, "--check"],
                                capture_output=True, text=True, timeout=60, env=env)
            ok = "一致" in (r2.stdout or "")
            log("提示词守卫：恢复%s（%s）" % (
                "成功" if ok else "失败", (rr.stdout or "").strip().replace("\n", " ")[:120]))
            if ok:
                restart("langbot", False, "提示词漂移已恢复，重载流水线")
        elif r.returncode != 0:
            log("提示词守卫执行失败：" + (out or (r.stderr or "").strip())[:160])
    except Exception as exc:
        log(f"提示词守卫执行失败：{type(exc).__name__}: {exc}")




# ---------------------------------------------------------------- 空闲重启门控（P2）
# LangBot 的 MCP 类重启会打断进行中的对话：有活动时延迟，超时或 run 卡死才强制。
DEFER_IDLE_SEC = float(os.environ.get("MOBIUS_DEFER_IDLE_SEC", "60"))
DEFER_RUN_STUCK_SEC = float(os.environ.get("MOBIUS_DEFER_RUN_STUCK_SEC", "600"))
DEFER_MAX_SEC = float(os.environ.get("MOBIUS_DEFER_MAX_SEC", "900"))
_defer_state = {"since": 0.0, "count": 0}


def _pg_password() -> str:
    env_file = "D:/AI/mobius/.env"
    try:
        if os.path.exists(env_file):
            for line in open(env_file, encoding="utf-8"):
                if line.startswith("PG_PASSWORD="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get("PG_PASSWORD", "")


def langbot_activity() -> dict:
    """读 PG 判断是否有进行中的 run 或刚发生的消息。"""
    try:
        import psycopg
        dsn = f"postgresql://postgres:{_pg_password()}@127.0.0.1:55432/langbot"
        with psycopg.connect(dsn, connect_timeout=3) as con:
            row = con.execute(
                "SELECT COUNT(*) AS n, EXTRACT(EPOCH FROM (now() - MIN(created_at))) AS oldest"
                " FROM agent_run WHERE status = 'running'"
            ).fetchone()
            msg = con.execute(
                "SELECT EXTRACT(EPOCH FROM (now() - MAX(timestamp))) AS last_msg FROM monitoring_messages"
            ).fetchone()
        return {
            "known": True,
            "running": int(row[0] or 0),
            "oldest_run_age": float(row[1] or 0),
            "last_msg_age": float(msg[0] or 9999),
        }
    except Exception as exc:  # noqa: BLE001
        return {"known": False, "error": f"{type(exc).__name__}: {exc}"}


def defer_langbot_restart(why: str, dry_run: bool = False) -> bool:
    """返回 True 表示本次推迟重启（不执行 restart）。"""
    now_ts = time.time()
    act = langbot_activity()
    if not act.get("known"):
        log(f"重启门控：无法读取活动状态（{act.get('error')}），按空闲处理")
        return False
    busy = act["running"] > 0 or act["last_msg_age"] < DEFER_IDLE_SEC
    if not busy:
        _defer_state.update({"since": 0.0, "count": 0})
        return False
    if _defer_state["since"] == 0.0:
        _defer_state["since"] = now_ts
    _defer_state["count"] += 1
    elapsed = now_ts - _defer_state["since"]
    stuck = act["oldest_run_age"]
    if stuck > DEFER_RUN_STUCK_SEC:
        log(f"重启门控：最老 run 已 {stuck:.0f}s（>{DEFER_RUN_STUCK_SEC:.0f}s），判定卡死，强制重启")
        _defer_state.update({"since": 0.0, "count": 0})
        return False
    if elapsed > DEFER_MAX_SEC:
        log(f"重启门控：已推迟 {elapsed:.0f}s（>{DEFER_MAX_SEC:.0f}s），强制重启")
        _defer_state.update({"since": 0.0, "count": 0})
        return False
    log(
        f"重启门控：延迟重启 LangBot（{why}）——运行中 run={act['running']}，"
        f"最后消息 {act['last_msg_age']:.0f}s 前，已推迟 {elapsed:.0f}s"
    )
    return True


# ---------------------------------------------------------------- 版本自检（P1：纳入版本控制）
WATCHDOG_CANONICAL = r"C:\\Users\\lenovo\\Desktop\\_说明文档\\mobius\\ops\\watchdog\\watchdog.py"


def _file_sha256(path: str) -> str | None:
    import hashlib
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_self_version():
    """启动时自检：运行中的 watchdog.py 与仓库副本是否一致。"""
    runtime = os.path.abspath(__file__)
    a, b = _file_sha256(runtime), _file_sha256(WATCHDOG_CANONICAL)
    if b is None:
        log(f"看门狗自检：未找到仓库副本（{WATCHDOG_CANONICAL}）")
        return
    if a != b:
        log("⚠️ 看门狗自检：运行版本与仓库副本不一致（请运行 ops/sync_watchdog.py --capture 更新仓库）")
    else:
        log(f"看门狗自检：版本一致（sha256={a[:12]}…）")

# ---------------------------------------------------------------- PG / Redis 健康 + 备份
PG_BACKUP_SCRIPT = r"C:\Users\lenovo\Desktop\_说明文档\mobius\ops\pg_backup.py"
_pg_state = {"last_backup": 0.0}


def check_pg_redis(dry_run=False):
    """检查 mobius-pg / mobius-redis 容器；未运行则尝试拉起。"""
    for name in ("mobius-pg", "mobius-redis"):
        try:
            r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", name],
                               capture_output=True, text=True, timeout=20)
            running = (r.stdout or "").strip() == "true"
            if running:
                continue
            if dry_run:
                log(f"{name} 未运行：[dry-run] 跳过拉起")
                continue
            subprocess.run(["docker", "start", name], capture_output=True, text=True, timeout=60)
            log(f"{name} 未运行，已尝试拉起")
        except Exception as exc:
            log(f"{name} 健康检查失败：{type(exc).__name__}: {exc}")


def check_pg_backup(dry_run=False):
    """每日一次 PG 备份；超过 26 小时未备份则补做。"""
    now = time.time()
    if now - _pg_state["last_backup"] < 26 * 3600 and _pg_state["last_backup"] > 0:
        return
    if not os.path.exists(PG_BACKUP_SCRIPT):
        return
    if dry_run:
        log("PG 备份：[dry-run] 跳过")
        return
    try:
        env = dict(os.environ)
        env_file = "D:/AI/mobius/.env"
        if os.path.exists(env_file):
            for line in open(env_file, encoding="utf-8"):
                if line.startswith("PG_PASSWORD="):
                    env["PG_PASSWORD"] = line.split("=", 1)[1].strip()
        r = subprocess.run([sys.executable, PG_BACKUP_SCRIPT, "--backup"],
                           capture_output=True, text=True, timeout=300, env=env)
        tail = [l for l in (r.stdout or "").splitlines() if l.strip()][-3:]
        log("PG 备份：" + ("; ".join(tail) if tail else f"exit={r.returncode}"))
        if r.returncode == 0:
            _pg_state["last_backup"] = now
    except Exception as exc:
        log(f"PG 备份失败：{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- SQLite 每日备份
# 2026-10-30 事故：langbot.db 因宿主机/容器双写而损坏，4 个页面读不出，
# 当时能救回是运气（所有表都还能扫描）。此后规定：
#   - 宿主机绝不直写 langbot.db（备份走 docker exec + VACUUM INTO）
#   - 备份必须做「独立目录恢复 + 行数比对 + integrity_check」，不能只证明命令跑过
# 实现放在仓库 ops/sqlite_backup.py（版本化），脚本自身含当日幂等守卫。
SQLITE_BACKUP_SCRIPT = r"C:\Users\lenovo\Desktop\_说明文档\mobius\ops\sqlite_backup.py"


def check_sqlite_backup(dry_run=False):
    if dry_run:
        log("SQLite 备份：[dry-run] 跳过")
        return
    if not os.path.exists(SQLITE_BACKUP_SCRIPT):
        log(f"SQLite 备份：脚本不存在 {SQLITE_BACKUP_SCRIPT}")
        return
    try:
        r = subprocess.run(
            [sys.executable, SQLITE_BACKUP_SCRIPT],
            capture_output=True, text=True, timeout=900,
        )
    except Exception as exc:
        log(f"SQLite 备份：执行异常 {type(exc).__name__}: {exc}")
        return
    out = (r.stdout or "").strip().splitlines()
    tail = out[-3:] if out else []
    if r.returncode == 0:
        # 只在天数变化时刷屏没意义，这里只输出验证结果行
        for line in tail:
            if "恢复验证" in line or "已写入" in line or "跳过" in line:
                log(f"SQLite 备份：{line.split(']', 1)[-1].strip()}")
    else:
        log(f"SQLite 备份：失败 code={r.returncode} {tail[-1] if tail else ''}")

# 绝对不要从宿主机直接写 langbot.db：
# 该文件由容器进程持有，宿主机与容器是两个 OS 文件锁视图，并发写会令 SQLite
# 报 "disk I/O error"，实测已把一个库写坏（2026-10-30 21:01，4 个页面读不出）。
# 因此清理动作一律通过 docker exec 在容器内执行，宿主机只读 stdout。
_QQ_POISON_SCRIPT = '''
import sqlite3
c = sqlite3.connect("/app/data/langbot.db", timeout=15)
p = '%"image_url": {%'
n = c.execute("select count(*) from transcript where content_json like ?", (p,)).fetchone()[0]
if n and DELETE:
    c.execute("delete from transcript where content_json like ?", (p,))
    c.commit()
print("POISON=%d" % n)
c.close()
'''


def clean_poisoned_history(dry_run=False):
    script = _QQ_POISON_SCRIPT.replace("DELETE", "False" if dry_run else "True")
    try:
        out = subprocess.run(
            ["docker", "exec", "langbot", "/app/.venv/bin/python", "-c", script],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:
        log(f"会话清理：容器内执行失败 {type(exc).__name__}: {exc}")
        return 0
    text = (out.stdout or "") + (out.stderr or "")
    if out.returncode != 0:
        log(f"会话清理：容器内脚本异常 code={out.returncode} {text[:160]}")
        return 0
    count = 0
    for line in text.splitlines():
        if line.startswith("POISON="):
            try:
                count = int(line.split("=", 1)[1])
            except ValueError:
                pass
    if count:
        if dry_run:
            log(f"会话清理：发现 {count} 条含图片的历史（dry-run，未删除）")
        else:
            log(f"会话清理：已删除 {count} 条含图片的历史，避免图片链接失效导致该会话持续 400")
    return count


def now_str():
    return time.strftime("%H:%M:%S")


def log(msg):
    print(f"[{now_str()}] {msg}", flush=True)


def _napcat_token() -> str:
    """从 D:/AI/mobius/.env 读取 NapCat OneBot HTTP token。"""
    env_file = "D:/AI/mobius/.env"
    try:
        if os.path.exists(env_file):
            for line in open(env_file, encoding="utf-8"):
                if line.startswith("NAPCAT_HTTP_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def http_get(url, timeout=8, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_ok(path, timeout=6):
    try:
        with urllib.request.urlopen(f"{LANGBOT_API}{path}", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def bot_logs(count=40):
    body = json.dumps({"from_index": -1, "max_count": count}).encode()
    req = urllib.request.Request(
        f"{LANGBOT_API}/api/v1/platform/bots/{BOT_ID}/logs",
        data=body,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode()).get("data", {}).get("logs", [])


def last_delivered_event():
    """最近一条「用户消息已投递到流水线」的时间（epoch）。

    修复（2026-11-02）：LangBot 的 bot 日志接口在重启后可能返回空，
    导致此前的判定完全失明。改为优先读 PG monitoring_messages 的 user 行，
    bot 日志仅作回退。
    """
    try:
        import psycopg
        dsn = f"postgresql://postgres:{_pg_password()}@127.0.0.1:55432/langbot"
        with psycopg.connect(dsn, connect_timeout=3) as con:
            row = con.execute(
                "select max(timestamp) from monitoring_messages where role='user'"
            ).fetchone()
        if row and row[0]:
            dt = row[0]
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.timestamp()
        return 0.0
    except Exception as exc:  # noqa: BLE001
        log(f"读取 PG 投递时间失败，回退 bot 日志：{type(exc).__name__}")
    newest = 0.0
    for entry in bot_logs():
        if "delivered to Pipeline" not in (entry.get("text") or ""):
            continue
        ts = float(entry.get("timestamp") or 0)
        if ts > newest:
            newest = ts
    return newest


def last_llm_epoch():
    """最近一次 LLM 调用时间（epoch）。

    优先读 LangBot 的 PostgreSQL（2026-11 已完成 SQLite → PG 迁移）；
    PG 不可用才回退旧 SQLite。两者存的都是 UTC 的 naive 时间戳。

    修复（2026-11-02）：此前只读迁移前的 SQLite，读到的是 11-01 的旧数据，
    导致「投递-调用滞后」虚高约 20 小时并误判重启 LangBot。
    """
    try:
        import psycopg
        dsn = f"postgresql://postgres:{_pg_password()}@127.0.0.1:55432/langbot"
        with psycopg.connect(dsn, connect_timeout=3) as con:
            row = con.execute("select max(timestamp) from monitoring_llm_calls").fetchone()
        if row and row[0]:
            dt = row[0]
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.timestamp()
    except Exception as exc:  # noqa: BLE001
        log(f"读取 PG LLM 调用时间失败，回退 SQLite：{type(exc).__name__}")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        row = con.execute(
            "select timestamp from monitoring_llm_calls order by timestamp desc limit 1"
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return 0.0
    dt = datetime.datetime.strptime(str(row[0])[:19], "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=datetime.timezone.utc).timestamp()


def cool(container):
    return time.time() - _state["last_restart"][container] < COOLDOWN_SECONDS


def restart(container, dry_run, why):
    # P2：LangBot 的 MCP 类重启先看忙闲，有活动就推迟（硬故障不受影响）
    if container == "langbot" and "MCP" in why and not dry_run:
        if defer_langbot_restart(why):
            return False
    if cool(container):
        log(f"{container} 处于冷却期，跳过重启（{why}）")
        return False
    log(f"重启 {container}：{why}")
    check_incident_escalation(why, dry_run)
    if not dry_run:
        subprocess.run(["docker", "restart", container], capture_output=True, timeout=180)
        _state["last_restart"][container] = time.time()
    return True


def napcat_waiting_qr():
    """NapCat 是否处于「未登录待扫码」：容器内存在 qrcode.png 即认为在等扫码。
    此时 OneBot API 连不上是正常的（登录成功前 API 不启动），不能当假死重启。"""
    try:
        r = subprocess.run(
            ["docker", "exec", "napcat", "sh", "-c",
             "test -f /app/napcat/cache/qrcode.png && echo yes"],
            capture_output=True, text=True, timeout=15,
        )
        return "yes" in r.stdout
    except Exception:
        return False


def check_napcat(dry_run):
    """NapCat 假死检测。返回 True 表示它还活着。"""
    try:
        data = http_get(f"{NAPCAT_API}/get_status", timeout=8, headers={"Authorization": f"Bearer {_napcat_token()}"})
        online = bool(data.get("data", {}).get("online"))
        if online:
            _state["napcat_fail"] = 0
            return True
        log("NapCat 返回 online=false")
    except Exception as exc:
        _state["napcat_fail"] += 1
        log(f"NapCat get_status 失败（第 {_state['napcat_fail']} 次）：{type(exc).__name__}")
        if napcat_waiting_qr():
            _state["napcat_fail"] = 0
            log("NapCat 处于未登录待扫码状态，跳过重启（请到网页面板完成登录）")
            return False
        if _state["napcat_fail"] >= FAIL_THRESHOLD:
            restart("napcat", dry_run, "get_status 连续失败，疑似假死")
            _state["napcat_fail"] = 0
        return False
    return True


def check_langbot(dry_run):
    """LangBot 健康 + 流水线卡死检测。"""
    if not http_ok("/healthz"):
        restart("langbot", dry_run, "健康检查失败")
        return
    try:
        delivered = last_delivered_event()
        last_llm = last_llm_epoch()
    except Exception as exc:
        log(f"读取 LangBot 状态失败：{type(exc).__name__}: {exc}")
        return

    age = time.time() - delivered if delivered else 0
    # 只在「刚刚确实有消息进来、但它没被处理」时才重启。
    # 上界必须有：NapCat 的 3000 端口没发布到宿主机时读不到新事件，
    # 旧的 delivered 记录会一直变老，没有上界就会无限误重启。
    if (delivered and STUCK_SECONDS < age < STUCK_MAX_SECONDS
            and delivered > last_llm + 1):
        restart(
            "langbot",
            dry_run,
            f"消息投递 {int(age)}s 未被处理（晚于最近 LLM 调用 {int(delivered - last_llm)}s）",
        )
    else:
        lag = int(delivered - last_llm) if delivered and last_llm else 0
        log(f"LangBot 正常（最后投递 {int(age)}s 前，投递-调用滞后 {lag}s）")


def mcp_alive(timeout=3):
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", MCP_PORT)) == 0
    finally:
        s.close()


def port_alive(port, timeout=3):
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()


def check_mcp(dry_run):
    """ComfyUI MCP Server 存活检测，掉线则后台拉起。"""
    if mcp_alive():
        log(f"MCP Server 正常（端口 {MCP_PORT}）")
        return True
    if time.time() - _state["last_start"]["mcp"] < COOLDOWN_SECONDS:
        log(f"MCP Server 端口 {MCP_PORT} 不可达，处于冷却期，跳过拉起")
        return False
    log(f"MCP Server 端口 {MCP_PORT} 不可达，拉起进程")
    if not dry_run:
        logf = open(MCP_LOG, "a", encoding="utf-8")
        subprocess.Popen(
            [MCP_PYTHON, MCP_SCRIPT, "--port", str(MCP_PORT)],
            stdout=logf,
            stderr=subprocess.STDOUT,
            creationflags=0x00000008,
        )
        logf.close()
        _state["last_start"]["mcp"] = time.time()
    return False


def check_tts(dry_run):
    """Kokoro TTS API 存活检测，掉线则后台拉起（语音工具依赖 7870 端口）。"""
    if port_alive(TTS_PORT):
        log(f"TTS Server 正常（端口 {TTS_PORT}）")
        return True
    if time.time() - _state["last_start"]["tts"] < COOLDOWN_SECONDS:
        log(f"TTS Server 端口 {TTS_PORT} 不可达，处于冷却期，跳过拉起")
        return False
    log(f"TTS Server 端口 {TTS_PORT} 不可达，拉起进程")
    if not dry_run:
        logf = open(TTS_LOG, "a", encoding="utf-8")
        subprocess.Popen(
            [TTS_PYTHON, TTS_SCRIPT] + TTS_ARGS,
            stdout=logf,
            stderr=subprocess.STDOUT,
            creationflags=0x00000008,
        )
        logf.close()
        _state["last_start"]["tts"] = time.time()
    return False


def _acquire_single_instance(port=38999):
    """用本地端口占位做单实例锁；重复启动直接退出，避免多个看门狗互相打架。"""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def check_core(dry_run):
    """Möbius Core 存活检测。它是宿主媒体服务的唯一监管者，掉了要立刻拉起。"""
    if port_alive(CORE_PORT):
        log(f"Möbius Core 正常（端口 {CORE_PORT}）")
        return True
    if time.time() - _state["last_start"]["core"] < COOLDOWN_SECONDS:
        log(f"Möbius Core 端口 {CORE_PORT} 不可达，处于冷却期，跳过拉起")
        return False
    log(f"Möbius Core 端口 {CORE_PORT} 不可达，拉起进程")
    if not dry_run:
        os.makedirs(os.path.dirname(CORE_LOG), exist_ok=True)
        logf = open(CORE_LOG, "a", encoding="utf-8")
        subprocess.Popen(
            [CORE_PYTHON, "-m", "core.server"],
            cwd=CORE_CWD,
            stdout=logf,
            stderr=subprocess.STDOUT,
            creationflags=0x00000008,
        )
        logf.close()
        _state["last_start"]["core"] = time.time()
    return False


def _parse_docker_time(value):
    """解析 docker inspect 的 StartedAt（形如 2026-10-28T15:04:59.751856728Z）。"""
    value = (value or "").strip()
    if not value or value.startswith("0001-"):
        return 0.0
    try:
        base = value.split(".")[0].replace("Z", "")
        if len(base) == 19:
            dt = datetime.datetime.strptime(base, "%Y-%m-%dT%H:%M:%S")
            return dt.replace(tzinfo=datetime.timezone.utc).timestamp()
    except Exception:
        pass
    return 0.0


def langbot_start_epoch():
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", "langbot"],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode != 0:
        return 0.0
    return _parse_docker_time(r.stdout)


def gateway_start_epoch():
    data = http_get(GATEWAY_STATUS, timeout=6)
    # /api/pulse 是平铺的 uptime_sec；老 /api/status 是 gateway.uptime_sec。两种都认。
    up = data.get("uptime_sec")
    if up is None:
        up = (data.get("gateway") or {}).get("uptime_sec")
    up = float(up or 0)
    return time.time() - up if up > 0 else 0.0


def langbot_mcp_init_failed():
    """LangBot 本次启动以来是否报过 MCP 初始化失败。

    这是比「启动时间先后」更直接的证据：无论什么原因（网关没起、网络抖动、配置错），
    只要日志里出现 Failed to start MCP server，这个 MCP 工具集就是废的，必须重启重建。
    """
    r = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.StartedAt}}", "langbot"],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode != 0:
        return False
    started = (r.stdout or "").strip()
    if not started:
        return False
    try:
        logs = subprocess.run(
            ["docker", "logs", "langbot", "--since", started],
            capture_output=True, text=True, timeout=25,
        )
    except Exception:
        return False
    out = (logs.stdout or "") + (logs.stderr or "")
    return "Failed to start MCP server" in out


def check_mcp_session(dry_run):
    """网关重启后 LangBot 的 MCP 会话会变死流且不重连，这里做定点自愈。"""
    if not port_alive(MCP_PORT):
        return
    try:
        gw = gateway_start_epoch()
        lb = langbot_start_epoch()
    except Exception as exc:
        log(f"读取网关/LangBot 启动时间失败：{type(exc).__name__}: {exc}")
        return
    if not gw or not lb:
        # 不要静默返回：读不到时间就等于自愈失效，必须能在日志里看出来。
        log(f"MCP 会话检查跳过（网关启动时间={gw:.0f}, LangBot 启动时间={lb:.0f}）")
        return
    if gw > lb + SESSION_SKEW_SECONDS:
        restart(
            "langbot", dry_run,
            f"MCP 会话已失效（网关 {time.strftime('%H:%M:%S', time.localtime(gw))} 重启，"
            f"LangBot {time.strftime('%H:%M:%S', time.localtime(lb))} 启动，未重连）",
        )
        return
    try:
        if langbot_mcp_init_failed():
            restart("langbot", dry_run, "LangBot 日志里有 MCP 初始化失败，MCP 工具集不可用")
            return
    except Exception as exc:
        log(f"检查 LangBot MCP 日志失败：{type(exc).__name__}: {exc}")
    log("MCP 会话正常（顺序正确，且无初始化失败记录）")


# ---------------------------------------------------------------- QQ 登录态降级
# 状态文件：避免 watchdog 重启后从零计数导致无限重试

def _qq_state_load():
    try:
        with open(QQ_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _qq_state_save(state):
    try:
        os.makedirs(os.path.dirname(QQ_STATE_FILE), exist_ok=True)
        tmp = QQ_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, QQ_STATE_FILE)
    except Exception as exc:
        log(f"QQ 登录：写状态文件失败 {type(exc).__name__}: {exc}")


def _post_json(url, payload, token=None, timeout=QQ_HTTP_TIMEOUT):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _qq_webui_token():
    """从宿主机挂载的 webui.json 读 WebUI token（不落盘、不硬编码）。"""
    with open(NAPCAT_WEBUI_CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    token = str(cfg.get("token") or "").strip()
    if not token:
        raise RuntimeError("webui.json 里 token 为空")
    return token


def _qq_credential(force=False):
    """用 webui token 换登录凭据。hash = sha256(token + '.napcat')，凭据约 1 小时有效。"""
    if not force and _qq_cred["value"] and time.time() - _qq_cred["at"] < 3000:
        return _qq_cred["value"]
    token = _qq_webui_token()
    digest = hashlib.sha256((token + ".napcat").encode("utf-8")).hexdigest()
    resp = _post_json(f"{NAPCAT_WEBUI}/api/auth/login", {"hash": digest})
    cred = ((resp or {}).get("data") or {}).get("Credential")
    if not cred:
        raise RuntimeError(f"WebUI 登录失败: {str(resp)[:200]}")
    _qq_cred.update({"value": cred, "at": time.time()})
    return cred


def _qq_login_status():
    """返回 {isLogin, isOffline, loginPhase, coreReady, ...}；凭据过期自动重登一次。"""
    resp = _post_json(
        f"{NAPCAT_WEBUI}/api/QQLogin/CheckLoginStatus", {}, token=_qq_credential()
    )
    if (resp or {}).get("code") != 0:
        resp = _post_json(
            f"{NAPCAT_WEBUI}/api/QQLogin/CheckLoginStatus",
            {},
            token=_qq_credential(force=True),
        )
    return (resp or {}).get("data") or None


def _qq_quick_login(uin=NAPCAT_ACCOUNT):
    resp = _post_json(
        f"{NAPCAT_WEBUI}/api/QQLogin/SetQuickLogin",
        {"uin": str(uin)},
        token=_qq_credential(),
    )
    return resp or {}


def _qq_wait_login(timeout):
    """等待登录就绪；返回状态 dict 或 None。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(3)
        try:
            st = _qq_login_status()
        except Exception:
            continue
        if st and st.get("isLogin") and not st.get("isOffline"):
            return st
    return None


def _docker_running(name):
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True, text=True, timeout=20,
        )
        return r.returncode == 0 and "true" in (r.stdout or "").lower()
    except Exception:
        return False


def check_qq_login(dry_run=False):
    """QQ 登录态检测 + 三级降级恢复。每轮调用一次。"""
    state = _qq_state_load()
    now = time.time()

    # --- L3 静默期：只报告，不动作 ---
    l3_until = float(state.get("l3_until") or 0)
    if now < l3_until:
        log(
            "QQ 登录：L3 静默期，不做任何自动动作（至 %s）"
            % time.strftime("%H:%M:%S", time.localtime(l3_until))
        )
        return

    # --- 容器没起 / WebUI 端口不通 ---
    if not port_alive(NAPCAT_WEBUI_PORT):
        if not _docker_running("napcat"):
            log("QQ 登录：napcat 容器未运行 → 尝试 docker start")
            if not dry_run:
                subprocess.run(["docker", "start", "napcat"], capture_output=True, timeout=90)
            return
        log("QQ 登录：WebUI 端口不通，但容器在运行（可能在重启中），本轮跳过")
        return

    # --- 查询状态 ---
    try:
        st = _qq_login_status()
    except Exception as exc:
        log(f"QQ 登录：查询状态失败 {type(exc).__name__}: {exc}")
        return
    if not st:
        log("QQ 登录：状态响应异常，本轮跳过")
        return

    # --- 正常 ---
    if st.get("isLogin") and not st.get("isOffline"):
        if state.get("offline_since") or state.get("level") or state.get("l1_fails") or state.get("l2_fails"):
            log("QQ 登录：已恢复正常，降级计数清零")
            state.update({"offline_since": 0, "level": 0, "l1_fails": 0, "l2_fails": 0,
                          "last_l1": 0, "last_l2": 0})
            _qq_state_save(state)
        else:
            log("QQ 登录：正常")
        return

    # --- NapCat 自己在登录流程中，别抢 ---
    phase = st.get("loginPhase") or ""
    if phase and phase != "ready" and not st.get("isOffline"):
        log(f"QQ 登录：NapCat 正在处理登录（phase={phase}），等待")
        return

    # --- 确认离线，进入观察期 ---
    if not state.get("offline_since"):
        state["offline_since"] = now
        _qq_state_save(state)
        log(
            "QQ 登录：检测到离线（isLogin=%s isOffline=%s phase=%s），进入 %ss 观察期"
            % (st.get("isLogin"), st.get("isOffline"), phase or "-", QQ_GRACE_SECONDS)
        )
        return
    elapsed = now - float(state["offline_since"])
    if elapsed < QQ_GRACE_SECONDS:
        log(f"QQ 登录：离线 {int(elapsed)}s，观察期内（<{QQ_GRACE_SECONDS}s）")
        return

    level = int(state.get("level") or 0)
    core_ready = bool(st.get("coreReady", True))

    # --- L1：快速登录 ---
    if level == 0 and core_ready:
        last = float(state.get("last_l1") or 0)
        if now - last < QQ_L1_COOLDOWN:
            log(f"QQ 登录：L1 冷却中，还需 {int(QQ_L1_COOLDOWN - (now - last))}s")
            return
        log(f"QQ 登录：离线 {int(elapsed)}s，L1 尝试快速登录（uin={NAPCAT_ACCOUNT}）")
        if dry_run:
            log("QQ 登录：[dry-run] 跳过实际调用")
            return
        state["last_l1"] = now
        try:
            resp = _qq_quick_login()
            log(f"QQ 登录：快速登录响应 {str(resp)[:160]}")
            ok = _qq_wait_login(15) is not None
        except Exception as exc:
            log(f"QQ 登录：L1 异常 {type(exc).__name__}: {exc}")
            ok = False
        if ok:
            log("QQ 登录：L1 成功，已重新登录")
            state.update({"offline_since": 0, "level": 0, "l1_fails": 0, "l2_fails": 0})
        else:
            state["l1_fails"] = int(state.get("l1_fails") or 0) + 1
            log(f"QQ 登录：L1 失败（连续 {state['l1_fails']}/{QQ_L1_MAX_FAILS}）")
            if state["l1_fails"] >= QQ_L1_MAX_FAILS:
                state["level"] = 1
                state["l1_fails"] = 0
                log("QQ 登录：L1 连续失败，升级到 L2（重启容器）")
        _qq_state_save(state)
        return

    # --- L2：重启容器 ---
    if level == 0 and not core_ready:
        log("QQ 登录：coreReady=false，快速登录救不了，直接升级 L2")
        state["level"] = 1
        _qq_state_save(state)
        level = 1
    if level == 1:
        last = float(state.get("last_l2") or 0)
        if now - last < QQ_L2_COOLDOWN:
            log(f"QQ 登录：L2 冷却中，还需 {int(QQ_L2_COOLDOWN - (now - last))}s")
            return
        log("QQ 登录：L2 重启 napcat 容器")
        if dry_run:
            log("QQ 登录：[dry-run] 跳过 docker restart")
            return
        state["last_l2"] = now
        try:
            subprocess.run(["docker", "restart", "napcat"], capture_output=True, timeout=180)
        except Exception as exc:
            log(f"QQ 登录：容器重启异常 {type(exc).__name__}: {exc}")
        ok = _qq_wait_login(150) is not None
        if ok:
            log("QQ 登录：L2 成功，已重新登录")
            state.update({"offline_since": 0, "level": 0, "l1_fails": 0, "l2_fails": 0})
        else:
            state["l2_fails"] = int(state.get("l2_fails") or 0) + 1
            log(f"QQ 登录：L2 失败（连续 {state['l2_fails']}/{QQ_L2_MAX_FAILS}）")
            if state["l2_fails"] >= QQ_L2_MAX_FAILS:
                state["level"] = 2
                state["l3_until"] = now + QQ_L3_HOLD
                log(
                    "QQ 登录：L2 也失败，进入 L3 —— 需要人工扫码。"
                    "30 分钟内不再自动动作（WebUI: http://127.0.0.1:%d，token 见 webui.json）"
                    % NAPCAT_WEBUI_PORT
                )
        _qq_state_save(state)
        return

    # --- L3：停手 ---
    if not state.get("l3_until"):
        state["l3_until"] = now + QQ_L3_HOLD
        _qq_state_save(state)
    log("QQ 登录：处于 L3，等待人工处理（扫码后会自动清零）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()

    lock = None
    if not args.once:
        lock = _acquire_single_instance()
        if lock is None:
            log("已有看门狗实例在运行，本实例退出")
            return 0

    log(f"看门狗启动（interval={args.interval}s, dry_run={args.dry_run}）")
    check_self_version()
    while True:
        core_ok = check_core(args.dry_run)
        if core_ok:
            # Core 负责 8899 / 7870 / 8188 的启停与健康检查，这里只看它活着没有。
            pass
        else:
            # 回退：Core 不在时，仍按旧逻辑保活 MCP 与 Kokoro，避免机器人彻底失去媒体能力。
            check_mcp(args.dry_run)
            check_tts(args.dry_run)
        napcat_ok = check_napcat(args.dry_run)
        # QQ 登录态三级降级（L1 快速登录 → L2 重启容器 → L3 停手）
        # 放在 check_napcat 之前：登录问题优先用最轻的手段处理。
        check_qq_login(args.dry_run)
        # 不再以 napcat_ok 为门槛：NapCat 的宿主机 3000 端口有时没发布，
        # 那只是「宿主机看不到它的 API」，不代表 LangBot 死了。
        # 卡死判定已加上时间上界，不会再因为读不到新事件而误重启。
        check_langbot(args.dry_run)
        # 会话图片中毒清理：QQ 里发过的图会以 CDN 临时链接写进历史，
        # 链接失效后 DeepSeek 每轮下载失败 → 该会话所有消息 400。
        clean_poisoned_history(args.dry_run)
        # 信息探针（只读采集，每 5 分钟）
        check_state_probe(args.dry_run)
        check_pipeline_guard(args.dry_run)
        # 超时补丁自愈（容器重建后补丁会丢失）
        ensure_langbot_timeout_patches(args.dry_run)
        # SQLite 每日备份（脚本自带当日幂等守卫 + 独立目录恢复验证）
        # 4.6：LangBot/Gateway/Core 已迁 PG，旧 SQLite 备份停用
        # check_sqlite_backup(args.dry_run)
        check_pg_redis(args.dry_run)
        check_pg_backup(args.dry_run)
        check_ops_mcp(args.dry_run)
        check_comfy_warmup(args.dry_run)
        check_embed_server(args.dry_run)
        check_gsv_tts(args.dry_run)
        check_kokoro(args.dry_run)
        check_napcat_restart_relink(args.dry_run)
        # MCP 工具调用失败签名 → 重建 LangBot 会话（先于会话顺序检查）
        check_langbot_tool_failure(args.dry_run)
        check_mcp_session(args.dry_run)
        if args.once:
            break
        time.sleep(args.interval)
    if lock is not None:
        lock.close()


if __name__ == "__main__":
    sys.exit(main() or 0)
