"""Möbius 启动器（P5）

职责：
- 单实例、管理员权限（NapCat 注入需要提权）
- 启动顺序：PostgreSQL → Core → Core 内全部服务（含原生 LangBot / NapCat / 媒体）
- 停止顺序：Core 服务 → Core → PostgreSQL
- 托盘/窗口：状态、启动、停止、打开控制台、日志
- 无 GUI 模式：--headless start|stop|status

用法：
  python mobius_launcher.py                # GUI（自动提权）
  python mobius_launcher.py --headless start
  python mobius_launcher.py --headless status
  python mobius_launcher.py --headless stop
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

def _find_root(start: str) -> str:
    """从 start 向上找含 config.json 的 Möbius 根目录。"""
    env_root = os.environ.get("MOBIUS_ROOT", "").strip()
    if env_root and os.path.isfile(os.path.join(env_root, "config.json")):
        return env_root
    cur = os.path.abspath(start)
    for _ in range(5):
        if os.path.isfile(os.path.join(cur, "config.json")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


ROOT = _find_root(os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
                  else os.path.dirname(os.path.abspath(__file__)))

CONFIG_PATH = os.path.join(ROOT, "config.json")
LOG_DIR = os.path.join(ROOT, "logs")
CORE_HEALTH = "http://127.0.0.1:8900/health"
CORE_API = "http://127.0.0.1:8900"
CONSOLE_URL = CORE_API + "/console"
CORE_LOG = os.path.join(LOG_DIR, "core.launcher.log")
LAUNCHER_LOG = os.path.join(LOG_DIR, "launcher.log")
MUTEX_NAME = "Global\\MobiusLauncherSingleton"


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LAUNCHER_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 平台工具
# ---------------------------------------------------------------------------


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """以管理员身份重新启动自己（会弹 UAC）。返回是否成功发起。"""
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = " ".join(sys.argv[1:])
    else:
        exe = sys.executable
        script = os.path.abspath(__file__)
        params = f'"{script}" ' + " ".join(sys.argv[1:])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, ROOT, 1)
        return rc > 32
    except Exception as exc:  # noqa: BLE001
        log(f"提权失败：{exc}")
        return False


def single_instance() -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return True
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            return False
        # 故意不释放句柄：进程退出时由系统回收
        return True
    except Exception:
        return True


def port_open(port: int, timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def http_json(path: str, method: str = "GET", body: dict | None = None, timeout: float = 10.0) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        CORE_API + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def wait_until(fn, timeout: float, interval: float = 1.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if fn():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(interval)
    return False


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def launcher_cfg(cfg: dict) -> dict:
    big = cfg.get("launcher") or {}
    svc = cfg.get("services") or {}
    pg = svc.get("postgres") or {}
    return {
        "core_python": big.get("core_python") or (svc.get("gateway") or {}).get("python") or sys.executable,
        "core_args": big.get("core_args") or ["-m", "core.server"],
        "pg_ctl": big.get("pg_ctl") or pg.get("stop_exe") or pg.get("exe"),
        "pg_data": big.get("pg_data") or _pg_data(pg),
        "pg_log": big.get("pg_log") or os.path.join(os.path.dirname(_pg_data(pg) or ""), "pg.log"),
        "pg_port": int(big.get("pg_port") or (pg.get("ports") or [55432])[0]),
        "start_napcat": bool(big.get("start_napcat", True)),
        "elevate": bool(big.get("elevate", True)),
    }


def _pg_data(pg: dict) -> str:
    args = pg.get("args") or []
    try:
        idx = args.index("-D")
        return str(args[idx + 1])
    except (ValueError, IndexError):
        return ""


def ensure_launcher_config(cfg: dict) -> None:
    if "launcher" in cfg:
        return
    svc = cfg.get("services") or {}
    pg = svc.get("postgres") or {}
    cfg["launcher"] = {
        "core_python": (svc.get("gateway") or {}).get("python", ""),
        "core_args": ["-m", "core.server"],
        "pg_ctl": pg.get("stop_exe") or pg.get("exe", ""),
        "pg_data": _pg_data(pg),
        "pg_log": os.path.join(os.path.dirname(_pg_data(pg) or ""), "pg.log"),
        "pg_port": (pg.get("ports") or [55432])[0],
        "start_napcat": True,
        "elevate": True,
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


def start_postgres(lc: dict) -> bool:
    if port_open(lc["pg_port"]):
        log("PostgreSQL 已在运行")
        return True
    if not (lc["pg_ctl"] and os.path.isfile(lc["pg_ctl"])):
        log(f"找不到 pg_ctl: {lc['pg_ctl']}")
        return False
    log("启动 PostgreSQL …")
    subprocess.Popen(
        [lc["pg_ctl"], "-D", lc["pg_data"], "-o",
         f"-p {lc['pg_port']} -c listen_addresses=127.0.0.1",
         "-l", lc["pg_log"], "start"],
        cwd=os.path.dirname(lc["pg_ctl"]),
        creationflags=0x08000000,
    )
    return wait_until(lambda: port_open(lc["pg_port"]), 60)


def stop_postgres(lc: dict) -> None:
    if not port_open(lc["pg_port"]):
        return
    log("停止 PostgreSQL …")
    try:
        subprocess.run(
            [lc["pg_ctl"], "-D", lc["pg_data"], "stop", "-m", "fast"],
            capture_output=True, timeout=60, creationflags=0x08000000,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"停止 PostgreSQL 失败：{exc}")


def core_healthy() -> bool:
    try:
        with urllib.request.urlopen(CORE_HEALTH, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_core(lc: dict) -> bool:
    if core_healthy():
        log("Core 已在运行")
        return True
    os.makedirs(LOG_DIR, exist_ok=True)
    log("启动 Core …")
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    with open(CORE_LOG, "a", encoding="utf-8", errors="replace") as logf:
        logf.write(f"\n\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} launcher 启动 Core =====\n")
        logf.flush()
        subprocess.Popen(
            [lc["core_python"], *lc["core_args"]],
            cwd=ROOT, env=env,
            stdout=logf, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=0x08000000,
        )
    return wait_until(core_healthy, 90)


def start_services() -> bool:
    log("启动全部服务（Core supervisor）…")
    try:
        http_json("/api/services/start-all", "POST", {}, timeout=20)
    except Exception as exc:  # noqa: BLE001
        log(f"start-all 请求失败：{exc}")
        return False
    return wait_until(lambda: _autostart_ready(), 240, interval=3.0)


def _autostart_ready() -> bool:
    data = http_json("/api/services")
    services = data.get("services") or []
    for svc in services:
        if svc.get("enabled") and svc.get("autostart"):
            if svc.get("state") not in ("ready", "busy", "error", "disabled"):
                return False
    return True


def start_napcat() -> bool:
    try:
        http_json("/api/services/napcat/start", "POST", {}, timeout=20)
    except Exception as exc:  # noqa: BLE001
        log(f"启动 NapCat 失败：{exc}")
        return False
    return wait_until(lambda: port_open(3001), 90)


def stop_all() -> None:
    """先停服务（保留 PostgreSQL），再停 Core，最后停 PostgreSQL。"""
    if core_healthy():
        log("停止 Core 服务 …")
        try:
            http_json("/api/services/stop-all", "POST", {}, timeout=120)
        except Exception as exc:  # noqa: BLE001
            log(f"stop-all 失败：{exc}")
        try:
            http_json("/api/shutdown", "POST", {}, timeout=15)
        except Exception:  # noqa: BLE001
            pass
        wait_until(lambda: not core_healthy(), 45)
    try:
        cfg = load_config()
        lc = launcher_cfg(cfg)
        stop_postgres(lc)
    except Exception as exc:  # noqa: BLE001
        log(f"停止 PostgreSQL 失败：{exc}")


def cmd_start() -> int:
    cfg = load_config()
    ensure_launcher_config(cfg)
    lc = launcher_cfg(cfg)
    ok = start_postgres(lc)
    ok = ok and start_core(lc)
    ok = ok and start_services()
    if ok and lc.get("start_napcat"):
        start_napcat()
    log("启动完成" if ok else "启动未完全成功，请查看日志")
    return 0 if ok else 1


def cmd_stop() -> int:
    stop_all()
    log("已停止")
    return 0


def cmd_status() -> int:
    try:
        data = http_json("/api/services")
    except Exception as exc:  # noqa: BLE001
        print(f"Core 不可达：{exc}")
        return 1
    for svc in data.get("services") or []:
        if svc.get("enabled"):
            print(f"  {svc['name']:<12} {svc['state']}")
    return 0


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


def run_gui() -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("Möbius 启动器")
    root.geometry("560x420")
    root.minsize(520, 380)

    status_var = tk.StringVar(value="正在检查…")
    header = ttk.Label(root, text="Möbius", font=("Segoe UI", 16, "bold"))
    header.pack(anchor="w", padx=14, pady=(12, 0))
    ttk.Label(root, textvariable=status_var, foreground="#555").pack(anchor="w", padx=14)

    tree = ttk.Treeview(root, columns=("state",), show="tree headings", height=10)
    tree.heading("#0", text="服务")
    tree.heading("state", text="状态")
    tree.column("#0", width=220)
    tree.column("state", width=120)
    tree.pack(fill="both", expand=True, padx=14, pady=10)

    btns = ttk.Frame(root)
    btns.pack(fill="x", padx=14, pady=(0, 12))

    def refresh() -> None:
        try:
            data = http_json("/api/services", timeout=8)
            tree.delete(*tree.get_children())
            ready = 0
            total = 0
            for svc in data.get("services") or []:
                if not svc.get("enabled"):
                    continue
                total += 1
                if svc.get("state") in ("ready", "busy"):
                    ready += 1
                tree.insert("", "end", text=svc["name"], values=(svc.get("state"),))
            status_var.set(f"Core 在线 · 服务就绪 {ready}/{total}")
        except Exception:  # noqa: BLE001
            tree.delete(*tree.get_children())
            status_var.set("Core 离线（未启动）")
        root.after(3000, refresh)

    def do_start() -> None:
        status_var.set("启动中…（PostgreSQL → Core → 服务）")
        root.after(50, lambda: _run_bg(cmd_start, "启动完成"))

    def do_stop() -> None:
        if not messagebox.askyesno("停止", "停止全部服务并退出？"):
            return
        status_var.set("停止中…")
        root.after(50, lambda: _run_bg(cmd_stop, "已停止"))

    def _run_bg(fn, ok_text: str) -> None:
        import threading

        def job() -> None:
            rc = fn()
            root.after(0, lambda: status_var.set(ok_text if rc == 0 else "操作未完全成功，见日志"))

        threading.Thread(target=job, daemon=True).start()

    ttk.Button(btns, text="启动全部", command=do_start).pack(side="left")
    ttk.Button(btns, text="全部停止", command=do_stop).pack(side="left", padx=8)
    ttk.Button(btns, text="打开控制台", command=lambda: os.startfile(CONSOLE_URL)).pack(side="left", padx=8)
    ttk.Button(btns, text="打开日志", command=lambda: os.startfile(LOG_DIR)).pack(side="left", padx=8)
    ttk.Button(btns, text="退出", command=root.destroy).pack(side="right")

    refresh()
    root.mainloop()


# ---------------------------------------------------------------------------


def _attach_console() -> None:
    """windowed exe 在 --headless 模式下把输出接回调用它的控制台。"""
    if not getattr(sys, "frozen", False):
        return
    try:
        if not ctypes.windll.kernel32.AttachConsole(-1):
            return
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Möbius 启动器")
    parser.add_argument("--headless", choices=["start", "stop", "status"], default=None)
    parser.add_argument("--no-elevate", action="store_true")
    parser.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.headless:
        _attach_console()

    if not single_instance():
        log("已有启动器实例在运行")
        return 0

    cfg = load_config()
    lc = launcher_cfg(cfg)
    if lc.get("elevate") and not args.no_elevate and not is_admin():
        log("需要管理员权限（NapCat 注入），正在请求提权…")
        if relaunch_as_admin():
            return 0
        log("提权被取消，继续以普通权限运行（NapCat 可能无法启动）")

    if args.headless == "start":
        return cmd_start()
    if args.headless == "stop":
        return cmd_stop()
    if args.headless == "status":
        return cmd_status()
    run_gui()
    return 0


if __name__ == "__main__":
    try:
        _rc = main()
    except Exception:  # noqa: BLE001
        import traceback
        try:
            log("启动器异常：\n" + traceback.format_exc())
        except Exception:
            pass
        _rc = 1
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(_rc)
