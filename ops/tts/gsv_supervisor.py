"""八重樱 TTS 常驻监管：保证 GPT-SoVITS 原生 API(7873) 与 OpenAI 适配层(7871) 始终在线。

由 Core 作为服务启动（开机预热）或 watchdog 兜底拉起；进程本身长期驻留。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = r"D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50"
PY = os.path.join(ROOT, "runtime", "python.exe")
ADAPTER = r"D:\AI\gsv_openai_adapter.py"
LOG = r"D:\AI\gsv_supervisor.log"


def log(msg: str) -> None:
    line = time.strftime("%H:%M:%S") + " " + msg
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.5):
            return True
    except Exception:
        return False


def _get(url: str, timeout: float = 120) -> None:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        r.read()


def ensure_native() -> None:
    if port_open(7873):
        return
    log("启动 GPT-SoVITS 原生 API (7873)")
    subprocess.Popen(
        [PY, "api_v2.py", "-a", "127.0.0.1", "-p", "7873",
         "-c", "GPT_SoVITS/configs/tts_infer_v4_fp32.yaml"],
        cwd=ROOT,
        stdout=open(os.path.join(ROOT, "api_native.log"), "a", encoding="utf-8"),
        stderr=open(os.path.join(ROOT, "api_native.err.log"), "a", encoding="utf-8"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(90):
        time.sleep(2)
        if port_open(7873):
            break
    if port_open(7873):
        try:
            _get("http://127.0.0.1:7873/set_gpt_weights?weights_path=D:\\AI\\community_models\\Rosmontis\\GPT_weights\\Rosmontis-e10.ckpt", 180)
            _get("http://127.0.0.1:7873/set_sovits_weights?weights_path=D:\\AI\\community_models\\Rosmontis\\SoVITS_weights\\Rosmontis_e200_s5600.pth", 180)
            log("微调权重加载完成")
        except Exception as exc:  # noqa: BLE001
            log(f"权重加载失败: {exc}")


def ensure_adapter() -> None:
    if port_open(7871):
        return
    log("启动 OpenAI 适配层 (7871)")
    subprocess.Popen(
        [PY, ADAPTER],
        stdout=open(os.path.join(ROOT, "adapter.log"), "a", encoding="utf-8"),
        stderr=open(os.path.join(ROOT, "adapter.err.log"), "a", encoding="utf-8"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    for _ in range(40):
        time.sleep(2)
        if port_open(7871):
            break


def main() -> int:
    log("GSV 监管启动")
    while True:
        try:
            ensure_native()
            ensure_adapter()
        except Exception as exc:  # noqa: BLE001
            log(f"监管循环异常: {exc}")
        time.sleep(20)


if __name__ == "__main__":
    raise SystemExit(main())
