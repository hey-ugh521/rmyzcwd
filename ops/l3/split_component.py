"""把单个组件归档切成 GitHub Releases 可用的分卷（每卷 <2GB），并更新 manifest 片段。

用法：
  python ops/l3/split_component.py --id comfyui-runtime --archive D:\AI\components\comfyui-runtime-20261108.7z
输出：
  - 分卷：<archive>.001, .002, ...
  - 片段：ops/l3/packs/<id>-<version>.json 增加 "parts": [{url,size,sha256}]
"""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKS = ROOT / "ops" / "l3" / "packs"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--version", default="")
    ap.add_argument("--chunk-mb", type=int, default=1900)
    args = ap.parse_args()

    archive = Path(args.archive)
    if not archive.is_file():
        raise SystemExit(f"归档不存在: {archive}")
    version = args.version
    if not version:
        m = re.match(rf"{re.escape(args.id)}-(.+)\.7z$", archive.name)
        version = m.group(1) if m else "unknown"

    frag_path = PACKS / f"{args.id}-{version}.json"
    frag = json.loads(frag_path.read_text(encoding="utf-8"))

    chunk = args.chunk_mb * 1024 * 1024
    parts = []
    whole = hashlib.sha256()
    idx = 1
    with open(archive, "rb") as src:
        while True:
            part_path = Path(str(archive) + f".{idx:03d}")
            size = 0
            h = hashlib.sha256()
            with open(part_path, "wb") as out:
                while size < chunk:
                    block = src.read(min(1024 * 1024, chunk - size))
                    if not block:
                        break
                    out.write(block)
                    whole.update(block)
                    h.update(block)
                    size += len(block)
            if size == 0:
                part_path.unlink(missing_ok=True)
                break
            parts.append({
                "index": idx - 1,
                "name": part_path.name,
                "url": f"https://github.com/hey-ugh521/mobius/releases/download/l3-{version}/" + part_path.name,
                "size": size,
                "sha256": h.hexdigest(),
            })
            print(f"  分卷 {part_path.name}: {size/1024/1024:.1f} MB")
            idx += 1

    frag["size"] = sum(p["size"] for p in parts)
    frag["sha256"] = whole.hexdigest()
    frag["parts"] = parts
    frag["urls"] = [p["url"] for p in parts]
    frag["install"] = {**frag.get("install", {}), "format": "7z", "parts": True}
    frag_path.write_text(json.dumps(frag, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"片段已更新: {frag_path}（{len(parts)} 卷，合计 {frag['size']/1024/1024/1024:.2f} GB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
