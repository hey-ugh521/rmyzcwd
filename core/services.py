"""Möbius Core —— 宿主媒体服务的唯一监管者 + Media Gateway 客户端。

职责：
- 维护媒体模式（off / on_demand / prewarm）
- 按需启动 / 休眠 ComfyUI、Kokoro、Media Gateway
- 对外提供健康状态（Console 的「概览」直接读它）
- 作为 Media Gateway 的 REST 客户端（生图、TTS、任务查询）

设计约束：
- 模型不能自行反复启停大服务；启停只由 mode + 用户请求 + 冷却/退避决定。
- 不接管 Docker 容器（NapCat / LangBot 仍由 watchdog.py 负责）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from . import store
from .store import CFG, BASE, LOG_DIR, now

MEDIA_MODES = ("off", "on_demand", "prewarm")
START_COOLDOWN = 30.0        # 同一服务两次启动最小间隔
MODEL_PATH_WAIT_SEC = 120    # 启动 ComfyUI 前等待额外模型目录（U 盘）就绪的最长时间
log = logging.getLogger("mobius.services")
FAIL_BACKOFF = 300.0         # 连续失败后进入退避
ACTIVITY_TOUCH = 5.0         # 同一请求内的活跃标记合并窗口


class MediaDisabled(RuntimeError):
    pass


class ServiceError(RuntimeError):
    pass


class _Service:
    def __init__(self, name: str, cfg: dict):
        self.name = name
        self.cfg = cfg or {}
        self.state = "unknown"          # down|starting|ready|busy|error|disabled
        self.pid: int | None = None
        self.proc: subprocess.Popen | None = None
        self.last_check = 0.0
        self.last_error: str | None = None
        self.started_at = 0.0

    @property
    def port(self) -> int:
        return int(self.cfg.get("port") or 0)

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", True))

    @property
    def autostart(self) -> bool:
        return bool(self.cfg.get("autostart", False))

    @property
    def health_url(self) -> str:
        return str(self.cfg.get("health_url") or "")

    @property
    def extra_ports(self) -> list[int]:
        return [int(p) for p in (self.cfg.get("ports") or []) if str(p).isdigit()]

    def public(self) -> dict:
        return {
            "name": self.name,
            "state": self.state if self.enabled else "disabled",
            "enabled": self.enabled,
            "autostart": self.autostart,
            "port": self.port,
            "last_check": round(self.last_check, 1),
            "pid": self.pid,
            "error": self.last_error,
            "uptime_sec": round(now() - self.started_at, 1) if self.started_at else 0,
            "note": str(self.cfg.get("note") or ""),
        }


async def _port_open(port: int, timeout: float = 1.5) -> bool:
    if not port:
        return False
    try:
        fut = asyncio.open_connection("127.0.0.1", port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url)
            return r.status_code < 500
    except Exception:
        return False


def _kill_tree(pid: int) -> None:
    """清理整棵进程树（Windows 用 taskkill /T，其他平台忽略）。"""
    if os.name != "nt" or not pid:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        pass


def _kill_matched(pattern: str) -> list[int]:
    """按命令行正则清理游离子进程（有人用 DETACHED_PROCESS 启动时会脱离进程树）。"""
    killed: list[int] = []
    if not pattern:
        return killed
    try:
        import re as _re
        try:
            import psutil  # type: ignore

            for p in psutil.process_iter(["pid", "cmdline"]):
                try:
                    cmd = " ".join(p.info.get("cmdline") or [])
                except Exception:
                    continue
                if cmd and _re.search(pattern, cmd) and p.pid != os.getpid():
                    try:
                        p.kill()
                        killed.append(p.pid)
                    except Exception:
                        pass
        except ImportError:
            # 无 psutil 时退回 wmic
            out = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine", "/format:csv"],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            for line in out.splitlines():
                if _re.search(pattern, line):
                    parts = [x.strip() for x in line.split(",")]
                    for token in parts[::-1]:
                        if token.isdigit():
                            subprocess.run(
                                ["taskkill", "/PID", token, "/T", "/F"],
                                capture_output=True, timeout=10,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            )
                            killed.append(int(token))
                            break
    except Exception:
        pass
    return killed


class Supervisor:
    def __init__(self) -> None:
        svc = CFG.get("services", {})
        self.services: dict[str, _Service] = {k: _Service(k, v) for k, v in svc.items()}
        self._lock = asyncio.Lock()
        self._last_start: dict[str, float] = {}
        self._fail: dict[str, tuple[int, float]] = {}
        self._last_activity = now()   # 以进程启动时间为基线，否则会被算成「闲置 10 亿秒」
        self._mode_cache: str | None = None
        self._comfy_freed = False     # 闲置后是否已卸载过 ComfyUI 模型

    # -------------------------------------------------- 模式

    def get_mode(self) -> str:
        if self._mode_cache is None:
            m = store.kv_get("media_mode", "on_demand")
            self._mode_cache = m if m in MEDIA_MODES else "on_demand"
        return self._mode_cache

    def set_mode(self, mode: str) -> str:
        if mode not in MEDIA_MODES:
            raise ServiceError(f"未知模式 {mode}，可选 {MEDIA_MODES}")
        self._mode_cache = mode
        store.kv_set("media_mode", mode)
        store.audit("console", "media.set_mode", {"mode": mode}, source="console")
        return mode

    def config(self) -> dict:
        return {
            "idle_sleep_sec": int(CFG["core"].get("idle_sleep_sec", 600)),
            "prewarm_on_start": bool(CFG["core"].get("prewarm_on_start", False)),
            "mode": self.get_mode(),
        }

    def update_config(self, idle_sleep_sec: int | None = None,
                      prewarm_on_start: bool | None = None) -> dict:
        if idle_sleep_sec is not None:
            CFG["core"]["idle_sleep_sec"] = max(60, int(idle_sleep_sec))
        if prewarm_on_start is not None:
            CFG["core"]["prewarm_on_start"] = bool(prewarm_on_start)
        try:
            import json
            with open(store.CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(CFG, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        store.audit("console", "media.update_config", self.config(), source="console")
        return self.config()

    # -------------------------------------------------- 探测

    async def probe_all(self) -> dict[str, dict]:
        results = await asyncio.gather(*[self._probe_one(s) for s in self.services.values()])
        return {s.name: s.public() for s in self.services.values()}

    async def _is_up(self, s: _Service) -> bool:
        """探测服务是否就绪（供 start/probe/start_all 共用）。"""
        if not s.enabled:
            return False
        if s.health_url:
            return await _http_ok(s.health_url)
        if s.extra_ports:
            for p in s.extra_ports:
                if await _port_open(p):
                    return True
            return False
        return await _port_open(s.port)

    async def _probe_one(self, s: _Service) -> None:
        s.last_check = now()
        if not s.enabled:
            s.state = "disabled"
            return
        ok = await self._is_up(s)
        if ok:
            if s.state in ("down", "unknown", "error"):
                s.state = "ready"
                if not s.started_at:
                    s.started_at = now()
            s.last_error = None
        else:
            # 启动过程中不要清 pid/state，否则并发 /api/status 会把「正在启动」打回 down。
            if s.state != "starting":
                s.state = "down"
                s.started_at = 0.0
                s.pid = None

    async def probe(self, name: str) -> dict:
        s = self.services.get(name)
        if not s:
            raise ServiceError(f"未知服务 {name}")
        await self._probe_one(s)
        return s.public()

    # -------------------------------------------------- 启停

    async def _wait_model_paths(self, s: _Service) -> None:
        """启动 ComfyUI 前先等 extra_model_paths 里的目录就绪。

        背景：额外模型目录在 U 盘上（E:\\ai-models\\Qwen-Image-2.1 等）。开机后
        U 盘枚举可能比 ComfyUI 启动更晚，ComfyUI 会把目录扫成空，导致提交任务时
        报 `value_not_in_list`（2026-10-30 11:57 实测：千问三个模型全部 not in []）。
        这里等到目录存在且非空，最多等 MODEL_PATH_WAIT_SEC 秒；超时也照常启动，
        只是把等待结果写进日志。
        """
        cfg = s.cfg or {}
        cwd = cfg.get("cwd") or "."
        if cwd == ".":
            cwd = str(BASE)
        yaml_path = Path(str(Path(cwd).resolve())) / "ComfyUI" / "extra_model_paths.yaml"
        if not yaml_path.is_file():
            return
        try:
            text = yaml_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return
        bases: list[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"base_path\s*:\s*(.+)$", line)
            if m:
                p = m.group(1).strip().strip('"').strip("'")
                if p:
                    bases.append(os.path.normpath(p))
        if not bases:
            return

        def ready(path: str) -> bool:
            try:
                if not os.path.isdir(path):
                    return False
                with os.scandir(path) as it:
                    return next(it, None) is not None
            except Exception:
                return False

        deadline = now() + MODEL_PATH_WAIT_SEC
        waited = False
        while now() < deadline:
            pending = [p for p in bases if not ready(p)]
            if not pending:
                if waited:
                    store.audit("supervisor", "service.model_paths.ready",
                                {"paths": bases, "waited_sec": round(MODEL_PATH_WAIT_SEC - (deadline - now()), 1)},
                                source="supervisor")
                return
            if not waited:
                waited = True
                log.info("[supervisor] 等待额外模型目录就绪（可能在 U 盘上）：%s", pending)
            await asyncio.sleep(2)
        store.audit("supervisor", "service.model_paths.timeout",
                    {"pending": [p for p in bases if not ready(p)]},
                    result="error", source="supervisor")
        log.warning("[supervisor] 额外模型目录等待超时，仍继续启动 ComfyUI（可能扫不到对应模型）")

    def _popen(self, s: _Service) -> subprocess.Popen:
        cfg = s.cfg
        exe = str(cfg.get("exe") or "").strip()
        python = str(cfg.get("python") or "").strip()
        if not exe and not python:
            raise ServiceError(f"服务 {s.name} 未配置 python/exe")
        cwd = cfg.get("cwd") or "."
        if cwd == ".":
            cwd = str(BASE)
        cwd = str(Path(cwd).resolve())
        args = cfg.get("args") or []
        env = os.environ.copy()
        env.update({k: str(v) for k, v in (cfg.get("env") or {}).items()})
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"{s.name}.log"
        logf = open(log_path, "a", encoding="utf-8", errors="replace")
        logf.write(f"\n\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} supervisor 启动 {s.name} =====\n")
        logf.flush()
        flags = 0x08000000 if os.name == "nt" else 0   # CREATE_NO_WINDOW
        cmd = [exe, *args] if exe else [python, *args]
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=env,
            stdout=logf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=flags,
        )
        s.proc = proc
        s.pid = proc.pid
        s.started_at = now()
        s.last_error = None
        logf.close()
        return proc

    async def start(self, name: str, wait: bool = True) -> dict:
        s = self.services[name]
        if not s.enabled:
            return s.public()
        async with self._lock:
            await self._probe_one(s)
            if s.state in ("ready", "busy"):
                return s.public()
            still_running = s.proc is not None and s.proc.poll() is None
            if s.state == "starting" or still_running:
                # 已经在启动中：绝不重复 spawn（否则第二个进程绑不上端口会立刻退出，
                # 还会把「连续失败」计数打高，进入无谓的退避）。
                s.state = "starting"
            else:
                cd = START_COOLDOWN - (now() - self._last_start.get(name, 0))
                if cd > 0:
                    s.last_error = f"启动冷却中，还需 {cd:.0f}s"
                    return s.public()
                cnt, until = self._fail.get(name, (0, 0.0))
                if now() < until:
                    s.last_error = f"连续失败 {cnt} 次，退避至 {time.strftime('%H:%M:%S', time.localtime(until))}"
                    s.state = "error"
                    return s.public()
                s.state = "starting"
                s.last_error = None
                self._last_start[name] = now()
                if name == "comfyui":
                    # U 盘上的 extra_model_paths 可能比 ComfyUI 晚就绪，先等一等
                    await self._wait_model_paths(s)
                try:
                    self._popen(s)
                except Exception as e:
                    s.state = "error"
                    s.last_error = f"启动失败: {e}"
                    cnt, _ = self._fail.get(name, (0, 0.0))
                    self._fail[name] = (cnt + 1, now() + FAIL_BACKOFF if cnt + 1 >= 3 else 0.0)
                    return s.public()

        if not wait:
            return s.public()

        timeout = float(s.cfg.get("start_timeout_sec", 120))
        deadline = now() + timeout
        while now() < deadline:
            await asyncio.sleep(1.5)
            if s.proc and s.proc.poll() is not None:
                s.state = "error"
                s.last_error = f"进程退出 code={s.proc.returncode}，详见 logs/{s.name}.log"
                cnt, _ = self._fail.get(name, (0, 0.0))
                self._fail[name] = (cnt + 1, now() + FAIL_BACKOFF if cnt + 1 >= 3 else 0.0)
                return s.public()
            if s.name == "comfyui" or s.health_url:
                ok = await _http_ok(s.health_url or "http://127.0.0.1:8188/system_stats")
            elif s.extra_ports:
                ok = any([await _port_open(p) for p in s.extra_ports])
            else:
                ok = await _port_open(s.port)
            if ok:
                s.state = "ready"
                s.started_at = now()
                self.touch_activity()   # 刚起来的不算闲置，别让 idle_loop 立刻又把它睡回去
                self._fail.pop(name, None)
                store.audit("supervisor", f"service.start.{name}",
                            {"pid": s.pid, "seconds": round(now() - self._last_start[name], 1)},
                            source="supervisor")
                return s.public()
        # 超时但进程还活着：保持 starting，让后续请求继续等到端口就绪，
        # 而不是标成 error 再 spawn 第二个进程。冷启动（重启后首启）可能要好几分钟。
        if s.proc is not None and s.proc.poll() is None:
            s.state = "starting"
            s.last_error = (f"启动中（已等 {timeout:.0f}s 仍未就绪，进程 pid={s.pid} 仍在运行；"
                            f"首次冷启动可能需要数分钟，稍后重试即可）")
        else:
            s.state = "error"
            s.last_error = f"启动超时（{timeout:.0f}s）且进程已退出，详见 logs/{s.name}.log"
            cnt, _ = self._fail.get(name, (0, 0.0))
            self._fail[name] = (cnt + 1, now() + FAIL_BACKOFF if cnt + 1 >= 3 else 0.0)
        return s.public()

    async def stop(self, name: str, only_if_ours: bool = True, force: bool = False) -> dict:
        s = self.services[name]
        async with self._lock:
            if only_if_ours and s.proc is None:
                pattern = str(s.cfg.get("kill_pattern") or "").strip()
                if force and pattern:
                    # 显式 force：允许按 kill_pattern 结束外部实例（如手工启动的 NapCat）
                    s.last_error = "强制停止外部实例（force）"
                    _kill_matched(pattern)
                    for _ in range(20):
                        await asyncio.sleep(0.3)
                        await self._probe_one(s)
                        if s.state not in ("ready", "busy", "starting"):
                            break
                    s.proc = None
                    return s.public()
                # 不是本进程拉起的（用户手工开的 ComfyUI 等），不擅自杀，也不谎报 down。
                s.last_error = "外部实例，未由 Core 启动，跳过停止（可 force=true）"
                await self._probe_one(s)
                return s.public()
            proc = s.proc
            # 支持优雅停止命令（如 PostgreSQL 的 pg_ctl stop -m fast）
            stop_exe = str(s.cfg.get("stop_exe") or "").strip()
            if stop_exe and os.path.isfile(stop_exe):
                stop_args = [str(a) for a in (s.cfg.get("stop_args") or [])]
                try:
                    subprocess.run(
                        [stop_exe, *stop_args], capture_output=True, timeout=60,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except Exception as e:  # noqa: BLE001
                    s.last_error = f"优雅停止失败: {e}"
            if proc and proc.poll() is None:
                pid = proc.pid
                try:
                    proc.terminate()
                    for _ in range(20):
                        if proc.poll() is not None:
                            break
                        await asyncio.sleep(0.3)
                    if proc.poll() is None:
                        proc.kill()
                except Exception as e:
                    s.last_error = f"停止失败: {e}"
                # 父进程退出后子进程可能还在（如 GSV supervisor 拉起的 api_v2/adapter），
                # Windows 下用 taskkill /T 清整棵进程树，避免端口仍被占用。
                _kill_tree(pid)
                pattern = str(s.cfg.get("kill_pattern") or "").strip()
                if pattern:
                    killed = _kill_matched(pattern)
                    if killed:
                        log.info("[supervisor] %s 清理游离子进程: %s", name, killed)
            s.proc = None
            s.pid = None
            s.state = "down"
            s.started_at = 0.0
        store.audit("supervisor", f"service.stop.{name}", {}, source="supervisor")
        return s.public()

    async def restart(self, name: str, wait: bool = True) -> dict:
        await self.stop(name)
        return await self.start(name, wait=wait)

    # -------------------------------------------------- 全栈启停（P1）

    def list_services(self) -> list[dict]:
        return [s.public() for s in self.services.values()]

    async def start_all(self) -> dict:
        """按 autostart 拉起全部服务，并发等待就绪。"""
        targets = [s for s in self.services.values() if s.enabled and s.autostart]
        started: dict[str, dict] = {}
        for s in targets:
            try:
                started[s.name] = await self.start(s.name, wait=False)
            except Exception as e:  # noqa: BLE001
                s.state = "error"
                s.last_error = f"启动失败: {e}"
                started[s.name] = s.public()

        async def _wait_one(s: _Service) -> dict:
            timeout = float(s.cfg.get("start_timeout_sec", 120))
            deadline = now() + timeout
            while now() < deadline:
                if s.proc is not None and s.proc.poll() is not None:
                    s.state = "error"
                    s.last_error = f"进程退出 code={s.proc.returncode}，详见 logs/{s.name}.log"
                    return s.public()
                if await self._is_up(s):
                    s.state = "ready"
                    s.last_error = None
                    if not s.started_at:
                        s.started_at = now()
                    self.touch_activity()
                    self._fail.pop(s.name, None)
                    return s.public()
                await asyncio.sleep(1.5)
            if s.proc is not None and s.proc.poll() is None:
                s.state = "starting"
                s.last_error = f"启动中（已等 {timeout:.0f}s 仍未就绪，进程仍在运行）"
            else:
                s.state = "error"
                s.last_error = f"启动超时（{timeout:.0f}s）且进程已退出"
            return s.public()

        results = await asyncio.gather(*[_wait_one(s) for s in targets])
        out = {r["name"]: r for r in results}
        store.audit("supervisor", "services.start_all",
                    {"targets": [s.name for s in targets],
                     "ready": [k for k, v in out.items() if v.get("state") in ("ready", "busy")]},
                    source="supervisor")
        return out

    async def stop_all(self) -> dict:
        """停止全部由 Core 拉起的服务（逆序），外部实例跳过。"""
        out: dict[str, dict] = {}
        for s in reversed(list(self.services.values())):
            if not s.enabled:
                out[s.name] = s.public()
                continue
            try:
                out[s.name] = await self.stop(s.name)
            except Exception as e:  # noqa: BLE001
                out[s.name] = {"name": s.name, "state": "error", "error": str(e)}
        await self.probe_all()
        store.audit("supervisor", "services.stop_all",
                    {"stopped": list(out.keys())}, source="supervisor")
        return out

    def logs(self, name: str, lines: int = 100) -> dict:
        s = self.services.get(name)
        if not s:
            raise ServiceError(f"未知服务 {name}")
        lines = max(1, min(int(lines or 100), 2000))
        path = LOG_DIR / f"{name}.log"
        if not path.is_file():
            return {"name": name, "path": str(path), "lines": [], "exists": False}
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            raise ServiceError(f"读取日志失败: {e}")
        tail = text.splitlines()[-lines:]
        return {"name": name, "path": str(path), "lines": tail, "exists": True}

    # -------------------------------------------------- ensure（Core 调用入口）

    def touch_activity(self) -> None:
        self._last_activity = now()

    def seconds_since_activity(self) -> float:
        if not self._last_activity:
            return 1e9
        return now() - self._last_activity

    async def ensure_gateway(self) -> dict:
        s = self.services.get("gateway")
        if not s:
            return {}
        st = await self.probe("gateway")
        if st["state"] == "ready":
            return st
        return await self.start("gateway")

    async def ensure(self, kind: str = "image") -> dict:
        """确保媒体后端可用。kind: image|voice"""
        mode = self.get_mode()
        if mode == "off":
            raise MediaDisabled("media_off")
        self.touch_activity()
        await self.ensure_gateway()
        if kind == "voice":
            st = await self.probe("kokoro")
            if st["state"] != "ready":
                st = await self.start("kokoro")
            if st["state"] != "ready":
                raise ServiceError(f"TTS 未就绪: {st.get('error') or st['state']}")
            return st
        st = await self.probe("comfyui")
        if st["state"] in ("ready", "busy"):
            self._comfy_freed = False      # 有新的媒体请求，模型会被重新加载
            return st
        st = await self.start("comfyui")
        self._comfy_freed = False
        if st["state"] not in ("ready", "busy"):
            raise ServiceError(f"ComfyUI 未就绪: {st.get('error') or st['state']}")
        return st

    async def wake(self) -> dict:
        if self.get_mode() == "off":
            raise MediaDisabled("media_off")
        await self.ensure_gateway()
        return await self.start("comfyui")

    # -------------------------------------------------- 空闲休眠

    async def gateway_activity(self) -> tuple[int, float]:
        """网关侧的活动：QQ 走 LangBot→网关 生图时，Core 自己收不到任何信号，
        必须从这里读，否则会在刚出完图后把 ComfyUI 睡掉（实测发生过）。
        用 /api/pulse（纯内存），不能用会探测后端的 /api/status（最坏 9s）。

        注意：active_tasks 在 /api/pulse 里是 **嵌套在 activity 下**的，
        早先直接取顶层字段恒为 0，等于这条守卫从未生效——曾导致生成过程中
        被 /free，模型反复卸载重载，步进从 1.3s/it 劣化到 40s/it。
        """
        gw = self.services.get("gateway")
        port = gw.port if gw else 8899
        try:
            async with httpx.AsyncClient(timeout=2.5) as c:
                r = await c.get(f"http://127.0.0.1:{port}/api/pulse")
            d = r.json() or {}
            act = d.get("activity") or {}
            active = int(act.get("active_tasks") or d.get("active_tasks") or 0)
            last = float(act.get("last_activity_at") or d.get("last_activity_at") or 0.0)
            return active, last
        except Exception:
            return 0, 0.0

    async def comfy_busy(self) -> bool:
        """ComfyUI 自己的队列是否还有任务——最可靠的「正在生成」信号。"""
        s = self.services.get("comfyui")
        base = "http://127.0.0.1:8188"
        if s and s.cfg.get("health_url"):
            base = str(s.cfg["health_url"]).split("/system_stats")[0].rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{base}/queue")
            q = r.json() or {}
            return bool(q.get("queue_running") or q.get("queue_pending"))
        except Exception:
            return True   # 查不到就当忙，宁可不卸

    async def free_comfy_models(self) -> dict:
        """让 ComfyUI 卸载模型、释放显存与常驻内存，但**保留进程**。

        为什么不用 kill：冷启动要加载几十个 custom node + 模型，正常 70~110 秒，
        内存紧张时实测飙到 532 秒，直接超过 MCP 工具超时 → 用户看到"崩了"。
        /free 只卸载模型，进程与 CUDA 上下文留着，下次生成 30~40 秒即可。
        """
        s = self.services.get("comfyui")
        base = "http://127.0.0.1:8188"
        if s and s.cfg.get("health_url"):
            base = str(s.cfg["health_url"]).split("/system_stats")[0].rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(f"{base}/free",
                                 json={"unload_models": True, "free_memory": True})
            return {"ok": r.status_code < 400, "status": r.status_code}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def idle_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(30)
                if self.get_mode() != "on_demand":
                    continue
                s = self.services.get("comfyui")
                if not s or s.state not in ("ready", "busy"):
                    continue
                if s.proc is None:
                    # 外部拉起的实例不归我们管，避免「谎报休眠」和状态来回跳。
                    continue
                if store.tasks_active("image"):
                    continue
                gw_active, gw_last = await self.gateway_activity()
                if gw_active > 0:
                    self._comfy_freed = False
                    continue
                if await self.comfy_busy():
                    # ComfyUI 自己还有任务在跑，绝不能卸载模型
                    self._comfy_freed = False
                    continue
                idle = self.seconds_since_activity()
                if gw_last:
                    idle = min(idle, now() - gw_last)
                limit = int(CFG["core"].get("idle_sleep_sec", 600))
                if idle < limit:
                    # 有新活动 → 模型会被重新加载，解除「已卸载」标记。
                    # 注意：不能只依赖 ensure() 重置——网关的 ensure_comfy()
                    # 会先探活，ComfyUI 活着就直接返回、根本不调 Core。
                    self._comfy_freed = False
                    continue
                if self._comfy_freed:
                    continue
                # 优先「卸载模型」而不是「杀进程」：省下的是显存和大头内存，
                # 但下一次生成不用再付冷启动成本。
                res = await self.free_comfy_models()
                if res.get("ok"):
                    self._comfy_freed = True
                    store.audit("supervisor", "service.free.comfyui",
                                {"idle_sec": round(idle, 1)}, source="supervisor")
                else:
                    # /free 不可用才退回杀进程
                    await self.stop("comfyui")
                    self._comfy_freed = True
                    store.audit("supervisor", "service.sleep.comfyui",
                                {"idle_sec": round(idle, 1),
                                 "reason": res.get("error") or res.get("status")},
                                source="supervisor")
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    async def status(self) -> dict:
        svc = await self.probe_all()
        return {
            "mode": self.get_mode(),
            "idle_sleep_sec": int(CFG["core"].get("idle_sleep_sec", 600)),
            "prewarm_on_start": bool(CFG["core"].get("prewarm_on_start", False)),
            "active_tasks": len(store.tasks_active()),
            "seconds_since_activity": round(self.seconds_since_activity(), 1),
            "comfy_models_unloaded": bool(self._comfy_freed),
            "autostart": [s.name for s in self.services.values() if s.enabled and s.autostart],
            "services": svc,
        }


# ---------------------------------------------------------------- Gateway 客户端

class GatewayClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        g = CFG.get("gateway", {})
        self.base = (base_url or g.get("url") or "http://127.0.0.1:8899").rstrip("/")
        self.timeout = float(timeout or g.get("timeout_sec") or 30)

    async def _request(self, method: str, path: str, timeout: float | None = None, **kw) -> dict:
        url = f"{self.base}{path}"
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as c:
            r = await c.request(method, url, **kw)
        if r.status_code >= 400:
            try:
                body = r.json()
            except Exception:
                body = {"error": r.text[:300]}
            raise ServiceError(f"gateway {path} -> HTTP {r.status_code}: {body.get('error') or body}")
        try:
            return r.json()
        except Exception as e:
            raise ServiceError(f"gateway {path} 返回非 JSON: {e}")

    async def status(self) -> dict:
        return await self._request("GET", "/api/status")

    async def models(self) -> dict:
        return await self._request("GET", "/api/models")

    async def loras(self, q: str = "") -> dict:
        return await self._request("GET", "/api/loras", params={"q": q} if q else None)

    async def characters(self) -> dict:
        return await self._request("GET", "/api/characters")

    async def voices(self) -> dict:
        return await self._request("GET", "/api/voices")

    async def generate(self, **payload) -> dict:
        payload.setdefault("async", True)
        # 网关在 ComfyUI 未就绪时会先 ensure（可能需要等唤醒），所以给足超时。
        return await self._request("POST", "/api/generate", json=payload, timeout=300)

    async def task(self, task_id: str) -> dict:
        return await self._request("GET", f"/api/tasks/{task_id}")

    async def cancel(self, task_id: str) -> dict:
        return await self._request("POST", f"/api/tasks/{task_id}/cancel")

    async def tts(self, text: str, voice: str = "", engine: str = "kokoro",
                  speed: float = 1.0) -> dict:
        return await self._request("POST", "/api/tts", json={
            "text": text, "voice": voice, "engine": engine, "speed": speed,
        }, timeout=180)

    async def wait_task(self, task_id: str, timeout: float = 240.0,
                        interval: float = 2.0) -> dict:
        deadline = now() + timeout
        last: dict = {}
        while now() < deadline:
            last = await self.task(task_id)
            st = (last.get("task") or last).get("status")
            if st in ("succeeded", "failed", "canceled"):
                return last.get("task") or last
            await asyncio.sleep(interval)
        return last.get("task") or {"status": "timeout", "id": task_id}
