"""S2-2：用 GPT-SoVITS v4（fp32）生成 4 情绪 × 10 文本矩阵。"""

from __future__ import annotations

import json
import os
import time
import urllib.request

API = "http://127.0.0.1:7873/tts"
REFS = r"D:\AI\voice_eval\refs.json"
TEXTS = r"D:\AI\voice_eval\test_texts.json"
OUT = r"D:\AI\voice_eval\gen\gsv"


def main() -> int:
    refs = json.load(open(REFS, encoding="utf-8"))
    texts = json.load(open(TEXTS, encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)
    total = 0
    for emo, ref in refs.items():
        d = os.path.join(OUT, emo)
        os.makedirs(d, exist_ok=True)
        for t in texts:
            dst = os.path.join(d, f"{t['id']}.wav")
            if os.path.exists(dst) and os.path.getsize(dst) > 10000:
                total += 1
                continue
            body = json.dumps(
                {
                    "text": t["text"],
                    "text_lang": "zh",
                    "ref_audio_path": ref["path"],
                    "prompt_text": ref["prompt_text"],
                    "prompt_lang": "zh",
                    "top_k": 5,
                    "top_p": 1,
                    "temperature": 1.0,
                    "text_split_method": "cut0",
                    "batch_size": 1,
                    "speed_factor": 1.0,
                    "split_bucket": False,
                    "streaming_mode": False,
                    "media_type": "wav",
                    "repetition_penalty": 1.35,
                }
            ).encode("utf-8")
            req = urllib.request.Request(API, data=body, headers={"Content-Type": "application/json"})
            try:
                t0 = time.time()
                with urllib.request.urlopen(req, timeout=180) as resp:
                    audio = resp.read()
                open(dst, "wb").write(audio)
                print(f"  {emo}/{t['id']} ok {len(audio)/1024:.0f}KB {time.time()-t0:.1f}s", flush=True)
                total += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  {emo}/{t['id']} 失败: {type(exc).__name__}: {exc}", flush=True)
    print(" 完成:", total, "->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
