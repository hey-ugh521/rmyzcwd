"""德丽莎 V4 训练（复用 teresa 预处理数据：s1 GPT + s2 V4 LoRA）。

运行：runtime\\python.exe D:\\AI\\teresa_v4_train.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = r"D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50"
EXP_ROOT = r"D:\AI\gpt_sovits_logs"
EXP = "teresa44"        # 44.1k 重建数据集
EXP_NAME_V4 = "teresa44"
S1_DIR = os.path.join(EXP_ROOT, EXP)
PY = os.path.join(ROOT, "runtime", "python.exe")

V4_S1 = "GPT_SoVITS/pretrained_models/s1v3.ckpt"
V4_S2G = "GPT_SoVITS/pretrained_models/gsv-v4-pretrained/s2Gv4.pth"
S1_EPOCHS = 20
S2_EPOCHS = 12
LORA_RANK = 32


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


def step_s1():
    import yaml

    with open(os.path.join(ROOT, "GPT_SoVITS/configs/s1longer-v2.yaml"), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    out_dir = os.path.join(S1_DIR, "logs_s1_v4")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "GPT_weights_v4"), exist_ok=True)
    data["train"].update({"batch_size": 4, "epochs": S1_EPOCHS, "save_every_n_epoch": 5,
                          "if_save_every_weights": True, "if_save_latest": True, "if_dpo": False})
    data["pretrained_s1"] = V4_S1
    data["train"]["half_weights_save_dir"] = "GPT_weights_v4"
    data["train"]["exp_name"] = EXP_NAME_V4
    data["train_semantic_path"] = os.path.join(S1_DIR, "6-name2semantic.tsv")
    data["train_phoneme_path"] = os.path.join(S1_DIR, "2-name2text.txt")
    data["output_dir"] = out_dir
    tmp = os.path.join(EXP_ROOT, "tmp_s1_teresa44.yaml")
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True)
    run([PY, "-s", "GPT_SoVITS/s1_train.py", "--config_file", tmp],
        {"_CUDA_VISIBLE_DEVICES": "0", "hz": "25hz"}, "s1 GPT(v4) 训练")


def step_s2():
    with open(os.path.join(ROOT, "GPT_SoVITS/configs/s2.json"), encoding="utf-8") as f:
        data = json.load(f)
    data["train"].update({
        "batch_size": 4, "epochs": S2_EPOCHS, "log_interval": 100,
        "fp16_run": True, "gpu_numbers": "0", "grad_ckpt": True,
        "lora_rank": LORA_RANK,
        "pretrained_s2G": V4_S2G,
        "if_save_every_weights": True, "save_every_epoch": 2,
        "if_save_latest": True,
    })
    data["data"]["exp_dir"] = S1_DIR
    data["s2_ckpt_dir"] = S1_DIR
    data["save_weight_dir"] = "SoVITS_weights_v4"
    data["name"] = EXP_NAME_V4
    data["model"]["version"] = "v4"
    os.makedirs(os.path.join(ROOT, "SoVITS_weights_v4"), exist_ok=True)
    os.makedirs(os.path.join(S1_DIR, "logs_s2_v4_lora_%s" % LORA_RANK), exist_ok=True)
    tmp = os.path.join(EXP_ROOT, "tmp_s2_teresa44.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    run([PY, "-s", "GPT_SoVITS/s2_train_v3_lora.py", "--config", tmp],
        {"_CUDA_VISIBLE_DEVICES": "0"}, "s2 V4 LoRA 训练")


def main() -> int:
    env = {
        "inp_text": r"D:\AI\teresa44_dataset\teresa44.list",
        "inp_wav_dir": r"D:\AI\teresa44_dataset\segments",
        "exp_name": EXP, "opt_dir": S1_DIR,
        "i_part": "0", "all_parts": "1",
        "_CUDA_VISIBLE_DEVICES": "0", "is_half": "True", "version": "v2",
    }
    import subprocess as sp
    BERT = "GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large"
    CNH = "GPT_SoVITS/pretrained_models/chinese-hubert-base"
    S2G0 = "GPT_SoVITS/pretrained_models/gsv-v2final-pretrained/s2G2333k.pth"
    run([PY, "-s", "GPT_SoVITS/prepare_datasets/1-get-text.py"], {**env, "bert_pretrained_dir": BERT}, "1a")
    part = os.path.join(S1_DIR, "2-name2text-0.txt"); out = os.path.join(S1_DIR, "2-name2text.txt")
    lines = []
    if os.path.exists(part):
        lines = [ln for ln in open(part, encoding="utf-8").read().strip("\n").split("\n") if ln]
        os.remove(part)
    open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    run([PY, "-s", "GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py"], {**env, "cnhubert_base_dir": CNH}, "1b")
    run([PY, "-s", "GPT_SoVITS/prepare_datasets/3-get-semantic.py"], {**env, "pretrained_s2G": S2G0, "s2config_path": "GPT_SoVITS/configs/s2.json"}, "1c")
    part = os.path.join(S1_DIR, "6-name2semantic-0.tsv"); out = os.path.join(S1_DIR, "6-name2semantic.tsv")
    body = []
    if os.path.exists(part):
        body = [ln for ln in open(part, encoding="utf-8").read().strip("\n").split("\n") if ln]
        os.remove(part)
    with open(out, "w", encoding="utf-8") as f:
        f.write("item_name\tsemantic_audio\n"); f.write("\n".join(body) + "\n")
    step_s2()
    print("\nV4 训练完成：GPT_weights_v4 / SoVITS_weights_v4", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
