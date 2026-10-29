#!/usr/bin/env python3
"""对真实 Möbius Core（8900）做一次 Pipe 端到端探测。

会真实调用 /v1/chat（会消耗一次 LLM 请求，但只发纯文本、不触发媒体工具）。
用法：
    python mobius/core/console/_mock/probe_core_pipe.py
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOBIUS_DIR = HERE.parents[2]
PIPE_PATH = MOBIUS_DIR / "openwebui" / "mobius_pipe.py"


def load_pipe():
    spec = importlib.util.spec_from_file_location("mobius_pipe", PIPE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["mobius_pipe"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    module = load_pipe()
    pipe = module.Pipe()
    pipe.valves.CORE_URL = "http://127.0.0.1:8900"
    pipe.valves.DEFAULT_CHARACTER = "bronya"

    body = {
        "model": "mobius",
        "stream": True,
        "messages": [
            {"role": "user", "content": "用一句话介绍你自己。不要调用任何工具，不要生成图片或语音。"}
        ],
        "metadata": {"chat_id": "pipe-probe-" + str(int(time.time()))},
    }

    chunks = []
    started = time.time()
    print("POST http://127.0.0.1:8900/v1/chat (SSE) ...", flush=True)
    try:
        for chunk in pipe.pipe(body, __user__={"id": "probe-user"}):
            chunks.append(chunk)
            print("[chunk %02d %5.2fs] %r" % (len(chunks), time.time() - started, chunk[:120]), flush=True)
            if time.time() - started > 120:
                print("超过 120s，主动停止探测", flush=True)
                break
    except Exception as exc:  # noqa: BLE001
        print("FAIL  Pipe 抛异常:", type(exc).__name__, exc)
        return 1

    text = "".join(chunks)
    print("\n总耗时: %.2fs" % (time.time() - started))
    print("chunk 数:", len(chunks))
    print("正文预览:", text[:500].replace("\n", "\\n"))
    ok = bool(text) and not text.lstrip().startswith("⚠️")
    print("结果:", "PASS 真实 Core SSE 端到端可用" if ok else "WARN Core 返回了错误提示（属 Pipe 已正确降级）")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
