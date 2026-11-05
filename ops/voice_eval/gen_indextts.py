"""S2-3：用 IndexTTS-2.5 生成 4 情绪 × 10 文本矩阵（情绪向量驱动）。

运行：D:\\AI\\IndexTTS-bundle\\index-tts\\.venv\\Scripts\\python.exe ops\\voice_eval\\gen_indextts.py
"""

from __future__ import annotations

import json
import os
import sys

BASE = r"D:\AI\IndexTTS-bundle"
ROOT = os.path.join(BASE, "index-tts")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

CKPT = os.path.join(BASE, "checkpoints")
OUT = r"D:\AI\voice_eval\gen\indextts"
REF = r"D:\AI\teresa_dataset\segments\ref44_0140.wav"
TEXTS = r"D:\AI\voice_eval\test_texts.json"

# [happy, angry, sad, afraid, disgusted, melancholic, surprised, calm]
EMO_VEC = {
    "neutral": None,
    "happy": [1.0, 0, 0, 0, 0, 0, 0, 0],
    "angry": [0, 1.0, 0, 0, 0, 0, 0, 0],
    "sad": [0, 0, 1.0, 0, 0, 0, 0, 0],
}


def main() -> int:
    texts = json.load(open(TEXTS, encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)
    print("加载 IndexTTS...", flush=True)
    tts = IndexTTS2(
        cfg_path=os.path.join(CKPT, "config.yaml"),
        model_dir=CKPT,
        use_bf16=True,
        use_qwen_emo=False,
    )
    print("加载完成", flush=True)
    for emo, vec in EMO_VEC.items():
        d = os.path.join(OUT, emo)
        os.makedirs(d, exist_ok=True)
        for t in texts:
            dst = os.path.join(d, f"{t['id']}.wav")
            if os.path.exists(dst) and os.path.getsize(dst) > 10000:
                continue
            kw = {} if vec is None else {"emo_vector": vec}
            try:
                tts.infer(
                    spk_audio_prompt=REF,
                    text=t["text"],
                    lang="ZH",
                    output_path=dst,
                    verbose=False,
                    **kw,
                )
                print(f"  {emo}/{t['id']} ok", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  {emo}/{t['id']} 失败: {type(exc).__name__}: {exc}", flush=True)
    print(" 完成 ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
