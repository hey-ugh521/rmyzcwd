"""八重樱数据集 Step B/C：切片 + FunASR 中文转写 + 生成训练 list。

运行：runtime\\python.exe D:\\AI\\yae_stepBC_slice_asr.py
"""

import os
import re
import sys

ROOT = r"D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50"
WORK = r"D:\AI\yae_dataset"
VOCAL = os.path.join(WORK, "vocals")
SLICES = os.path.join(WORK, "slices")
ASR_OUT = os.path.join(WORK, "asr")
LIST_FILE = os.path.join(WORK, "yae.list")

sys.path.insert(0, os.path.join(ROOT, "tools", "uvr5"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

CJK = re.compile(r"[\u4e00-\u9fff]")


def main() -> int:
    os.makedirs(SLICES, exist_ok=True)
    os.makedirs(ASR_OUT, exist_ok=True)

    # 1) 切片：3s~12s，按静音切（复用 GPT-SoVITS 的 slicer2，避免 slice_audio 导入即执行）
    import numpy as np
    from scipy.io import wavfile
    from slicer2 import Slicer
    from tools.my_utils import load_audio

    print("[1/3] 切片中 ...", flush=True)
    slicer = Slicer(
        sr=32000,
        threshold=-40,
        min_length=3000,
        min_interval=300,
        hop_size=10,
        max_sil_kept=500,
    )
    for name in sorted(os.listdir(VOCAL)):
        if not name.endswith(".wav"):
            continue
        audio = load_audio(os.path.join(VOCAL, name), 32000)
        for chunk, start, end in slicer.slice(audio):
            tmp_max = np.abs(chunk).max()
            if tmp_max > 1:
                chunk /= tmp_max
            chunk = (chunk / tmp_max * (12000 * 0.9)) + (1 - 0.9) * chunk
            wavfile.write(
                "%s/%s_%010d_%010d.wav" % (SLICES, name, start, end),
                32000,
                (chunk * 32767).astype(np.int16),
            )
    slices = sorted(f for f in os.listdir(SLICES) if f.endswith(".wav"))
    print(f"  切片数: {len(slices)}", flush=True)

    # 2) FunASR 中文转写
    from tools.asr.funasr_asr import execute_asr

    print("[2/3] ASR 转写中 ...", flush=True)
    execute_asr(SLICES, ASR_OUT, "large", "zh")

    # 3) 汇总并过滤
    lines = []
    txt_files = [f for f in os.listdir(ASR_OUT) if f.endswith(".list")]
    for tf in txt_files:
        with open(os.path.join(ASR_OUT, tf), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.count("|") < 3:
                    continue
                parts = line.split("|")
                path, name, lang, text = parts[0], parts[1], parts[2], "|".join(parts[3:])
                text = text.strip()
                cjk = len(CJK.findall(text))
                if len(text) < 4 or cjk < max(2, int(len(text) * 0.5)):
                    continue
                if not os.path.exists(path):
                    continue
                lines.append(f"{path}|八重樱|zh|{text}")

    # 去重（同切片可能重复出现）
    seen = set()
    uniq = []
    for ln in lines:
        p = ln.split("|")[0]
        if p in seen:
            continue
        seen.add(p)
        uniq.append(ln)

    with open(LIST_FILE, "w", encoding="utf-8") as fh:
        fh.write("\n".join(uniq) + ("\n" if uniq else ""))

    total = 0.0
    for ln in uniq:
        p = ln.split("|")[0]
        total += float(os.path.getsize(p)) / 64000 / 2  # 粗略：32k 16bit mono
    print(f"[3/3] 训练 list: {len(uniq)} 条，约 {total/60:.1f} 分钟 -> {LIST_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
