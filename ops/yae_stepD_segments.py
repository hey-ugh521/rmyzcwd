"""八重樱数据集 Step D：按 whisper 句段时间戳切句 + 生成训练 list。

运行：runtime\\python.exe D:\\AI\\yae_stepD_segments.py
"""

import os
import re
import subprocess

ROOT = r"D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50"
WORK = r"D:\AI\yae_dataset"
VOCAL = os.path.join(WORK, "vocals")
OUT = os.path.join(WORK, "segments")
LIST_FILE = os.path.join(WORK, "yae.list")
FFMPEG = os.path.join(ROOT, "ffmpeg.exe")

CJK = re.compile(r"[\u4e00-\u9fff]")
SPEAKER = "八重樱"


def main() -> int:
    from faster_whisper import WhisperModel

    os.makedirs(OUT, exist_ok=True)
    model = WhisperModel(r"D:\AI\models\faster-whisper-large-v3", device="cuda", compute_type="float16")

    rows = []
    for name in sorted(os.listdir(VOCAL)):
        if not name.endswith(".wav"):
            continue
        path = os.path.join(VOCAL, name)
        segments, info = model.transcribe(
            path, language=None, vad_filter=True, beam_size=5
        )
        print(f"\n=== {name} lang={info.language} p={info.language_probability:.2f}", flush=True)
        idx = 0
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            cjk = len(CJK.findall(text))
            if info.language != "zh" and cjk < max(2, int(len(text) * 0.5)):
                continue
            if len(text) < 4:
                continue
            dur = seg.end - seg.start
            if dur < 1.0 or dur > 15.0:
                continue
            out = os.path.join(OUT, f"{os.path.splitext(name)[0]}_{idx:03d}.wav")
            idx += 1
            subprocess.run(
                [
                    FFMPEG, "-y", "-i", path,
                    "-ss", f"{seg.start:.2f}", "-to", f"{seg.end:.2f}",
                    "-ar", "32000", "-ac", "1", out,
                ],
                capture_output=True,
            )
            rows.append(f"{out}|{SPEAKER}|zh|{text}")
            print(f"  [{seg.start:6.1f}-{seg.end:6.1f}] {text[:60]}", flush=True)

    with open(LIST_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(rows) + ("\n" if rows else ""))
    total = sum(os.path.getsize(r.split("|")[0]) / 64000 for r in rows)
    print(f"\n数据集: {len(rows)} 条，约 {total/60:.2f} 分钟 -> {LIST_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
