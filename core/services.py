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
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

from . import store
from .store import CFG, BASE, LOG_DIR, now

MEDIA_MODES = ("off", "on_demand", "prewarm")
START_COOLDOWN = 30.0        # 同一服务两次启动最小间隔
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

    def public(self) -> dict:
        return {
            "state": self.state if self.enabled else "disabled",
            "port": self.port,
            "last_check": round(self.last_check, 1),
            "pid": self.pid,
            "error": self.last_error,
            "uptime_sec": round(now() - self.started_at, 1) if self.started_at else 0,
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


class Supervisor:
    def __init__(self) -> None:
        svc = CFG.get("services", {})
        self.services: dict[str, _Service] = {k: _Service(k, v) for k, v in svc.items()}
        self._lock = asyncio.Lock()
        self._last_start: dict[str, float] = {}
        self._fail: dict[str, tuple[int, float]] = {}
        self._last_activity = now()   # 以进程启动时间为基线，否则会被算成「闲置 10 亿秒」
        self._mode_cache: str | None = None

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

    async def _probe_one(self, s: _Service) -> None:
        s.last_check = now()
        if not s.enabled:
            s.state = "disabled"
            return
        if s.name == "comfyui":
            ok = await _http_ok(s.cfg.get("health_url", "http://127.0.0.1:8188/system_stats"))
        else:
            ok = await _port_open(s.port)
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

    def _popen(self, s: _Service) -> subprocess.Popen:
        cfg = s.cfg
        python = cfg.get("python")
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
        proc = subprocess.Popen(
            [python, *args], cwd=cwd, env=env,
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
            if s.name == "comfyui":
                ok = await _http_ok(s.cfg.get("health_url", "http://127.0.0.1:8188/system_stats"))
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

    async def stop(self, name: str, only_if_ours: bool = True) -> dict:
        s = self.services[name]
        async with self._lock:
            if only_if_ours and s.proc is None:
                # 不是本进程拉起的（用户手工开的 ComfyUI 等），不擅自杀，也不谎报 down。
                s.last_error = "外部实例，未由 Core 启动，跳过停止"
                await self._probe_one(s)
                return s.public()
            proc = s.proc
            if proc and proc.poll() is None:
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
            s.proc = None
            s.pid = None
            s.state = "down"
            s.started_at = 0.0
        store.audit("supervisor", f"service.stop.{name}", {}, source="supervisor")
        return s.public()

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
            return st
        st = await self.start("comfyui")
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
        用 /api/pulse（纯内存），不能用会探测后端的 /api/status（最坏 9s）。"""
        gw = self.services.get("gateway")
        port = gw.port if gw else 8899
        try:
            async with httpx.AsyncClient(timeout=2.5) as c:
                r = await c.get(f"http://127.0.0.1:{port}/api/pulse")
            d = r.json() or {}
            return int(d.get("active_tasks") or 0), float(d.get("last_activity_at") or 0.0)
        except Exception:
            return 0, 0.0

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
                    continue
                idle = self.seconds_since_activity()
                if gw_last:
                    idle = min(idle, now() - gw_last)
                limit = int(CFG["core"].get("idle_sleep_sec", 600))
                if idle >= limit:
                    await self.stop("comfyui")
                    store.audit("supervisor", "service.sleep.comfyui",
                                {"idle_sec": round(idle, 1)}, source="supervisor")
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
