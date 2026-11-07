"""把 GitHub Release 资产写回组件 manifest（私有仓库用 API 资产 URL），并生成发布清单。

用法：
  python ops/l3/finalize_release.py --repo hey-ugh521/mobius --tag l3-20261108 --version 0.2.0

依赖：gh CLI 已登录；仓库私有 → 下载器需设置 MOBIUS_GH_TOKEN。
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKS = ROOT / "ops" / "l3" / "packs"
GH = r"D:\Tools\gh\bin\gh.exe"


def gh_json(repo: str, tag: str) -> dict:
    out = subprocess.run(
        [GH, "release", "view", tag, "--repo", repo,
         "--json", "assets,url,name,tagName"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    release = gh_json(args.repo, args.tag)
    assets = {a["name"]: a for a in release.get("assets") or []}
    print(f"Release {args.tag}: {len(assets)} 个资产")

    frag_path = PACKS / "fragments.json"
    manifest = json.loads(frag_path.read_text(encoding="utf-8"))
    missing: list[str] = []

    for cid, comp in (manifest.get("components") or {}).items():
        ver = comp.get("version")
        if comp.get("parts"):
            for part in comp["parts"]:
                name = part.get("name")
                if not name and part.get("index") is not None:
                    name = f"{cid}-{ver}.7z.{int(part['index']) + 1:03d}"
                if not name:
                    name = str(part.get("url", "")).split("/")[-1]
                asset = assets.get(name)
                if not asset:
                    missing.append(name)
                    continue
                part["url"] = asset.get("apiUrl") or asset.get("url")
                part.setdefault("name", name)
            comp["urls"] = [p["url"] for p in comp["parts"]]
        else:
            name = f"{cid}-{ver}.7z"
            asset = assets.get(name)
            if not asset:
                missing.append(name)
                continue
            comp["urls"] = [asset.get("apiUrl") or asset.get("url")]

    if missing:
        print("缺失资产（可重跑上传后再次 finalize）：")
        for name in missing:
            print("  -", name)

    manifest["release"] = {"repo": args.repo, "tag": args.tag, "assets": len(assets)}
    frag_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest 已更新: {frag_path}")

    out = Path(args.out) if args.out else ROOT / "release" / args.version
    out.mkdir(parents=True, exist_ok=True)
    (out / "fragments.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"发布副本: {out / 'fragments.json'}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
