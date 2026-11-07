# L3 第三方许可采集/生成器
# 用法：
#   1) 在每个运行时环境里各跑一次，输出 JSONL：
#      <env>\Scripts\python.exe ops\l3\licenses\collect_licenses.py --env python-core --out ops\l3\licenses\python-core.jsonl
#   2) 合并生成 Markdown 与 JSON：
#      python ops\l3\licenses\collect_licenses.py --merge
#
# 只读取 importlib.metadata，不修改环境。

import argparse
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # mobius/
OUT_DIR = ROOT / "ops" / "l3" / "licenses"

# 人工确认（仓库 LICENSE 已核对；分发前仍需复核）
OVERRIDES = {
    "addict": ("MIT", "人工确认"),
    "dotenv": ("MIT", "人工确认（pedroburon/dotenv）"),
    "langbot-plugin": ("AGPL-3.0", "人工确认（LangBot 生态）"),
    "matrix-nio": ("MIT", "人工确认"),
    "socksio": ("MIT", "人工确认"),
    "tiktoken": ("MIT", "人工确认"),
    "espeakng-loader": ("待确认", "可能包含 espeak-ng GPL 数据"),
}


def _license_of(meta) -> str:
    lic = meta.get("License") or ""
    if lic and len(lic) < 200:
        return " ".join(str(lic).split())
    for c in meta.get_all("Classifier") or []:
        m = re.match(r"License :: (?:OSI Approved :: )?(.+)", c)
        if m:
            return m.group(1).strip()
    if meta.get_all("License-Expression"):
        return meta["License-Expression"]
    return "UNKNOWN"


def collect(env_name: str, out_path: Path) -> None:
    from importlib.metadata import distributions

    rows = []
    for dist in distributions():
        meta = dist.metadata
        rows.append({
            "env": env_name,
            "name": meta.get("Name") or dist.name,
            "version": dist.version,
            "license": _license_of(meta),
            "home_page": meta.get("Home-page") or meta.get("Project-URL") or "",
        })
    rows.sort(key=lambda r: (r["name"] or "").lower())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"{env_name}: {len(rows)} 个包 -> {out_path}")


def export_texts(env_name: str, out_dir: Path) -> None:
    from importlib.metadata import distributions

    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for dist in distributions():
        name = (dist.metadata.get("Name") or dist.name or "").lower()
        for f in dist.files or []:
            fn = str(f).lower()
            if any(fn.endswith(x) for x in ("license", "license.txt", "license.md", "copying", "copying.txt", "notice")):
                try:
                    path = dist.locate_file(f)
                    if path.is_file() and path.stat().st_size < 200_000:
                        dest = out_dir / f"{name}-{dist.version}-{f.name}"[:120]
                        dest.write_bytes(path.read_bytes())
                        count += 1
                except OSError:
                    continue
                break
    print(f"{env_name}: 导出 {count} 份许可文本 -> {out_dir}")


def merge() -> None:
    data: dict[str, dict] = {}
    for path in sorted(OUT_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = (row["name"] or "").lower()
            entry = data.setdefault(key, {
                "name": row["name"], "version": row["version"], "license": row["license"],
                "home_page": row["home_page"], "envs": [],
            })
            if row["version"] != entry["version"]:
                entry.setdefault("other_versions", set()).add(f"{row['version']} ({row['env']})")
            entry["envs"].append(row["env"])
            if entry["license"] == "UNKNOWN" and row["license"] != "UNKNOWN":
                entry["license"] = row["license"]
            ov = OVERRIDES.get(entry["name"].lower())
            if ov and entry["license"] == "UNKNOWN":
                entry["license"] = f"{ov[0]}（{ov[1]}）"

    for entry in data.values():
        if isinstance(entry.get("other_versions"), set):
            entry["other_versions"] = sorted(entry["other_versions"])
        entry["envs"] = sorted(set(entry["envs"]))

    (OUT_DIR / "licenses.json").write_text(
        json.dumps({"packages": sorted(data.values(), key=lambda e: e["name"].lower())},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# 第三方 Python 包许可清单（自动生成）", "",
             "> 由 `ops/l3/licenses/collect_licenses.py` 从各运行时环境采集；",
             "> 覆盖范围：python-core / python-gateway / python-memory / python-langbot / python-kokoro。",
             "> 生成时间以 `licenses.json` 为准；分发前请复核 UNKNOWN 项。", "",
             "| 包 | 版本 | 许可 | 出现环境 | 主页 |",
             "|---|---|---|---|---|"]
    unknown = 0
    for entry in sorted(data.values(), key=lambda e: e["name"].lower()):
        lic = entry["license"]
        if lic == "UNKNOWN":
            unknown += 1
        versions = entry["version"]
        if entry.get("other_versions"):
            versions += " / " + ", ".join(entry["other_versions"])
        envs = ", ".join(entry["envs"])
        home = entry["home_page"]
        if home and len(home) > 60:
            home = home[:57] + "…"
        lines.append(f"| `{entry['name']}` | {versions} | {lic} | {envs} | {home} |")
    lines += ["", f"共 {len(data)} 个包，UNKNOWN {unknown} 个。", ""]
    (OUT_DIR / "THIRD-PARTY.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"合并完成：{len(data)} 个包（UNKNOWN {unknown}）-> THIRD-PARTY.md / licenses.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--texts", default="")
    ap.add_argument("--merge", action="store_true")
    args = ap.parse_args()
    if args.merge:
        merge()
        return 0
    if args.texts:
        export_texts(args.texts, OUT_DIR / "texts")
        return 0
    if not args.env or not args.out:
        print("用法: --env <name> --out <jsonl>  或  --merge")
        return 2
    collect(args.env, Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
