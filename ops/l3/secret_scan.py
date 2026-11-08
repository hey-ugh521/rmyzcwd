r"""发布前密钥/隐私扫描：阻止把真实配置、令牌、聊天库打进组件包或安装包。

用法：
  python ops/l3/secret_scan.py --dir D:\AI\langbot-native --exclude .venv data
  python ops/l3/secret_scan.py --pack D:\AI\components\langbot-20261108.7z   # 扫描 7z 文件列表

规则（文本文件）：
  - sk- / lbk_ / github_pat_ 等 API Key 模式
  - NAPCAT_HTTP_TOKEN= / access-token: / token: 后跟长随机串
  - recovery_key / secret: 32+ hex
  - 明确的敏感路径：data/config.yaml、*.db（langbot 用户库）、onebot11_*.json、webui.json

退出码：0 干净；1 有命中（禁止发布）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PATTERNS: list[tuple[str, re.Pattern]] = [
    ("openai/deepseek key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("langbot api key", re.compile(r"lbk_[A-Za-z0-9_-]{20,}")),
    ("github token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("napcat token assignment", re.compile(r"NAPCAT_HTTP_TOKEN\s*=\s*[A-Za-z0-9]{16,}")),
    ("onebot token", re.compile(r"\"token\"\s*:\s*\"[A-Za-z0-9]{16,}\"")),
    ("long secret hex", re.compile(r"(?i)\b(?:secret|recovery_key|password)\s*[:=]\s*[\"']?[A-Za-z0-9_+/=-]{16,}")),
]

SENSITIVE_NAMES = [
    re.compile(r"(^|[\\/])data[\\/]config\.yaml$"),
    re.compile(r"(^|[\\/])webui\.json$"),
    re.compile(r"(^|[\\/])onebot11_\d+\.json$"),
    re.compile(r"\.db(-wal|-shm)?$"),
    re.compile(r"(^|[\\/])\.env$"),
]

TEXT_EXT = {".py", ".json", ".yaml", ".yml", ".txt", ".md", ".ini", ".conf", ".bat", ".ps1", ".js", ".ts", ".mjs"}


def scan_dir(root: Path, excludes: list[str]) -> list[str]:
    hits: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        # 示例文件只含形状合法的占位值（tests/test_secrets_rotate.py 校验），不参与扫描
        if rel.endswith("secrets.example.json"):
            continue
        if any(rel == ex or rel.startswith(ex.rstrip("/") + "/") for ex in excludes):
            continue
        if any(p.search(rel) for p in SENSITIVE_NAMES):
            hits.append(f"{rel}  [敏感文件]")
            continue
        if path.suffix.lower() not in TEXT_EXT or path.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pattern in PATTERNS:
            if pattern.search(text):
                hits.append(f"{rel}  [{name}]")
                break
    return hits


def scan_pack(path: Path) -> list[str]:
    sevenzip = r"D:\AI\napcat-native\7z.exe"
    proc = subprocess.run([sevenzip, "l", str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        return [f"{path.name}  [无法读取归档]"]
    hits: list[str] = []
    for line in proc.stdout.splitlines():
        m = re.search(r"\s([^\s]+\.(?:json|yaml|yml|db|env|txt|md))$", line)
        if not m:
            continue
        name = m.group(1)
        if any(p.search(name) for p in SENSITIVE_NAMES):
            hits.append(f"{name}  [敏感文件]")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="")
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--pack", default="")
    args = ap.parse_args()

    hits: list[str] = []
    if args.dir:
        root = Path(args.dir)
        print(f"扫描目录: {root}")
        hits += scan_dir(root, args.exclude)
    if args.pack:
        pack = Path(args.pack)
        print(f"扫描归档: {pack}")
        hits += scan_pack(pack)

    if hits:
        print("\n发现敏感内容（禁止发布）:")
        for h in hits[:50]:
            print("  -", h)
        if len(hits) > 50:
            print(f"  ... 共 {len(hits)} 条")
        return 1
    print("干净：未发现密钥/敏感文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
