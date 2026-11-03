"""琪亚娜数据集：从语音素材库音频按 whisper 句段切片，生成训练 list。

运行：runtime\\python.exe D:\\AI\\kiana_stepA_dataset.py
"""

from __future__ import annotations

import os
import re
import subprocess

ROOT = r"D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50"
SRC = r"D:\TTS_Workspace\kiana_audio\kiana_p1_44k.wav"
WORK = r"D:\AI\kiana_dataset"
SEG = os.path.join(WORK, "segments")
LIST = os.path.join(WORK, "kiana.list")
FFMPEG = os.path.join(ROOT, "ffmpeg.exe")

CJK = re.compile(r"[\u4e00-\u9fff]")
SPEAKER = "琪亚娜"


def main() -> int:
    os.makedirs(SEG, exist_ok=True)
    from faster_whisper import WhisperModel

    model = WhisperModel(r"D:\AI\models\faster-whisper-large-v3", device="cuda", compute_type="float16")
    segments, info = model.transcribe(SRC, language=None, vad_filter=True, beam_size=5)
    print(f"lang={info.language} p={info.language_probability:.2f}", flush=True)

    rows = []
    idx = 0
    for seg in segments:
        text = seg.text.strip()
        cjk = len(CJK.findall(text))
        if info.language != "zh" or cjk < 3 or len(text) < 5:
            continue
        dur = seg.end - seg.start
        if dur < 1.2 or dur > 10.0:
            continue
        out = os.path.join(SEG, f"kiana_{idx:04d}.wav")
        idx += 1
        subprocess.run(
            [FFMPEG, "-y", "-i", SRC, "-ss", f"{seg.start:.2f}", "-to", f"{seg.end:.2f}",
             "-ar", "32000", "-ac", "1", out],
            capture_output=True,
        )
        rows.append(f"{out}|{SPEAKER}|zh|{text}")

    with open(LIST, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + ("\n" if rows else ""))
    total = sum(os.path.getsize(r.split("|")[0]) / 64000 for r in rows)
    print(f"数据集: {len(rows)} 条，约 {total/60:.2f} 分钟 -> {LIST}", flush=True)
    for r in rows[:5]:
        print("  ", r.split("|")[-1][:50], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
