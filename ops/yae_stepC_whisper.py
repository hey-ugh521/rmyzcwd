"""八重樱数据集 Step C：faster-whisper large-v3 自动识别语言，只保留中文片段。

运行：runtime\\python.exe D:\\AI\\yae_stepC_whisper.py
"""

import os
import re

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

WORK = r"D:\AI\yae_dataset"
SLICES = os.path.join(WORK, "slices")
ASR_OUT = os.path.join(WORK, "asr_whisper")
LIST_FILE = os.path.join(WORK, "yae.list")

CJK = re.compile(r"[\u4e00-\u9fff]")


def main() -> int:
    from faster_whisper import WhisperModel

    os.makedirs(ASR_OUT, exist_ok=True)
    model = WhisperModel(
        r"D:\AI\models\faster-whisper-large-v3",
        device="cuda",
        compute_type="float16",
    )

    files = sorted(f for f in os.listdir(SLICES) if f.endswith(".wav"))
    print(f"切片数: {len(files)}", flush=True)

    rows = []
    for i, name in enumerate(files, 1):
        path = os.path.join(SLICES, name)
        try:
            segments, info = model.transcribe(
                path, language=None, vad_filter=False, beam_size=5
            )
            text = "".join(seg.text for seg in segments).strip()
        except Exception as exc:  # noqa: BLE001
            print(f"  失败 {name}: {type(exc).__name__}", flush=True)
            continue
        lang = getattr(info, "language", "")
        cjk = len(CJK.findall(text))
        if lang != "zh" or cjk < 3:
            continue
        if len(text) > 40:
            text = text[:40]
        rows.append((path, text, lang, getattr(info, "language_probability", 0.0)))
        if i % 20 == 0:
            print(f"  进度 {i}/{len(files)}，保留 {len(rows)}", flush=True)

    with open(LIST_FILE, "w", encoding="utf-8") as fh:
        for path, text, _lang, _p in rows:
            fh.write(f"{path}|八重樱|zh|{text}\n")

    total = sum(os.path.getsize(p) / 64000 for p, *_ in rows)
    print(f"保留中文片段: {len(rows)} 条，约 {total/60:.1f} 分钟 -> {LIST_FILE}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
