"""八重樱 v2 重训（净化数据集 yae_clean.list，exp=yae2）。

运行：runtime\\python.exe D:\\AI\\yae_stepE2_train.py
"""

import json
import os
import subprocess
import sys

ROOT = r"D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50"
EXP_ROOT = r"D:\AI\gpt_sovits_logs"
EXP = "yae2"
S1_DIR = os.path.join(EXP_ROOT, EXP)
D_LIST = r"D:\AI\yae_dataset\yae_clean.list"
D_WAV = r"D:\AI\yae_dataset\segments"
PY = os.path.join(ROOT, "runtime", "python.exe")
VERSION = "v2"

BERT = "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"
CNHUBERT = "GPT_SoVITS/pretrained_models/chinese-hubert-base"
S2G = "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth"
S2D = "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2D2333k.pth"
S1 = "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"

S1_EPOCHS = 25
S2_EPOCHS = 10


def run(cmd, env_extra, name):
    env = dict(os.environ)
    env.update(env_extra)
    pp = [os.path.join(ROOT, "GPT_SoVITS"), ROOT, os.path.join(ROOT, "tools")]
    env["PYTHONPATH"] = os.pathsep.join(pp + [env.get("PYTHONPATH", "")])
    print(f"\n### {name}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT, env=env)
    if r.returncode != 0:
        print(f"!!! {name} 失败 retcode={r.returncode}", flush=True)
        sys.exit(1)


def prep_env():
    return {
        "inp_text": D_LIST,
        "inp_wav_dir": D_WAV,
        "exp_name": EXP,
        "opt_dir": S1_DIR,
        "i_part": "0",
        "all_parts": "1",
        "_CUDA_VISIBLE_DEVICES": "0",
        "is_half": "True",
        "version": VERSION,
    }


def step1a(env):
    run([PY, "-s", "GPT_SoVITS/prepare_datasets/1-get-text.py"],
        {**env, "bert_pretrained_dir": BERT}, "1a 文本特征")
    part = os.path.join(S1_DIR, "2-name2text-0.txt")
    out = os.path.join(S1_DIR, "2-name2text.txt")
    lines = []
    if os.path.exists(part):
        with open(part, encoding="utf-8") as f:
            lines = [ln for ln in f.read().strip("\n").split("\n") if ln]
        os.remove(part)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  2-name2text.txt: {len(lines)} 行", flush=True)


def step1b(env):
    run([PY, "-s", "GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py"],
        {**env, "cnhubert_base_dir": CNHUBERT}, "1b HuBERT 特征")


def step1c(env):
    run([PY, "-s", "GPT_SoVITS/prepare_datasets/3-get-semantic.py"],
        {**env, "pretrained_s2G": S2G, "s2config_path": "GPT_SoVITS/configs/s2.json"}, "1c 语义特征")
    part = os.path.join(S1_DIR, "6-name2semantic-0.tsv")
    out = os.path.join(S1_DIR, "6-name2semantic.tsv")
    body = []
    if os.path.exists(part):
        with open(part, encoding="utf-8") as f:
            body = [ln for ln in f.read().strip("\n").split("\n") if ln]
        os.remove(part)
    with open(out, "w", encoding="utf-8") as f:
        f.write("item_name\tsemantic_audio\n")
        f.write("\n".join(body) + "\n")
    print(f"  6-name2semantic.tsv: {len(body)} 行", flush=True)


def step_s1():
    import yaml
    if os.path.exists(os.path.join(ROOT, "GPT_weights_v2", f"{EXP}-e{S1_EPOCHS}.ckpt")):
        print("s1 权重已存在，跳过", flush=True)
        return

    with open(os.path.join(ROOT, "GPT_SoVITS/configs/s1longer-v2.yaml"), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    os.makedirs(os.path.join(S1_DIR, "logs_s1_v2"), exist_ok=True)
    data["train"].update({"batch_size": 4, "epochs": S1_EPOCHS, "save_every_n_epoch": 5,
                          "if_save_every_weights": True, "if_save_latest": True, "if_dpo": False})
    data["pretrained_s1"] = S1
    data["train"]["half_weights_save_dir"] = "GPT_weights_v2"
    data["train"]["exp_name"] = EXP
    data["train_semantic_path"] = os.path.join(S1_DIR, "6-name2semantic.tsv")
    data["train_phoneme_path"] = os.path.join(S1_DIR, "2-name2text.txt")
    data["output_dir"] = os.path.join(S1_DIR, "logs_s1_v2")
    tmp = os.path.join(EXP_ROOT, "tmp_s1_yae2.yaml")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True)
    run([PY, "-s", "GPT_SoVITS/s1_train.py", "--config_file", tmp],
        {"_CUDA_VISIBLE_DEVICES": "0", "hz": "25hz"}, "s1 GPT 训练")


def step_s2():
    with open(os.path.join(ROOT, "GPT_SoVITS/configs/s2.json"), encoding="utf-8") as f:
        data = json.load(f)
    data["train"].update({
        "batch_size": 8, "epochs": S2_EPOCHS, "text_low_lr_rate": 0.4,
        "pretrained_s2G": S2G, "pretrained_s2D": S2D,
        "if_save_every_weights": True, "save_every_n_epoch": 5, "save_every_epoch": 5,
        "if_save_latest": True, "gpu_numbers": "0",
    })
    data["data"]["exp_dir"] = S1_DIR
    data["s2_ckpt_dir"] = S1_DIR
    data["save_weight_dir"] = "SoVITS_weights_v2"
    data["name"] = EXP
    data["version"] = VERSION
    data["model"]["version"] = VERSION
    os.makedirs(os.path.join(S1_DIR, "logs_s2_v2"), exist_ok=True)
    tmp = os.path.join(EXP_ROOT, "tmp_s2_yae2.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    run([PY, "-s", "GPT_SoVITS/s2_train.py", "--config", tmp],
        {"_CUDA_VISIBLE_DEVICES": "0"}, "s2 SoVITS 训练")


def main() -> int:
    os.makedirs(S1_DIR, exist_ok=True)
    env = prep_env()
    step1a(env)
    step1b(env)
    step1c(env)
    step_s1()
    step_s2()
    print("\n重训完成。权重：GPT_weights_v2 / SoVITS_weights_v2", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
