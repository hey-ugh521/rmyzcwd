"""八重樱数据集扩充：处理 yae_audio/extra 新下载素材，追加到训练集。

运行：runtime\\python.exe D:\\AI\\yae_stepF_extra.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = r"D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50"
SRC = r"D:\TTS_Workspace\yae_audio\extra"
WORK = r"D:\AI\yae_dataset"
INP = os.path.join(WORK, "extra_input")
VOCAL = os.path.join(WORK, "extra_vocals")
INS = os.path.join(WORK, "extra_ins")
SEG = os.path.join(WORK, "segments_extra")
LIST = os.path.join(WORK, "yae.list")
FFMPEG = os.path.join(ROOT, "ffmpeg.exe")

CJK = re.compile(r"[\u4e00-\u9fff]")

sys.path.insert(0, os.path.join(ROOT, "tools", "uvr5"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def main() -> int:
    for d in (INP, VOCAL, INS, SEG):
        os.makedirs(d, exist_ok=True)

    # 1) 转 44.1k 双声道
    wavs = []
    for name in sorted(os.listdir(SRC)):
        if not name.lower().endswith((".m4a", ".wav", ".mp3")):
            continue
        out = os.path.join(INP, os.path.splitext(name)[0] + "_44k.wav")
        if not os.path.exists(out):
            subprocess.run(
                [FFMPEG, "-y", "-i", os.path.join(SRC, name), "-ar", "44100", "-ac", "2", out],
                capture_output=True, timeout=900,
            )
        wavs.append(out)
    print(f"输入: {len(wavs)} 个", flush=True)

    # 2) UVR5 人声分离
    from vr import AudioPre

    pre = AudioPre(
        agg=10,
        model_path=os.path.join(ROOT, "tools", "uvr5", "uvr5_weights", "HP2_all_vocals.pth"),
        device="cuda",
        is_half=True,
    )
    for i, w in enumerate(wavs, 1):
        base = os.path.basename(w)
        out = os.path.join(VOCAL, "vocal_" + base)
        if os.path.exists(out):
            continue
        print(f"[{i}/{len(wavs)}] 分离 {base}", flush=True)
        pre._path_audio_(w, ins_root=INS, vocal_root=VOCAL, format="wav", is_hp3=False)

    # 3) whisper 切句（只留中文）
    from faster_whisper import WhisperModel

    model = WhisperModel(r"D:\AI\models\faster-whisper-large-v3", device="cuda", compute_type="float16")
    existing = set()
    if os.path.exists(LIST):
        for ln in open(LIST, encoding="utf-8"):
            existing.add(ln.split("|")[0])

    new_rows = []
    for name in sorted(os.listdir(VOCAL)):
        if not name.endswith(".wav"):
            continue
        path = os.path.join(VOCAL, name)
        segments, info = model.transcribe(path, language=None, vad_filter=True, beam_size=5)
        print(f"\n=== {name} lang={info.language} p={info.language_probability:.2f}", flush=True)
        idx = 0
        for seg in segments:
            text = seg.text.strip()
            cjk = len(CJK.findall(text))
            if info.language != "zh" or cjk < 3 or len(text) < 5:
                continue
            dur = seg.end - seg.start
            if dur < 1.2 or dur > 15.0:
                continue
            out = os.path.join(SEG, f"x_{os.path.splitext(name)[0]}_{idx:03d}.wav")
            idx += 1
            subprocess.run(
                [FFMPEG, "-y", "-i", path, "-ss", f"{seg.start:.2f}", "-to", f"{seg.end:.2f}",
                 "-ar", "32000", "-ac", "1", out],
                capture_output=True,
            )
            new_rows.append(f"{out}|八重樱|zh|{text}")
            print(f"  [{seg.start:6.1f}-{seg.end:6.1f}] {text[:60]}", flush=True)

    # 4) 追加 list
    if new_rows:
        with open(LIST, "a", encoding="utf-8") as fh:
            fh.write("\n".join(new_rows) + "\n")
    total = sum(os.path.getsize(r.split("|")[0]) / 64000 for r in new_rows)
    old = len(existing)
    print(f"\n新增 {len(new_rows)} 条（约 {total/60:.2f} 分钟），原 {old} 条", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
