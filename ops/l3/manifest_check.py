"""Möbius 组件 manifest 生成 / 漂移校验（L3 发布流水线）。

背景
----
``ops/l3/packs/*.json`` 是逐组件片段（源），``ops/l3/packs/fragments.json`` 是
聚合发布 manifest（生成物）。历史上聚合逻辑内联在 ``ops/l3/build_release.ps1``
的 here-string 里，与源片段各写一份，曾出现「旧分卷清单复活」——源片段已经
换成单包，聚合结果却还保留着旧的多卷 parts/urls。

本脚本把聚合逻辑收敛成唯一实现，并额外提供漂移校验与产物校验：

- ``aggregate``：源片段 -> manifest（components 按 id 排序，顶层字段固定）。
- URL/parts 保留规则：仅当 existing 中同 id 的 sha256 与源片段一致时，才沿用
  existing 的 parts（含 urls）/urls；sha 变化时一律用源片段，旧分卷不会被复活。
- ``diff_manifests``：逐组件逐字段比较，返回可读差异（URL 截断、去查询串，不打印密钥）。
- ``verify_packs``：校验磁盘上的 ``.7z`` / ``.7z.001..`` 是否存在且 size/sha256 一致；
  分卷按 1MB chunk 流式计算拼接后的整体 sha256 与 manifest 比对。
- CLI：
    python ops/l3/manifest_check.py check [--check-packs --component-dir D:\\AI\\components] [--json]
    python ops/l3/manifest_check.py write [--json]
  check 只读；有漂移/缺失退出码 1。write 原子写 fragments.json（临时文件 + os.replace），
  内容语义相同则不写。

设计约束：标准库实现（无第三方依赖）；组件以源片段为准；fragments.json 只由
build_release.ps1 调用 ``write`` 生成，仓库里的副本用 ``check`` 做 drift 闸门。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1
API_VERSION = 1
LAUNCHER_MIN_VERSION = "0.2.0"

# 极简核心必需组件（与 docs/L3-COMPONENTS.md 一致；kokoro/comfyui/gsv 为可选）
REQUIRED: tuple[str, ...] = (
    "python-core",
    "python-gateway",
    "python-memory",
    "langbot",
    "python-langbot",
    "postgres",
    "napcat-code",
)

# 组件字段的稳定输出顺序（其余源字段按字母序追加，保持向前兼容）
COMPONENT_ORDER: tuple[str, ...] = (
    "id",
    "name",
    "kind",
    "version",
    "required",
    "depends_on",
    "size",
    "sha256",
    "urls",
    "install",
    "license",
    "parts",
)

CHUNK_SIZE = 1024 * 1024  # 1MB：分卷整体 sha256 的流式块大小

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKS_DIR = REPO_ROOT / "ops" / "l3" / "packs"
MANIFEST_PATH = PACKS_DIR / "fragments.json"
DEFAULT_COMPONENT_DIR = Path(r"D:\AI\components")


# ---------------------------------------------------------------- 读取 / 聚合

def load_packs(packs_dir: str | os.PathLike) -> list[dict]:
    """读取源片段（``packs/*.json``，排除聚合产物 ``fragments.json``）。"""
    base = Path(packs_dir)
    packs: list[dict] = []
    for path in sorted(base.glob("*.json")):
        if path.name == "fragments.json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"源片段无法解析: {path}: {exc}") from exc
        if not isinstance(data, dict) or not str(data.get("id") or "").strip():
            raise ValueError(f"源片段缺少 id: {path}")
        packs.append(data)
    packs.sort(key=lambda d: str(d["id"]))
    return packs


def load_manifest(path: str | os.PathLike) -> dict:
    """读取聚合 manifest；文件不存在返回空 dict（首次生成场景）。"""
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _iter_packs(packs) -> list[dict]:
    """兼容 ``[fragment, ...]`` 与 ``{id: fragment}`` 两种输入。"""
    if isinstance(packs, dict):
        return [v for v in packs.values() if isinstance(v, dict)]
    return [v for v in (packs or []) if isinstance(v, dict)]


def aggregate(
    packs,
    existing: dict | None = None,
    required: tuple[str, ...] | set[str] = REQUIRED,
    launcher_min_version: str = LAUNCHER_MIN_VERSION,
    release=None,
) -> dict:
    """把源片段聚合为发布 manifest。

    - 每个组件以源片段为准；``required`` 由 ``required`` 集合覆盖。
    - URL/parts 保留：仅当 existing 同 id 的 sha256 一致时才沿用 existing 的
      parts（含 urls）/单包 urls；否则用源片段的，防止旧分卷清单复活。
    - components 按 id 排序；顶层字段固定为
      schema / api_version / launcher_min_version / release / components。
    """
    existing = existing or {}
    if release is None:
        release = (existing or {}).get("release")
    old_components = (existing.get("components") or {}) if isinstance(existing, dict) else {}
    required_set = set(required or ())
    generated: dict[str, dict] = {}

    for frag in sorted(_iter_packs(packs), key=lambda d: str(d.get("id") or "")):
        cid = str(frag.get("id") or "").strip()
        if not cid:
            raise ValueError("源片段缺少 id")
        comp = {key: copy.deepcopy(frag[key]) for key in COMPONENT_ORDER if key in frag}
        # 源片段的扩展字段（未来新增）保留，按字母序追加，保证确定性
        for key in sorted(set(frag) - set(COMPONENT_ORDER)):
            comp[key] = copy.deepcopy(frag[key])
        comp["id"] = cid
        comp["required"] = cid in required_set

        prev = old_components.get(cid) or {}
        prev_sha = str(prev.get("sha256") or "").strip().lower()
        cur_sha = str(comp.get("sha256") or "").strip().lower()
        if prev_sha and prev_sha == cur_sha:
            # sha 一致：existing 可能已被发布流程重写（asset URL / -BaseUrl），安全沿用
            if prev.get("parts"):
                comp["parts"] = copy.deepcopy(prev["parts"])
                if prev.get("urls"):
                    comp["urls"] = copy.deepcopy(prev["urls"])
                elif any(p.get("url") for p in comp["parts"] if isinstance(p, dict)):
                    comp["urls"] = [p["url"] for p in comp["parts"]
                                    if isinstance(p, dict) and p.get("url")]
            elif not comp.get("parts") and prev.get("urls"):
                # 单包同理：只沿用已发布的 urls，不沿用 parts
                comp["urls"] = copy.deepcopy(prev["urls"])
        # sha 不一致（或 existing 无此组件）：保留源片段的 parts/urls，旧分卷不复活

        generated[cid] = comp

    return {
        "schema": SCHEMA_VERSION,
        "api_version": API_VERSION,
        "launcher_min_version": str(launcher_min_version),
        "release": copy.deepcopy(release),
        "components": {cid: generated[cid] for cid in sorted(generated)},
    }


# ---------------------------------------------------------------- 漂移比较

def _short(text, limit: int = 80) -> str:
    s = str(text)
    return s if len(s) <= limit else s[:limit] + "…"


def _redact_url(url) -> str:
    """URL 去掉查询串/片段后截断：漂移报告可读且不泄露 token。"""
    s = str(url)
    for sep in ("?", "#"):
        if sep in s:
            s = s.split(sep, 1)[0]
    return _short(s, 70)


def _urls_list(urls) -> list[str]:
    if urls is None:
        return []
    if isinstance(urls, str):
        return [urls]
    return [str(u) for u in urls]


def _fmt(value, limit: int = 120) -> str:
    if isinstance(value, (dict, list)):
        return _short(json.dumps(value, ensure_ascii=False, sort_keys=True), limit)
    if isinstance(value, str):
        return _short(value, limit)
    return _short(repr(value), limit)


def _fmt_urls(urls) -> str:
    return "[" + ", ".join(_redact_url(u) for u in _urls_list(urls)) + "]"


def _parts_sig(parts) -> list[tuple]:
    """卷签名：按 index 排序后的 (index, name, sha256, size)。"""
    items = [p for p in (parts or []) if isinstance(p, dict)]
    items.sort(key=lambda p: int(p.get("index", 0) or 0))
    return [
        (int(p.get("index", i) or 0), str(p.get("name") or ""),
         str(p.get("sha256") or "").lower(), p.get("size"))
        for i, p in enumerate(items)
    ]


def _fmt_parts(parts) -> str:
    items = [p for p in (parts or []) if isinstance(p, dict)]
    if not items:
        return "无"
    items.sort(key=lambda p: int(p.get("index", 0) or 0))
    rows = []
    for p in items:
        sha = str(p.get("sha256") or "")[:12]
        rows.append("%s(sha=%s, size=%s)" % (p.get("name") or "?", sha or "—",
                                             p.get("size") if p.get("size") is not None else "—"))
    return "[" + ", ".join(rows) + "]"


_DIFF_FIELDS: tuple[str, ...] = (
    "name", "kind", "version", "required", "depends_on", "size",
    "sha256", "urls", "install", "license", "parts",
)


def diff_manifests(existing: dict | None, generated: dict | None) -> list[str]:
    """逐组件逐字段比较，返回可读差异列表；无差异返回空列表。"""
    existing = existing or {}
    generated = generated or {}
    diffs: list[str] = []

    for key in ("schema", "api_version", "launcher_min_version", "release"):
        a, b = existing.get(key), generated.get(key)
        if a != b:
            diffs.append(f"顶层 {key}: existing={_fmt(a)} generated={_fmt(b)}")

    ec = existing.get("components") or {}
    gc = generated.get("components") or {}
    for cid in sorted(set(ec) | set(gc)):
        if cid not in gc:
            diffs.append(f"组件 {cid}: 仅存在于 existing（生成结果缺失）")
            continue
        if cid not in ec:
            diffs.append(f"组件 {cid}: 仅存在于 generated（新增）")
            continue
        a = ec.get(cid) or {}
        b = gc.get(cid) or {}
        for field in _DIFF_FIELDS:
            if field == "parts":
                pa, pb = a.get("parts") or [], b.get("parts") or []
                sa, sb = _parts_sig(pa), _parts_sig(pb)
                if sa != sb:
                    if len(pa) != len(pb):
                        diffs.append(f"组件 {cid} parts: 数量 existing={len(pa)} generated={len(pb)}"
                                     f"（existing={_fmt_parts(pa)} generated={_fmt_parts(pb)}）")
                    else:
                        diffs.append(f"组件 {cid} parts: existing={_fmt_parts(pa)}"
                                     f" generated={_fmt_parts(pb)}")
                continue
            if field == "urls":
                ua, ub = _urls_list(a.get("urls")), _urls_list(b.get("urls"))
                if ua != ub:
                    diffs.append(f"组件 {cid} urls: existing={_fmt_urls(ua)}"
                                 f" generated={_fmt_urls(ub)}")
                continue
            va, vb = a.get(field), b.get(field)
            if va != vb:
                diffs.append(f"组件 {cid} {field}: existing={_fmt(va)} generated={_fmt(vb)}")
        for field in sorted(set(a) - set(b)):
            if field != "id" and field not in _DIFF_FIELDS:
                diffs.append(f"组件 {cid} {field}: 仅存在于 existing")
        for field in sorted(set(b) - set(a)):
            if field != "id" and field not in _DIFF_FIELDS:
                diffs.append(f"组件 {cid} {field}: 仅存在于 generated")
    return diffs


# ---------------------------------------------------------------- 产物校验

def sha256_file(path: str | os.PathLike) -> str:
    """整文件 sha256（1MB chunk 流式，避免大包吃内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_concat(paths: list[Path]) -> str:
    """分卷按顺序拼接后的整体 sha256（流式，不落地拼接文件）。"""
    h = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(CHUNK_SIZE), b""):
                h.update(chunk)
    return h.hexdigest()


def verify_packs(components, component_dir: str | os.PathLike) -> dict:
    """校验磁盘组件包与 manifest 是否一致。

    ``components`` 可以是 ``{id: comp}`` 映射，也可以是整个 manifest
    （自动取其中的 ``components``）。返回：
        {"ok": bool, "component_dir": str, "checked": int,
         "missing": [..], "mismatched": [..], "errors": [..]}
    单包检查 ``<id>-<version>.7z`` 的 size/sha256；分卷逐卷检查 name/size/sha256，
    再流式拼接验证整体 sha256（与 manifest 顶层 sha256 比对）。
    """
    if isinstance(components, dict) and isinstance(components.get("components"), dict):
        components = components["components"]
    comps = components if isinstance(components, dict) else {}
    base = Path(component_dir)
    missing: list[str] = []
    mismatched: list[str] = []
    checked = 0

    for cid in sorted(comps):
        comp = comps[cid] if isinstance(comps[cid], dict) else {}
        version = str(comp.get("version") or "")
        parts = [p for p in (comp.get("parts") or []) if isinstance(p, dict)]

        if parts:
            parts.sort(key=lambda p: int(p.get("index", 0) or 0))
            files: list[Path] = []
            for idx, meta in enumerate(parts):
                name = str(meta.get("name") or f"{cid}-{version}.7z.{idx + 1:03d}")
                path = base / name
                files.append(path)
                checked += 1
                if not path.is_file():
                    missing.append(f"{cid}: 缺少分卷 {name}")
                    continue
                want_size = meta.get("size")
                if want_size is not None and path.stat().st_size != int(want_size):
                    mismatched.append(
                        f"{cid}: 分卷 {name} 大小不符（manifest={int(want_size)} 实际={path.stat().st_size}）")
                want_sha = str(meta.get("sha256") or "").strip().lower()
                if want_sha:
                    actual = sha256_file(path)
                    if actual != want_sha:
                        mismatched.append(
                            f"{cid}: 分卷 {name} sha256 不符（manifest={want_sha[:12]}… 实际={actual[:12]}…）")
            if len(files) == len(parts) and all(p.is_file() for p in files):
                want_sha = str(comp.get("sha256") or "").strip().lower()
                actual = _sha256_concat(files)
                if want_sha and actual != want_sha:
                    mismatched.append(
                        f"{cid}: 分卷拼接整体 sha256 不符（manifest={want_sha[:12]}… 实际={actual[:12]}…）")
                want_size = comp.get("size")
                total = sum(p.stat().st_size for p in files)
                if want_size is not None and total != int(want_size):
                    mismatched.append(
                        f"{cid}: 分卷总大小不符（manifest={int(want_size)} 实际={total}）")
            continue

        name = str(comp.get("archive_name") or f"{cid}-{version}.7z")
        path = base / name
        checked += 1
        if not path.is_file():
            missing.append(f"{cid}: 缺少组件包 {name}")
            continue
        want_size = comp.get("size")
        if want_size is not None and path.stat().st_size != int(want_size):
            mismatched.append(
                f"{cid}: 大小不符（manifest={int(want_size)} 实际={path.stat().st_size}）")
        want_sha = str(comp.get("sha256") or "").strip().lower()
        if want_sha:
            actual = sha256_file(path)
            if actual != want_sha:
                mismatched.append(
                    f"{cid}: sha256 不符（manifest={want_sha[:12]}… 实际={actual[:12]}…）")

    errors = missing + mismatched
    return {
        "ok": not errors,
        "component_dir": str(base),
        "checked": checked,
        "missing": missing,
        "mismatched": mismatched,
        "errors": errors,
    }


# ---------------------------------------------------------------- 写入

def canonical_json(manifest: dict) -> str:
    """确定性 JSON：ensure_ascii=False、indent=2、结尾换行、LF。"""
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def write_manifest(path: str | os.PathLike, manifest: dict) -> bool:
    """原子写 manifest；内容语义相同则不写。返回是否实际写入。"""
    p = Path(path)
    try:
        existing = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
    except Exception:  # noqa: BLE001
        existing = None
    if existing == manifest:
        return False

    p.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(manifest)
    fd, tmp_name = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, p)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return True


# ---------------------------------------------------------------- CLI

def _packs_dir(root: Path) -> Path:
    return Path(root) / "ops" / "l3" / "packs"


def run_check(root: str | os.PathLike, check_packs: bool = False,
              component_dir: str | os.PathLike = DEFAULT_COMPONENT_DIR) -> dict:
    """只读 drift 校验。返回结构化结果，供 CLI / 测试使用。"""
    packs_dir = _packs_dir(Path(root))
    manifest_path = packs_dir / "fragments.json"
    packs = load_packs(packs_dir)
    existing = load_manifest(manifest_path)
    generated = aggregate(packs, existing=existing)
    drift = diff_manifests(existing, generated)
    pack_ids = {str(p["id"]) for p in packs}
    missing_required = sorted(set(REQUIRED) - pack_ids)
    pack_result = verify_packs(generated["components"], component_dir) if check_packs else None
    return {
        "command": "check",
        "packs_dir": str(packs_dir),
        "manifest": str(manifest_path),
        "pack_count": len(packs),
        "component_count": len(generated["components"]),
        "manifest_exists": manifest_path.is_file(),
        "missing_required_packs": missing_required,
        "drift": drift,
        "packs_check": pack_result,
        "ok": (not drift) and (not missing_required)
              and (pack_result is None or pack_result["ok"]),
    }


def run_write(root: str | os.PathLike) -> dict:
    """按源片段原子重写 fragments.json。"""
    packs_dir = _packs_dir(Path(root))
    manifest_path = packs_dir / "fragments.json"
    packs = load_packs(packs_dir)
    existing = load_manifest(manifest_path)
    generated = aggregate(packs, existing=existing)
    written = write_manifest(manifest_path, generated)
    return {
        "command": "write",
        "manifest": str(manifest_path),
        "pack_count": len(packs),
        "component_count": len(generated["components"]),
        "written": written,
        "ok": True,
    }


def _print_check_human(result: dict) -> None:
    print("==> manifest 漂移校验")
    print(f"  源片段: {result['packs_dir']}（{result['pack_count']} 个）")
    state = "存在" if result["manifest_exists"] else "不存在（视为空）"
    print(f"  聚合文件: {result['manifest']}（{state}，{result['component_count']} 组件）")
    if result["missing_required_packs"]:
        print(f"  [WARN] 源片段缺少必需组件: {', '.join(result['missing_required_packs'])}")
    if result["drift"]:
        print(f"  [DRIFT] 发现 {len(result['drift'])} 处差异:")
        for line in result["drift"]:
            print(f"    - {line}")
    else:
        print("  [OK] 无漂移（fragments.json 与源片段一致）")
    pack_result = result.get("packs_check")
    if pack_result is None:
        print("  组件包校验: 跳过（未启用 --check-packs）")
    else:
        print(f"  组件包校验: {pack_result['component_dir']}"
              f"（检查 {pack_result['checked']} 个文件）")
        for line in pack_result["errors"][:50]:
            print(f"    - {line}")
        if len(pack_result["errors"]) > 50:
            print(f"    … 其余 {len(pack_result['errors']) - 50} 条省略")
        if pack_result["ok"]:
            print("  [OK] 组件包 size/sha256 全部一致")
    print("  结果: " + ("OK" if result["ok"] else "FAIL"))


def _print_write_human(result: dict) -> None:
    print("==> 生成组件 manifest")
    print(f"  源片段: {result['pack_count']} 个 -> {result['component_count']} 组件")
    if result["written"]:
        print(f"  [OK] 已原子写入: {result['manifest']}")
    else:
        print(f"  [OK] 内容未变化，未写入: {result['manifest']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Möbius 组件 manifest 生成与漂移校验（源片段 -> fragments.json）")
    parser.add_argument("--root", default=str(REPO_ROOT),
                        help="仓库根目录（默认按脚本位置推导）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="校验 fragments.json 与源片段是否漂移（只读）")
    p_check.add_argument("--check-packs", action="store_true",
                         help="同时校验组件包文件是否存在且 size/sha256 一致")
    p_check.add_argument("--component-dir", default=str(DEFAULT_COMPONENT_DIR),
                         help="组件包目录（默认 D:\\AI\\components）")
    p_check.add_argument("--json", action="store_true", help="输出 JSON")

    p_write = sub.add_parser("write", help="按源片段原子重写 fragments.json")
    p_write.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    try:
        if args.command == "check":
            result = run_check(root, check_packs=args.check_packs,
                               component_dir=args.component_dir)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                _print_check_human(result)
            return 0 if result["ok"] else 1
        if args.command == "write":
            result = run_write(root)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                _print_write_human(result)
            return 0 if result["ok"] else 1
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
