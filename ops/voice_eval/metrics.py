"""S2-4：评测指标（说话人相似度 / F0 / 情绪准确率 / 时长）。

运行：D:\\AI\\GPT-SoVITS-v4\\...\\runtime\\python.exe ops\\voice_eval\\metrics.py
"""

from __future__ import annotations

import csv
import json
import os

import librosa
import numpy as np

REF_DIR = r"D:\AI\teresa_dataset\segments"
REFS = r"D:\AI\voice_eval\refs.json"
GOLD_EXTRA = [r"D:\AI\teresa_dataset\segments\ref44_0140.wav"]
GEN_ROOT = r"D:\AI\voice_eval\gen"
OUT_CSV = r"D:\AI\voice_eval\metrics.csv"
OUT_MD = r"D:\AI\voice_eval\report.md"

EMO2VEC_DIR = r"D:\AI\models\modelscope\iic\emotion2vec_plus_large"
CAM_DIR = r"D:\AI\models\modelscope\iic\speech_campplus_sv_zh-cn_16k-common"

TARGET_LABEL = {
    "neutral": "中立/neutral",
    "happy": "开心/happy",
    "angry": "生气/angry",
    "sad": "难过/sad",
}


def cos(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(np.dot(a, b) / (na * nb)) if na and nb else 0.0


def speaker_embed(model, path: str) -> np.ndarray:
    res = model.generate(input=path, granularity="utterance", extract_embedding=True)
    emb = res[0].get("spk_embedding")
    if emb is None:
        emb = res[0].get("embedding")
    return np.asarray(emb).reshape(-1)


def f0_stats(path: str) -> tuple[float, float, float]:
    y, sr = librosa.load(path, sr=None, mono=True)
    f0, _, _ = librosa.pyin(y.astype(np.float32), fmin=70, fmax=500, sr=sr)
    f0 = f0[~np.isnan(f0)]
    if len(f0) < 10:
        return 0.0, 0.0, len(y) / sr
    st = 12 * np.log2(f0 / np.median(f0))
    return float(np.std(st)), float(np.percentile(st, 95) - np.percentile(st, 5)), len(y) / sr


def emotion_top(model, path: str) -> tuple[str, float]:
    res = model.generate(input=path, granularity="utterance", extract_embedding=False)
    item = res[0]
    labels = item.get("labels") or []
    scores = item.get("scores") or []
    if not labels or not scores:
        return "unknown", 0.0
    idx = int(np.argmax(scores))
    return labels[idx], float(scores[idx])


def main() -> int:
    from funasr import AutoModel

    print("加载 CAM++ / emotion2vec ...", flush=True)
    cam = AutoModel(model=CAM_DIR, device="cuda", disable_update=True)
    emo = AutoModel(model=EMO2VEC_DIR, device="cuda", disable_update=True)
    refs = json.load(open(REFS, encoding="utf-8"))
    gold_paths = [r["path"] for r in refs.values()] + GOLD_EXTRA
    gold = np.mean([speaker_embed(cam, p) for p in gold_paths], axis=0)
    print("黄金参考嵌入就绪", flush=True)

    rows = []
    for engine in ("gsv", "indextts"):
        base = os.path.join(GEN_ROOT, engine)
        if not os.path.isdir(base):
            continue
        for emo_name in TARGET_LABEL:
            d = os.path.join(base, emo_name)
            if not os.path.isdir(d):
                continue
            for fname in sorted(os.listdir(d)):
                if not fname.endswith(".wav"):
                    continue
                path = os.path.join(d, fname)
                try:
                    sim = cos(speaker_embed(cam, path), gold)
                    f0_std, f0_range, dur = f0_stats(path)
                    label, score = emotion_top(emo, path)
                    hit = label == TARGET_LABEL[emo_name]
                    rows.append(
                        {
                            "engine": engine,
                            "target_emotion": emo_name,
                            "file": fname,
                            "speaker_sim": round(sim, 4),
                            "f0_std_st": round(f0_std, 2),
                            "f0_range_st": round(f0_range, 2),
                            "duration": round(dur, 2),
                            "pred_emotion": label,
                            "pred_score": round(score, 3),
                            "emotion_hit": int(hit),
                        }
                    )
                    print(f"  {engine}/{emo_name}/{fname} sim={sim:.3f} emo={label[:6]}", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"  失败 {path}: {type(exc).__name__}: {exc}", flush=True)

    with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(" CSV:", OUT_CSV, len(rows), "行", flush=True)

    # 汇总
    summary = []
    for engine in ("gsv", "indextts"):
        sub = [r for r in rows if r["engine"] == engine]
        if not sub:
            continue
        emo_rows = [r for r in sub if r["target_emotion"] in ("happy", "angry", "sad")]
        summary.append(
            {
                "engine": engine,
                "n": len(sub),
                "speaker_sim_mean": round(float(np.mean([r["speaker_sim"] for r in sub])), 4),
                "speaker_sim_std": round(float(np.std([r["speaker_sim"] for r in sub])), 4),
                "f0_std_mean": round(float(np.mean([r["f0_std_st"] for r in sub])), 2),
                "f0_range_mean": round(float(np.mean([r["f0_range_st"] for r in sub])), 2),
                "dur_mean": round(float(np.mean([r["duration"] for r in sub])), 2),
                "emotion_acc": round(float(np.mean([r["emotion_hit"] for r in emo_rows])), 3) if emo_rows else 0.0,
            }
        )
    lines = ["# 语音评测报告（S2）", "", "| 引擎 | 条数 | 说话人相似度(均值/σ) | F0 σ | F0 区间 | 平均时长 | 情绪命中率 |", "|---|---|---|---|---|---|---|"]
    for s in summary:
        lines.append(
            f"| {s['engine']} | {s['n']} | {s['speaker_sim_mean']} / {s['speaker_sim_std']} | {s['f0_std_mean']} | {s['f0_range_mean']} | {s['dur_mean']}s | {s['emotion_acc']} |"
        )
    lines += ["", "## 按情绪细分", "", "| 引擎 | 情绪 | 相似度均值 | F0 σ | 命中率 |", "|---|---|---|---|---|"]
    for engine in ("gsv", "indextts"):
        for emo_name in TARGET_LABEL:
            sub = [r for r in rows if r["engine"] == engine and r["target_emotion"] == emo_name]
            if not sub:
                continue
            lines.append(
                f"| {engine} | {emo_name} | {np.mean([r['speaker_sim'] for r in sub]):.4f} | {np.mean([r['f0_std_st'] for r in sub]):.2f} | {np.mean([r['emotion_hit'] for r in sub]):.2f} |"
            )
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
