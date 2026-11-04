"""IndexTTS-2.5 零样本 + 情绪控制测试（德丽莎参考音）。

运行：D:\\AI\\IndexTTS-bundle\\index-tts\\.venv\\Scripts\\python.exe D:\\AI\\indextts_test.py
"""

from __future__ import annotations

import os
import sys

BASE = r"D:\AI\IndexTTS-bundle"
ROOT = os.path.join(BASE, "index-tts")
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

CKPT = os.path.join(BASE, "checkpoints")
OUT = r"D:\AI\indextts_out"
REF = r"D:\AI\teresa44_dataset\ref44_0140_dereverb.wav"
EMO_AUDIO = r"D:\AI\teresa_dataset\segments\teresa_0145.wav"  # 高起伏参考做情绪
TEXT = "舰长，今天也要好好工作哦。"


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    print("加载模型...", flush=True)
    tts = IndexTTS2(
        cfg_path=os.path.join(CKPT, "config.yaml"),
        model_dir=CKPT,
        use_bf16=True,
        use_qwen_emo=False,
    )
    print("模型加载完成", flush=True)

    jobs = [
        ("base", {}),
        
        ("emo_happy", {"emo_vector": [0.7, 0, 0, 0, 0, 0, 0, 0]}),
        ("emo_audio", {"emo_audio_prompt": EMO_AUDIO, "emo_alpha": 0.8}),
    ]
    for name, kw in jobs:
        out = os.path.join(OUT, f"{name}.wav")
        try:
            tts.infer(
                spk_audio_prompt=REF,
                text=TEXT,
                lang="ZH",
                output_path=out,
                verbose=False,
                **kw,
            )
            print(f"  {name}: OK", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  {name}: 失败 {type(exc).__name__}: {exc}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
