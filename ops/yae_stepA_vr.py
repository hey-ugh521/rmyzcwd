"""八重樱数据集 Step A（直接调用 VR 人声分离，绕开 webui/gradio）。

运行：runtime\\python.exe D:\\AI\\yae_stepA_vr.py
"""

import os
import sys

ROOT = r"D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50"
WORK = r"D:\AI\yae_dataset"
INP = os.path.join(WORK, "input")
VOCAL = os.path.join(WORK, "vocals")
INS = os.path.join(WORK, "instrumental")

sys.path.insert(0, os.path.join(ROOT, "tools", "uvr5"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from vr import AudioPre  # noqa: E402

MODEL = os.path.join(ROOT, "tools", "uvr5", "uvr5_weights", "HP2_all_vocals.pth")


def main() -> int:
    os.makedirs(VOCAL, exist_ok=True)
    os.makedirs(INS, exist_ok=True)
    files = sorted(f for f in os.listdir(INP) if f.endswith(".wav"))
    if not files:
        print("没有输入文件")
        return 1
    pre = AudioPre(agg=10, model_path=MODEL, device="cuda", is_half=True)
    for i, name in enumerate(files, 1):
        path = os.path.join(INP, name)
        print(f"[{i}/{len(files)}] 分离 {name} ...", flush=True)
        pre._path_audio_(
            path,
            ins_root=INS,
            vocal_root=VOCAL,
            format="wav",
            is_hp3=False,
        )
        print(f"  完成 {name}", flush=True)
    print("Step A 完成:", VOCAL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
