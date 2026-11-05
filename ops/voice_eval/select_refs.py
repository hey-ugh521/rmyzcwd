"""S2-1：选取 4 种情绪的参考音（GPT-SoVITS 要求参考音 3–10 秒）。"""

from __future__ import annotations

import csv
import json
import os

import soundfile as sf

LABELS = r"D:\AI\voice_eval\emotion_labels.csv"
LIST = r"D:\AI\teresa_dataset\teresa.list"
OUT = r"D:\AI\voice_eval\refs.json"

MAP = {"neutral": "中立/neutral", "happy": "开心/happy", "angry": "生气/angry", "sad": "难过/sad"}


def main() -> int:
    texts = {}
    for line in open(LIST, encoding="utf-8"):
        parts = line.strip().split("|")
        if len(parts) >= 4:
            texts[os.path.basename(parts[0])] = parts[3]

    rows = list(csv.DictReader(open(LABELS, encoding="utf-8")))
    refs = {}
    for key, label in MAP.items():
        cands = [r for r in rows if r["emotion"] == label]
        cands.sort(key=lambda r: -float(r["score"]))
        chosen = None
        for r in cands:
            path = os.path.join(r"D:\AI\teresa_dataset\segments", r["file"])
            try:
                info = sf.info(path)
            except Exception:  # noqa: BLE001
                continue
            dur = info.frames / info.samplerate
            text = texts.get(r["file"], "")
            # 文本与音频必须匹配：字数不能太少（否则 GPT 容易提前停止）
            rate = len(text) / dur if dur > 0 else 0
            if 3.0 <= dur <= 6.0 and len(text) >= 8 and 2.5 <= rate <= 7.0:
                chosen = (r, path, dur)
                break
        if chosen is None:
            raise SystemExit(f"{key}: 没有 3–6 秒的候选片段")
        r, path, dur = chosen
        refs[key] = {
            "file": r["file"],
            "path": path,
            "prompt_text": texts[r["file"]],
            "emotion": key,
            "emotion_label": r["emotion"],
            "score": r["score"],
            "duration": round(dur, 2),
        }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(refs, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(refs, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
