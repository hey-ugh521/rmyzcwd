"""评估各角色语音素材的"可用长句"时长（zh，2–10 秒）。

运行：runtime\\python.exe D:\\AI\\char_survey.py
"""

from __future__ import annotations

import os
import re

import soundfile as sf

ROOT = r"D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50"
SRC = r"D:\TTS_Workspace\char_audio"
OUT = r"D:\AI\char_survey"
FFMPEG = os.path.join(ROOT, "ffmpeg.exe")
CJK = re.compile(r"[\u4e00-\u9fff]")


def main() -> int:
    import subprocess

    os.makedirs(OUT, exist_ok=True)
    from faster_whisper import WhisperModel

    model = WhisperModel(r"D:\AI\models\faster-whisper-large-v3", device="cuda", compute_type="float16")

    results = []
    for name in sorted(os.listdir(SRC)):
        if not name.endswith(".m4a"):
            continue
        wav = os.path.join(OUT, os.path.splitext(name)[0] + ".wav")
        if not os.path.exists(wav):
            subprocess.run([FFMPEG, "-y", "-i", os.path.join(SRC, name), "-ar", "32000", "-ac", "1", wav],
                           capture_output=True, timeout=900)
        d, sr = sf.read(wav)
        total = len(d) / sr
        segments, info = model.transcribe(wav, language=None, vad_filter=True, beam_size=1)
        usable, usable_min2, n = 0.0, 0.0, 0
        for seg in segments:
            text = seg.text.strip()
            dur = seg.end - seg.start
            if info.language == "zh" and len(CJK.findall(text)) >= 3 and 1.2 <= dur <= 10:
                usable += dur
                n += 1
                if dur >= 2.0:
                    usable_min2 += dur
        results.append((name, total, usable, usable_min2, n, info.language))
        print(f"  {name[:20]}: 总 {total/60:.1f}min | 可用 {usable/60:.2f}min ({n} 句) | ≥2s {usable_min2/60:.2f}min", flush=True)

    print("\n=== 排名（按 ≥2s 可用时长）===", flush=True)
    for r in sorted(results, key=lambda x: -x[3]):
        print(f"  {r[0][:18]}: ≥2s {r[3]/60:.2f}min | 可用 {r[2]/60:.2f}min | 总 {r[1]/60:.1f}min | {r[4]} 句", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
