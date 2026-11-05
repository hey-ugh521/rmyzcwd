"""语音评测 S1：给德丽莎数据集自动打情绪标签（emotion2vec），统计分布。

运行：D:\\AI\\GPT-SoVITS-v4\\GPT-SoVITS-v4-20250529-nvidia50\\runtime\\python.exe D:\\AI\\voice_eval\\label_emotions.py
"""

from __future__ import annotations

import csv
import json
import os

DATA = r"D:\AI\teresa_dataset\segments"
LIST = r"D:\AI\teresa_dataset\teresa.list"
OUT = r"D:\AI\voice_eval\emotion_labels.csv"


def main() -> int:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    texts = {}
    for line in open(LIST, encoding="utf-8"):
        parts = line.strip().split("|")
        if len(parts) >= 4:
            texts[os.path.basename(parts[0])] = parts[3]

    from funasr import AutoModel

    model = AutoModel(model=r"D:\AI\models\modelscope\iic\emotion2vec_plus_large", device="cuda", disable_update=True)

    rows = []
    for name in sorted(os.listdir(DATA)):
        if not name.endswith(".wav"):
            continue
        path = os.path.join(DATA, name)
        try:
            res = model.generate(input=path, granularity="utterance", extract_embedding=False)
            item = res[0]
            labels = item.get("labels") or []
            scores = item.get("scores") or []
            top = labels[int(max(range(len(scores)), key=lambda i: scores[i]))] if labels and scores else "unknown"
            score = max(scores) if scores else 0.0
            rows.append({"file": name, "text": texts.get(name, ""), "emotion": top, "score": round(float(score), 4)})
        except Exception as exc:  # noqa: BLE001
            rows.append({"file": name, "text": texts.get(name, ""), "emotion": "error", "score": 0.0})
            print("  失败", name, type(exc).__name__)

    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["file", "text", "emotion", "score"])
        w.writeheader()
        w.writerows(rows)

    dist = {}
    for r in rows:
        dist[r["emotion"]] = dist.get(r["emotion"], 0) + 1
    print(" 标注完成:", len(rows), "->", OUT)
    print(" 情绪分布:", json.dumps(dist, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
