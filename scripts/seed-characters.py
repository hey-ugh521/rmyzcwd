"""从现有 LoRA 库补齐角色档案（幂等，绝不覆盖已存在的档案）。

背景：机器里有 31 个 LoRA，其中 21 个是崩坏3 角色 LoRA，但 data/characters 只有 3 个档案。
模型因此只能每次现拼 LoRA 名 —— 换成中文名字就命中不了，等于角色能力形同虚设。

用法：
    D:\\Anaconda3\\envs\\open-webui\\python.exe scripts\\seed-characters.py
    ... --dry-run      只看会生成什么
    ... --force        覆盖已存在档案（默认不动）

约定：
- 每个角色选「多套服装合集型」LoRA 作为主 LoRA（如 24in1 / 12in1 / 28in1），
  服装变体 LoRA 不自动塞进去，避免权重叠加过拟合；需要时在控制台里加。
- trigger 词按 Civitai 常见写法 "name (honkai impact 3rd)" 给，属于最佳猜测，
  控制台可改。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CHARS_DIR = BASE / "data" / "characters"
LORA_DIR = Path(r"D:\AI\comic\ComfyUI_windows_portable\ComfyUI\models\loras")

STYLE_PREFIX = "masterpiece, best quality, amazing quality, very aesthetic, absurdres, newest, 1girl, solo, "
NEGATIVE = (
    "lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, "
    "cropped, worst quality, low quality, jpeg artifacts, signature, watermark, username, blurry, nsfw"
)

# 角色档案：id -> 定义。aliases 里放中文名与常用外号，模型说中文名也能命中。
CAST = [
    {
        "id": "durandal",
        "name": "幽兰黛尔",
        "aliases": ["呆鹅", "比安卡", "比安卡·阿塔吉娜", "Bianka", "Durandal"],
        "lora": "Durandal20in1_Hi3_IL-03.safetensors",
        "weight": 0.75,
        "trigger": "durandal (honkai impact 3rd), bianka ataegina, blonde hair, blue eyes, side ponytail",
        "voice": "zf_xiaoyi",
    },
    {
        "id": "fuhua",
        "name": "符华",
        "aliases": ["华", "月轮", "云墨丹心", "赤鸢", "Fu Hua", "FuHua"],
        "lora": "FuHua24in1_Hi3_ILV2-03.safetensors",
        "weight": 0.75,
        "trigger": "fu hua (honkai impact 3rd), grey hair, blue eyes, hair bun, chinese clothes",
        "voice": "zf_xiaoni",
    },
    {
        "id": "griseo",
        "name": "格蕾修",
        "aliases": ["格雷修", "Griseo"],
        "lora": "Griseo10in1_Hi3_ILV3-03.safetensors",
        "weight": 0.75,
        "trigger": "griseo (honkai impact 3rd), blue hair, star hair ornament, yellow eyes, child",
        "voice": "zf_xiaoxiao",
    },
    {
        "id": "lisushang",
        "name": "李素裳",
        "aliases": ["素裳", "Li Sushang"],
        "lora": "LiSushang_Hi3_ILV4-03.safetensors",
        "weight": 0.75,
        "trigger": "li sushang (honkai impact 3rd), brown hair, yellow eyes, ponytail",
        "voice": "zf_xiaobei",
    },
    {
        "id": "pardofelis",
        "name": "帕朵菲莉丝",
        "aliases": ["帕朵", "猫猫", "Pardofelis"],
        "lora": "Pardofelis_Hi3_ILV2-03.safetensors",
        "weight": 0.75,
        "trigger": "pardofelis (honkai impact 3rd), pink hair, cat ears, green eyes",
        "voice": "zf_xiaoxiao",
    },
    {
        "id": "senti",
        "name": "识之律者",
        "aliases": ["识律", "识宝", "Senti", "Herrscher of Sentience"],
        "lora": "Senti_Hi3_IL-05.safetensors",
        "weight": 0.8,
        "trigger": "senti (honkai impact 3rd), white hair, red eyes, hair ornament",
        "voice": "zf_xiaoni",
    },
    {
        "id": "theresa",
        "name": "德丽莎",
        "aliases": ["德丽莎·阿波卡利斯", "大姨妈", "德莉莎", "Theresa", "Teri"],
        "lora": "Theresa_Apocalypse28in1_Hi3_IL-03.safetensors",
        "weight": 0.75,
        "trigger": "theresa apocalypse (honkai impact 3rd), white hair, blue eyes, twin braids",
        "voice": "zf_xiaobei",
    },
    {
        "id": "vita",
        "name": "薇塔",
        "aliases": ["薇塔·孤星旅者", "Vita"],
        "lora": "VitaV86_Hi3_IL-03.safetensors",
        "weight": 0.75,
        "trigger": "vita (honkai impact 3rd), white hair, red eyes, long hair",
        "voice": "zf_xiaoyi",
    },
    {
        "id": "aponia",
        "name": "阿波尼亚",
        "aliases": ["Aponia"],
        "lora": "Aponia_Hi3_ILV2-03.safetensors",
        "weight": 0.75,
        "trigger": "aponia (honkai impact 3rd), purple hair, purple eyes, veil",
        "voice": "zf_xiaoyi",
    },
]


def build_card(entry: dict) -> dict:
    return {
        "id": entry["id"],
        "name": entry["name"],
        "aliases": entry["aliases"],
        "checkpoint": "Illustrious-XL-v1.0.safetensors",
        "loras": [{"name": entry["lora"], "weight": entry["weight"]}],
        "workflow": "portrait",
        "size": [832, 1216],
        "style_prompt": STYLE_PREFIX + entry["trigger"],
        "negative_prompt": NEGATIVE,
        "voice": {"engine": "kokoro", "voice": entry["voice"], "speed": 1.0},
        "media_policy": {"allow_image": True, "allow_voice": True, "allow_clone": False},
        "constitution_id": entry["id"],
        # 供人和模型看的补充信息
        "notes": "主 LoRA 选的是合集型；服装变体 LoRA 未自动加入，可在控制台按需追加。trigger 词为最佳猜测。",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    CHARS_DIR.mkdir(parents=True, exist_ok=True)
    existing = {p.stem for p in CHARS_DIR.glob("*.json")}
    available = {p.name for p in LORA_DIR.glob("*.safetensors")} if LORA_DIR.exists() else set()

    created, skipped, missing = 0, 0, []
    for entry in CAST:
        if entry["lora"] not in available:
            missing.append(f"{entry['id']}: {entry['lora']}")
            continue
        path = CHARS_DIR / f"{entry['id']}.json"
        if path.exists() and not args.force:
            skipped += 1
            print(f"  跳过 {entry['id']}（已存在）")
            continue
        card = build_card(entry)
        print(f"  {'[dry] ' if args.dry_run else ''}生成 {entry['id']} ({entry['name']}) -> {entry['lora']}")
        if not args.dry_run:
            path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        created += 1

    print(f"\n新建 {created} 个，跳过 {skipped} 个，LoRA 缺失 {len(missing)} 个")
    if missing:
        for m in missing:
            print("  缺失:", m)
    total = len(list(CHARS_DIR.glob("*.json")))
    print(f"角色档案总数: {total}（之前 {len(existing)}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
