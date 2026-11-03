"""八重樱数据集 Step A：把 B站分P 转 wav 并用 UVR5 分离人声。

运行：runtime\\python.exe D:\\AI\\yae_pipeline_stepA.py
"""

import os
import subprocess
import sys

ROOT = r"D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50"
SRC = r"D:\TTS_Workspace\yae_audio"
WORK = r"D:\AI\yae_dataset"
INP = os.path.join(WORK, "input")
VOCAL = os.path.join(WORK, "vocals")
INS = os.path.join(WORK, "instrumental")
FFMPEG = os.path.join(ROOT, "ffmpeg.exe")

FILES = [
    "（更新护士服）八重樱，逆神の巫女，2～4心好感度，中日cv切换（崩壞3rd） p01 2心好感度，来自八重樱的诅咒？.wav",
    "p2.wav",
    "p3.wav",
    "p4.wav",
]


def main() -> int:
    for d in (INP, VOCAL, INS):
        os.makedirs(d, exist_ok=True)

    # 1) 统一转 44.1kHz 立体声 wav
    wavs = []
    for i, name in enumerate(FILES, 1):
        src = os.path.join(SRC, name)
        if not os.path.exists(src):
            print(f"  跳过（不存在）: {name}")
            continue
        out = os.path.join(INP, f"p{i}_44k.wav")
        if not os.path.exists(out):
            cmd = [FFMPEG, "-y", "-i", src, "-ar", "44100", "-ac", "2", out]
            subprocess.run(cmd, capture_output=True, timeout=600)
        wavs.append(out)
        print(f"  转换完成: {os.path.basename(out)}")

    if not wavs:
        print("没有可用输入")
        return 1

    # 2) UVR5 人声分离（HP2_all_vocals：主唱/主说话声）
    # webui.py 在导入时读取 sys.argv[1:5]，必须先伪造完整参数
    sys.argv = ["webui.py", "cuda", "True", "7865", "False"]
    sys.path.insert(0, os.path.join(ROOT, "tools", "uvr5"))
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    import importlib

    uvw = importlib.import_module("tools.uvr5.webui")
    uvw.device = "cuda"
    uvw.is_half = True
    uvw.uvr(
        "HP2_all_vocals",
        INP,
        VOCAL,
        wavs,
        INS,
        10,
        "wav",
    )
    print("Step A 完成:", VOCAL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
