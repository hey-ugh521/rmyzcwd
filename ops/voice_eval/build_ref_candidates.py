"""从本地数据集筛选"参考音候选库"（社区参考包下不动时的替代方案）。

筛选：3–6 秒、文本≥8 字、语速 2.5–7 字/秒、emotion2vec 置信度≥0.95、F0 有效帧占比高。
输出：D:\\AI\\voice_eval\\ref_candidates\\<emotion>\\ + candidates.csv
"""

from __future__ import annotations

import csv
import os
import shutil

import librosa
import numpy as np
import soundfile as sf

LABELS = r"D:\AI\voice_eval\emotion_labels.csv"
LIST = r"D:\AI\teresa_dataset\teresa.list"
SEG = r"D:\AI\teresa_dataset\segments"
OUT = r"D:\AI\voice_eval\ref_candidates"

MAP = {"neutral": "中立/neutral", "happy": "开心/happy", "angry": "生气/angry", "sad": "难过/sad"}


def quality(path: str) -> tuple[float, float]:
    y, sr = librosa.load(path, sr=16000, mono=True)
    if len(y) == 0:
        return 0.0, 0.0
    f0, _, _ = librosa.pyin(y.astype(np.float32), fmin=70, fmax=500, sr=sr)
    voiced = float(np.mean(~np.isnan(f0)))
    rms = float(np.mean(librosa.feature.rms(y=y)))
    return voiced, rms


def main() -> int:
    texts = {}
    for line in open(LIST, encoding="utf-8"):
        p = line.strip().split("|")
        if len(p) >= 4:
            texts[os.path.basename(p[0])] = p[3]
    rows = list(csv.DictReader(open(LABELS, encoding="utf-8")))
    out_rows = []
    for key, label in MAP.items():
        cands = []
        for r in rows:
            if r["emotion"] != label:
                continue
            try:
                score = float(r["score"])
            except ValueError:
                continue
            if score < 0.95:
                continue
            path = os.path.join(SEG, r["file"])
            text = texts.get(r["file"], "")
            info = sf.info(path)
            dur = info.frames / info.samplerate
            if not (3.0 <= dur <= 6.0) or len(text) < 8:
                continue
            rate = len(text) / dur
            if not (2.5 <= rate <= 7.0):
                continue
            voiced, rms = quality(path)
            if voiced < 0.55 or rms < 0.005:
                continue
            cands.append({"file": r["file"], "text": text, "dur": round(dur, 2), "score": score,
                          "rate": round(rate, 2), "voiced": round(voiced, 2), "rms": round(rms, 4)})
        cands.sort(key=lambda x: (-x["score"], abs(x["dur"] - 4.0)))
        for i, c in enumerate(cands[:5]):
            d = os.path.join(OUT, key)
            os.makedirs(d, exist_ok=True)
            dst = os.path.join(d, f"{i+1:02d}_{c['file']}")
            if not os.path.exists(dst):
                shutil.copyfile(os.path.join(SEG, c["file"]), dst)
            out_rows.append({"emotion": key, "rank": i + 1, **c, "dst": dst})
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "candidates.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["emotion", "rank", "file", "text", "dur", "score", "rate", "voiced", "rms", "dst"])
        w.writeheader()
        w.writerows(out_rows)
    for key in MAP:
        n = sum(1 for r in out_rows if r["emotion"] == key)
        print(f"  {key}: {n} 个候选")
    print(" 输出:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
