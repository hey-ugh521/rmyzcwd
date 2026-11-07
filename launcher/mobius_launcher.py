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
import shutil
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
        if (os.path.isfile(os.path.join(cur, "config.json"))
                or os.path.isfile(os.path.join(cur, "config.example.json"))):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
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
        kernel32.SetLastError(0)
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


def _path_map(cfg: dict) -> dict[str, str]:
    base = {
        "ROOT": ROOT,
        "RUNTIME": os.environ.get("MOBIUS_RUNTIME", "").strip() or os.path.join(ROOT, "runtime"),
        "MODELS": os.environ.get("MOBIUS_MODELS", "").strip() or os.path.join(ROOT, "models"),
        "DATA": os.environ.get("MOBIUS_DATA", "").strip() or os.path.join(ROOT, "data"),
        "LOGS": LOG_DIR,
    }
    for key, value in (cfg.get("paths") or {}).items():
        if isinstance(value, str):
            base[str(key)] = _expand(value, base)
    return base


def _expand(value: str, mapping: dict[str, str]) -> str:
    if not isinstance(value, str) or "${" not in value:
        return value
    out = value
    for _ in range(5):
        prev = out
        for name, repl in mapping.items():
            out = out.replace("${" + name + "}", repl)
        if out == prev:
            break
    return out


def _expand_cfg(cfg: dict, mapping: dict[str, str]) -> dict:
    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            return _expand(node, mapping)
        return node

    return walk(cfg)


def _ensure_config_file() -> None:
    """首次运行：没有 config.json 时用示例配置初始化（向导随后补全）。"""
    if os.path.isfile(CONFIG_PATH):
        return
    example = os.path.join(ROOT, "config.example.json")
    if os.path.isfile(example):
        shutil.copyfile(example, CONFIG_PATH)
        log("已从 config.example.json 初始化 config.json")


def load_config() -> dict:
    _ensure_config_file()
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    return _expand_cfg(cfg, _path_map(cfg))


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
    env.setdefault("MOBIUS_ROOT", ROOT)
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
# 首启向导（P4）
# ---------------------------------------------------------------------------

def _manifest_path() -> str:
    return os.path.join(ROOT, "ops", "l3", "packs", "fragments.json")


def _versions_path() -> str:
    return os.path.join(ROOT, "versions.json")


def components_state() -> list[dict]:
    import json as _json

    try:
        manifest = _json.loads(open(_manifest_path(), encoding="utf-8").read())
    except OSError:
        return []
    try:
        versions = _json.loads(open(_versions_path(), encoding="utf-8").read()).get("components", {})
    except (OSError, ValueError):
        versions = {}
    rows = []
    for cid, comp in sorted((manifest.get("components") or {}).items()):
        rows.append({
            "id": cid,
            "version": comp.get("version"),
            "required": bool(comp.get("required")),
            "size": int(comp.get("size") or 0),
            "installed": cid in versions,
        })
    return rows


def install_components(ids: list[str] | None = None, source_dir: str = "") -> bool:
    import importlib.util
    from pathlib import Path

    try:
        spec = importlib.util.spec_from_file_location(
            "mobius_downloader", os.path.join(ROOT, "ops", "l3", "downloader.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        dl = mod.Downloader(
            manifest=Path(_manifest_path()),
            root=Path(ROOT),
            source_dir=Path(source_dir) if source_dir else None,
        )
        target = ids or [c["id"] for c in components_state() if c["required"]]
        for cid in dl.resolve(target):
            log(f"安装组件: {cid}")
            dl.install(cid)
        return True
    except Exception as exc:  # noqa: BLE001
        log(f"组件安装失败: {exc}")
        return False


def _raw_config() -> dict:
    import json as _json

    _ensure_config_file()
    try:
        return _json.loads(open(CONFIG_PATH, encoding="utf-8").read())
    except (OSError, ValueError):
        return {}


def _save_raw_config(cfg: dict) -> None:
    import json as _json

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        _json.dump(cfg, f, ensure_ascii=False, indent=2)


def configure_llm(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    cfg = _raw_config()
    llm = cfg.setdefault("llm", {})
    if base_url:
        llm["base_url"] = base_url.rstrip("/")
    if api_key:
        llm["api_key"] = api_key
    if model:
        llm["model"] = model
    _save_raw_config(cfg)
    return test_llm()


def test_llm() -> tuple[bool, str]:
    import json as _json

    cfg = _raw_config()
    llm = cfg.get("llm") or {}
    base = str(llm.get("base_url") or "").rstrip("/")
    key = str(llm.get("api_key") or "")
    model = str(llm.get("model") or "")
    if not base or not key or not model:
        return False, "LLM 配置不完整（base_url / api_key / model）"
    body = _json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read().decode("utf-8") or "{}")
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return True, text.strip() or "连接成功"
    except Exception as exc:  # noqa: BLE001
        return False, f"连接失败: {exc}"


def migrate_models(src: str, dst: str) -> bool:
    if not src or not os.path.isdir(src):
        log(f"模型源目录不存在: {src}")
        return False
    os.makedirs(dst, exist_ok=True)
    log(f"迁移模型: {src} -> {dst}")
    try:
        proc = subprocess.run(
            ["robocopy", src, dst, "/E", "/XJ", "/NFL", "/NDL", "/NJH", "/NJS", "/R:1", "/W:1"],
            capture_output=True, text=True,
        )
        ok = proc.returncode < 8
        log(f"迁移完成（robocopy 退出码 {proc.returncode}）")
        return ok
    except Exception as exc:  # noqa: BLE001
        log(f"迁移失败: {exc}")
        return False


def start_napcat_and_open() -> bool:
    ok = start_napcat()
    try:
        os.startfile("http://127.0.0.1:6099/webui")
    except OSError:
        pass
    return ok


def mark_first_run_done() -> None:
    cfg = _raw_config()
    cfg.setdefault("launcher", {})["first_run_done"] = True
    _save_raw_config(cfg)


def is_first_run() -> bool:
    cfg = _raw_config()
    return not bool((cfg.get("launcher") or {}).get("first_run_done"))



# ---------------------------------------------------------------------------
# GitHub Token（私有仓库组件下载）
# ---------------------------------------------------------------------------

GH_API_RELEASE = "https://api.github.com/repos/hey-ugh521/rmyzcwd/releases/tags/l3-20261108"


def _mask(token: str) -> str:
    if not token:
        return ""
    return token[:8] + "…" + token[-4:] if len(token) > 14 else "***"


def _apply_github_token(token: str | None = None, persist: bool = False) -> None:
    """把 token 注入环境变量；persist=True 时写入 config.json（launcher.github_token）。"""
    cfg = _raw_config()
    saved = str((cfg.get("launcher") or {}).get("github_token") or "")
    value = (token or "").strip() or saved
    if not value:
        return
    os.environ["MOBIUS_GH_TOKEN"] = value
    if persist:
        cfg.setdefault("launcher", {})["github_token"] = value
        _save_raw_config(cfg)
        log(f"已保存 GitHub Token（{_mask(value)}）")


def check_github() -> tuple[bool, str]:
    import json as _json

    token = os.environ.get("MOBIUS_GH_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        return False, "未配置 GitHub Token（私有仓库组件需要）"
    req = urllib.request.Request(
        GH_API_RELEASE,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "MobiusLauncher/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = _json.loads(resp.read().decode("utf-8") or "{}")
        return True, f"Release {data.get('tag_name')} 可访问（{len(data.get('assets') or [])} 个资产）"
    except Exception as exc:  # noqa: BLE001
        return False, f"GitHub 校验失败: {exc}"


def cmd_components() -> int:
    rows = components_state()
    if not rows:
        print("找不到组件清单（ops/l3/packs/fragments.json）")
        return 1
    for r in rows:
        mark = "*" if r["required"] else " "
        state = "已安装" if r["installed"] else "-"
        print(f" {mark} {r['id']:<16} {str(r['version']):<18} {r['size']/1024/1024:10.1f} MB  {state}")
    return 0


# ---------------------------------------------------------------------------
# 向导 GUI
# ---------------------------------------------------------------------------

def run_wizard() -> None:
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Möbius 首次运行向导")
    root.geometry("640x480")
    frame = ttk.Frame(root, padding=14)
    frame.pack(fill="both", expand=True)
    title_var = tk.StringVar(value="")
    title = ttk.Label(frame, textvariable=title_var, font=("Segoe UI", 14, "bold"))
    title.pack(anchor="w")
    body = ttk.Frame(frame)
    body.pack(fill="both", expand=True, pady=10)
    nav = ttk.Frame(frame)
    nav.pack(fill="x")
    log_var = tk.StringVar(value="")

    steps = []
    state = {"i": 0}

    f1 = ttk.Frame(body)
    ttk.Label(f1, text="运行时组件：安装缺失的必需组件").pack(anchor="w")
    comp_tree = ttk.Treeview(f1, columns=("ver", "state"), show="tree headings", height=9)
    comp_tree.heading("#0", text="组件")
    comp_tree.heading("ver", text="版本")
    comp_tree.heading("state", text="状态")
    comp_tree.pack(fill="both", expand=True, pady=6)

    def refresh_components() -> None:
        comp_tree.delete(*comp_tree.get_children())
        for r in components_state():
            comp_tree.insert("", "end", text=r["id"], values=(r["version"], "已安装" if r["installed"] else "-"))

    refresh_components()
    ttk.Label(f1, text="GitHub Token（私有仓库下载组件需要，公开仓库可留空）").pack(anchor="w", pady=(6, 0))
    e_token = ttk.Entry(f1, width=60, show="*")
    e_token.insert(0, str((_raw_config().get("launcher") or {}).get("github_token") or ""))
    e_token.pack(anchor="w", pady=2)
    steps.append(("1 / 4 运行时组件", f1))

    f2 = ttk.Frame(body)
    llm_cfg = _raw_config().get("llm") or {}
    e_base = ttk.Entry(f2, width=52)
    e_key = ttk.Entry(f2, width=52, show="*")
    e_model = ttk.Entry(f2, width=32)
    e_base.insert(0, str(llm_cfg.get("base_url") or "https://api.deepseek.com/v1"))
    e_key.insert(0, str(llm_cfg.get("api_key") or ""))
    e_model.insert(0, str(llm_cfg.get("model") or "deepseek-flash"))
    for label, entry in (("Base URL", e_base), ("API Key", e_key), ("Model", e_model)):
        ttk.Label(f2, text=label).pack(anchor="w")
        entry.pack(anchor="w", pady=2)
    llm_result = tk.StringVar(value="")
    ttk.Label(f2, textvariable=llm_result, foreground="#0a7").pack(anchor="w", pady=4)
    steps.append(("2 / 4 大模型配置", f2))

    f3 = ttk.Frame(body)
    model_dir = tk.StringVar(value=str(_raw_config().get("paths", {}).get("MODELS") or os.path.join(ROOT, "models")))
    ttk.Label(f3, text="模型根目录（可指向外接盘）").pack(anchor="w")
    ttk.Entry(f3, textvariable=model_dir, width=60).pack(anchor="w", pady=2)
    ttk.Button(f3, text="选择目录…", command=lambda: model_dir.set(filedialog.askdirectory() or model_dir.get())).pack(anchor="w")
    ttk.Label(f3, text="可从旧机器迁移（robocopy，支持跳过）。模型体积大，可稍后单独处理。").pack(anchor="w", pady=8)
    steps.append(("3 / 4 模型目录", f3))

    f4 = ttk.Frame(body)
    ttk.Label(f4, text="QQ 登录：启动 NapCat 并打开 WebUI 扫码；登录后可跳过。").pack(anchor="w")
    qq_result = tk.StringVar(value="")
    ttk.Label(f4, textvariable=qq_result).pack(anchor="w", pady=6)
    steps.append(("4 / 4 QQ 登录", f4))

    def show(i: int) -> None:
        for w in steps:
            w[1].pack_forget()
        steps[i][1].pack(fill="both", expand=True)
        title_var.set(steps[i][0])
        back_btn.config(state="normal" if i > 0 else "disabled")
        next_btn.config(text="完成并启动" if i == len(steps) - 1 else "下一步")
        state["i"] = i

    def next_step() -> None:
        i = state["i"]
        if i == 0:
            _apply_github_token(e_token.get(), persist=bool(e_token.get().strip()))
        if i == 1:
            ok, detail = configure_llm(e_base.get().strip(), e_key.get().strip(), e_model.get().strip())
            llm_result.set(("✓ " if ok else "✗ ") + detail[:80])
            if not ok and not messagebox.askyesno("LLM 连接失败", "连接测试失败，仍要继续吗？"):
                return
        if i < len(steps) - 1:
            show(i + 1)
        else:
            mark_first_run_done()
            root.destroy()

    def install_async() -> None:
        def job() -> None:
            _apply_github_token(e_token.get(), persist=True)
            log_var.set("正在安装组件…（详见 logs/launcher.log）")
            ok = install_components()
            root.after(0, lambda: (log_var.set("组件安装完成" if ok else "组件安装失败"), refresh_components()))
        threading.Thread(target=job, daemon=True).start()

    def qq_async() -> None:
        def job() -> None:
            ok = start_napcat_and_open()
            root.after(0, lambda: qq_result.set("NapCat 启动成功，请在 WebUI 扫码登录" if ok else "NapCat 启动失败，可稍后手动启动"))
        threading.Thread(target=job, daemon=True).start()

    def _migrate_dialog() -> None:
        src = filedialog.askdirectory(title="选择旧模型目录")
        if src:
            ok = migrate_models(src, model_dir.get())
            messagebox.showinfo("迁移", "完成" if ok else "迁移失败，见日志")

    ttk.Button(f1, text="安装必需组件", command=install_async).pack(anchor="w")
    ttk.Label(f1, textvariable=log_var, foreground="#666").pack(anchor="w")
    ttk.Button(f2, text="测试连接", command=lambda: llm_result.set("测试中…") or threading.Thread(
        target=lambda: llm_result.set(("%s %s" % (("✓" if test_llm()[0] else "✗"), test_llm()[1]))[:100]), daemon=True).start()).pack(anchor="w", pady=2)
    ttk.Button(f3, text="从旧目录迁移…", command=_migrate_dialog).pack(anchor="w")
    ttk.Button(f4, text="启动 NapCat 并打开 WebUI", command=qq_async).pack(anchor="w")

    back_btn = ttk.Button(nav, text="上一步", state="disabled", command=lambda: show(max(0, state["i"] - 1)))
    next_btn = ttk.Button(nav, text="下一步", command=next_step)
    ttk.Button(nav, text="退出", command=root.destroy).pack(side="right")
    next_btn.pack(side="right", padx=6)
    back_btn.pack(side="right")

    show(0)
    root.mainloop()

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
    parser.add_argument("--headless", choices=[
        "start", "stop", "status",
        "components", "install-components",
        "configure-llm", "test-llm", "migrate-models",
        "configure-github", "check-github",
    ], default=None)
    parser.add_argument("--wizard", action="store_true", help="强制进入首次运行向导")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--from-dir", default="")
    parser.add_argument("--to-dir", default="")
    parser.add_argument("--source-dir", default="")
    parser.add_argument("--github-token", default="")
    parser.add_argument("--no-elevate", action="store_true")
    parser.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.headless:
        _attach_console()

    if not single_instance():
        log("已有启动器实例在运行")
        return 0

    cfg = load_config()
    _apply_github_token()
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
    if args.headless == "components":
        return cmd_components()
    if args.headless == "install-components":
        return 0 if install_components(source_dir=args.source_dir) else 1
    if args.headless == "configure-llm":
        ok, detail = configure_llm(args.base_url, args.api_key, args.model)
        print(("✓ " if ok else "✗ ") + detail)
        return 0 if ok else 1
    if args.headless == "test-llm":
        ok, detail = test_llm()
        print(("✓ " if ok else "✗ ") + detail)
        return 0 if ok else 1
    if args.headless == "migrate-models":
        return 0 if migrate_models(args.from_dir, args.to_dir) else 1
    if args.headless == "configure-github":
        _apply_github_token(args.github_token, persist=True)
        ok, detail = check_github()
        print(("✓ " if ok else "✗ ") + detail)
        return 0 if ok else 1
    if args.headless == "check-github":
        ok, detail = check_github()
        print(("✓ " if ok else "✗ ") + detail)
        return 0 if ok else 1
    if is_first_run() or args.wizard:
        run_wizard()
        return 0
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
