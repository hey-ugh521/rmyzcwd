"""德丽莎数据集 44.1k 重建 + 参考音去混响（UVR5 DeEcho）。

运行：runtime\\python.exe D:\\AI\\teresa44_stepA_dataset.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ROOT = r"D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50"
SRC = r"D:\AI\teresa_dataset\p04_44k_mono.wav"
WORK = r"D:\AI\teresa44_dataset"
SEG = os.path.join(WORK, "segments")
LIST = os.path.join(WORK, "teresa44.list")
REF_OUT = r"D:\AI\teresa44_dataset\ref44_0140_dereverb.wav"
FFMPEG = os.path.join(ROOT, "ffmpeg.exe")
REF_SRC = r"D:\AI\teresa_dataset\segments\ref44_0140.wav"
CJK = re.compile(r"[\u4e00-\u9fff]")
SPEAKER = "德丽莎"

sys.path.insert(0, os.path.join(ROOT, "tools", "uvr5"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)


def main() -> int:
    for d in (SEG, WORK):
        os.makedirs(d, exist_ok=True)

    # 1) 44.1k 切句
    from faster_whisper import WhisperModel

    model = WhisperModel(r"D:\AI\models\faster-whisper-large-v3", device="cuda", compute_type="float16")
    segments, info = model.transcribe(SRC, language=None, vad_filter=True, beam_size=5)
    rows, idx = [], 0
    for seg in segments:
        text = seg.text.strip()
        dur = seg.end - seg.start
        if info.language != "zh" or len(CJK.findall(text)) < 3 or len(text) < 5:
            continue
        if dur < 2.0 or dur > 10.0:
            continue
        out = os.path.join(SEG, f"teresa44_{idx:04d}.wav")
        idx += 1
        subprocess.run([FFMPEG, "-y", "-i", SRC, "-ss", f"{seg.start:.2f}", "-to", f"{seg.end:.2f}",
                        "-ar", "44100", "-ac", "1", out], capture_output=True)
        rows.append(f"{out}|{SPEAKER}|zh|{text}")
    with open(LIST, "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")
    total = sum(os.path.getsize(r.split("|")[0]) / 88200 for r in rows)
    print(f"数据集(44.1k): {len(rows)} 条，约 {total/60:.2f} 分钟", flush=True)

    # 2) 参考音去混响（UVR5 DeEcho）
    from vr import AudioPreDeEcho

    pre = AudioPreDeEcho(
        agg=10,
        model_path=os.path.join(ROOT, "tools", "uvr5", "uvr5_weights", "VR-DeEchoNormal.pth"),
        device="cuda",
        is_half=True,
    )
    ins_dir = os.path.join(WORK, "ref_ins")
    os.makedirs(ins_dir, exist_ok=True)
    pre._path_audio_(REF_SRC, vocal_root=WORK, ins_root=ins_dir, format="wav", is_hp3=False)
    # 输出文件名通常为 vocal_<原名>.wav
    for f in os.listdir(WORK):
        if f.startswith("vocal_") and f.endswith(".wav"):
            os.replace(os.path.join(WORK, f), REF_OUT)
            break
    print("参考音去混响完成:", REF_OUT, os.path.exists(REF_OUT), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
