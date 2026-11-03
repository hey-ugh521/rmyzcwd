"""八重樱数据集 Step G：CAM++ 说话人过滤，生成干净训练集。

运行：runtime\\python.exe D:\\AI\\yae_stepG_filter.py
"""

from __future__ import annotations

import os

import numpy as np

WORK = r"D:\AI\yae_dataset"
LIST = os.path.join(WORK, "yae.list")
OUT = os.path.join(WORK, "yae_clean.list")
REF = os.path.join(WORK, "segments", "vocal_p3_44k.wav_10_002.wav")
THRESHOLD = 0.50


def main() -> int:
    from funasr import AutoModel

    os.environ.setdefault("MODELSCOPE_CACHE", r"D:\AI\models\modelscope")
    model = AutoModel(model="iic/speech_campplus_sv_zh-cn_16k-common", device="cuda", disable_update=True)

    def emb(path: str) -> np.ndarray:
        r = model.generate(input=path, return_raw=True)
        e = r[0]["spk_embedding"]
        try:
            e = e.cpu().numpy()
        except Exception:
            e = np.asarray(e)
        e = np.asarray(e, dtype=np.float32).reshape(-1)
        return e / (np.linalg.norm(e) or 1.0)

    ref = emb(REF)
    kept, dropped = [], []
    for line in open(LIST, encoding="utf-8"):
        line = line.strip()
        if not line or line.count("|") < 3:
            continue
        path = line.split("|")[0]
        if not os.path.exists(path):
            continue
        try:
            score = float(ref @ emb(path))
        except Exception:
            dropped.append((line, -1.0))
            continue
        if score >= THRESHOLD:
            kept.append(line)
        else:
            dropped.append((line, score))

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(kept) + ("\n" if kept else ""))

    total = sum(os.path.getsize(l.split("|")[0]) / 64000 for l in kept)
    print(f"保留 {len(kept)} 条（约 {total/60:.2f} 分钟），丢弃 {len(dropped)} 条", flush=True)
    print("\n丢弃示例（分数最低的 10 条）:", flush=True)
    for line, score in sorted(dropped, key=lambda x: x[1])[:10]:
        print(f"  {score:+.3f}  {line.split('|')[-1][:50]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
