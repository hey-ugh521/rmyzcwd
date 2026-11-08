"""Möbius 组件下载器（L3 P3）

按 components manifest 下载、校验、解压、安装、修复、卸载运行时组件。

用法：
  python ops/l3/downloader.py --manifest ops/l3/packs/fragments.json --root D:\\Mobius list
  python ops/l3/downloader.py ... install python-core python-gateway
  python ops/l3/downloader.py ... install --required
  python ops/l3/downloader.py ... verify
  python ops/l3/downloader.py ... repair python-core
  python ops/l3/downloader.py ... uninstall python-gateway

特性：
- 多源 URLs（file:// 或 http(s)://），失败自动换源
- 断点续传（.part + Range）
- SHA256 + 大小强制校验
- 7z / zip 解压；自动处理归档根目录（单顶层目录 → 重命名为 target）
- versions.json 记录已装组件；本地 components.local.json 覆盖 manifest
- 只依赖标准库（7z 解压调用外部 7z.exe，可用 MOBIUS_7Z 覆盖）

注意：本工具不会删除用户数据；uninstall 只删组件 target 目录。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

CHUNK = 1024 * 1024
DEFAULT_7Z = os.environ.get("MOBIUS_7Z") or r"D:\AI\napcat-native\7z.exe"


def log(msg: str) -> None:
    print(msg, flush=True)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


class ComponentError(RuntimeError):
    pass


class Downloader:
    def __init__(self, manifest: Path, root: Path, cache: Path | None = None,
                 sevenzip: str = DEFAULT_7Z, source_dir: Path | None = None):
        self.manifest_path = manifest
        self.root = root.resolve()
        self.cache = (cache or (self.root / "components" / "cache")).resolve()
        self.sevenzip = sevenzip
        self.source_dir = source_dir
        self.manifest = self._load_manifest()
        self.components: dict = self.manifest.get("components") or {}
        self.versions_path = self.root / "versions.json"
        self.installed = self._load_versions()

    # ---------------- manifest / versions ----------------

    def _load_manifest(self) -> dict:
        with open(self.manifest_path, encoding="utf-8") as f:
            data = json.load(f)
        local = self.root / "components.local.json"
        if local.is_file():
            with open(local, encoding="utf-8") as f:
                override = json.load(f)
            comps = data.setdefault("components", {})
            for cid, entry in (override.get("components") or {}).items():
                comps[cid] = {**comps.get(cid, {}), **entry}
            log(f"已合并本地覆盖: {local}")
        return data

    def _load_versions(self) -> dict:
        if self.versions_path.is_file():
            try:
                return json.loads(self.versions_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return {"components": {}}

    def _save_versions(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.versions_path.write_text(json.dumps(self.installed, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------------- 依赖 ----------------

    def resolve(self, ids: list[str], required_only: bool = False) -> list[str]:
        if required_only:
            ids = [cid for cid, c in self.components.items() if c.get("required")]
        if not ids:
            raise ComponentError("未指定组件（用 --required 或显式 id）")
        order: list[str] = []
        seen: set[str] = set()

        def visit(cid: str) -> None:
            if cid in seen:
                return
            seen.add(cid)
            comp = self.components.get(cid)
            if not comp:
                raise ComponentError(f"manifest 中不存在组件: {cid}")
            for dep in comp.get("depends_on") or []:
                visit(dep)
            order.append(cid)

        for cid in ids:
            visit(cid)
        return order

    # ---------------- 下载 ----------------

    def _cache_paths(self, comp: dict) -> tuple[Path, Path]:
        sha = (comp.get("sha256") or "").lower()
        if not sha:
            raise ComponentError(f"组件 {comp.get('id')} 缺少 sha256")
        d = self.cache / sha[:2]
        d.mkdir(parents=True, exist_ok=True)
        ext = ".7z" if str((comp.get("install") or {}).get("format", "7z")).lower() == "7z" else ".zip"
        return d / f"{sha}{ext}", d / f"{sha}{ext}.part"

    def _url_candidates(self, comp: dict) -> list[str]:
        raw = comp.get("urls") or []
        urls = [raw] if isinstance(raw, str) else list(raw)
        if self.source_dir:
            # 从本地目录构造候选（文件名匹配）
            name = Path(comp.get("archive_name") or "").name
            if name:
                urls.insert(0, (self.source_dir / name).resolve().as_uri())
            else:
                for u in list(urls):
                    fn = Path(u).name
                    if fn:
                        urls.insert(0, (self.source_dir / fn).resolve().as_uri())
                        break
        return urls

    def _open(self, url: str, offset: int):
        headers = {"User-Agent": "MobiusDownloader/1.0"}
        # GitHub API 资产（私有仓库需要 token；公开仓库也支持）
        if "api.github.com" in url:
            headers["Accept"] = "application/octet-stream"
            token = os.environ.get("MOBIUS_GH_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        if offset > 0:
            req.add_header("Range", f"bytes={offset}-")
        return urllib.request.urlopen(req, timeout=60)

    def download(self, cid: str) -> Path:
        comp = self.components[cid]
        archive, part = self._cache_paths(comp)
        sha = comp["sha256"].lower()
        size = int(comp.get("size") or 0)

        if comp.get("parts"):
            return self._download_parts(cid, comp, archive, sha, size)

        if archive.is_file() and sha256_file(archive) == sha:
            log(f"  缓存命中: {archive.name}")
            return archive

        urls = self._url_candidates(comp)
        if not urls:
            raise ComponentError(f"组件 {cid} 没有可用 URL")

        for url in urls:
            try:
                offset = part.stat().st_size if part.is_file() else 0
                if size and offset > size:
                    part.unlink(missing_ok=True)
                    offset = 0
                log(f"  下载 {cid} <- {url}" + (f"（续传 {human(offset)}）" if offset else ""))
                with self._open(url, offset) as resp:
                    total = int(resp.headers.get("Content-Length") or 0) + offset
                    mode = "ab" if offset and resp.status == 206 else "wb"
                    if mode == "wb":
                        offset = 0
                    done = offset
                    last = 0.0
                    with open(part, mode) as out:
                        while True:
                            block = resp.read(CHUNK)
                            if not block:
                                break
                            out.write(block)
                            done += len(block)
                            now = time.time()
                            if now - last > 0.5 and total:
                                pct = done * 100.0 / total
                                log(f"\r    {pct:5.1f}%  {human(done)} / {human(total)}", )
                                last = now
                log("")
                actual = sha256_file(part)
                if actual != sha:
                    part.unlink(missing_ok=True)
                    raise ComponentError(f"SHA256 不匹配（期望 {sha[:12]}…，实际 {actual[:12]}…）")
                if size and part.stat().st_size != size:
                    part.unlink(missing_ok=True)
                    raise ComponentError(f"大小不匹配（期望 {size}，实际 {part.stat().st_size}）")
                if archive.exists():
                    archive.unlink()
                part.replace(archive)
                log(f"  ✓ 校验通过: {archive.name} ({human(archive.stat().st_size)})")
                return archive
            except (urllib.error.URLError, ComponentError, OSError, TimeoutError) as exc:
                log(f"  ✗ 源失败: {url} -> {exc}")
                continue
        raise ComponentError(f"{cid} 所有下载源均失败")

    # ---------------- 解压 / 安装 ----------------

    def _extract(self, archive: Path, fmt: str, target: Path) -> None:
        """解压到 target 的父目录临时工作区，再把顶层目录整体 rename。

        这样避免递归搬迁超长路径（Windows MAX_PATH）导致的 WinError 3。
        """
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".mobius-extract-", dir=str(parent)))
        try:
            use_7z = Path(self.sevenzip).is_file()
            if use_7z:
                proc = subprocess.run(
                    [self.sevenzip, "x", str(archive), f"-o{work}", "-y"],
                    capture_output=True, text=True,
                )
                if proc.returncode != 0:
                    raise ComponentError(f"7z 解压失败: {proc.stderr[-500:]}")
            elif fmt == "zip":
                import zipfile

                with zipfile.ZipFile(archive) as z:
                    z.extractall(work)
            else:
                raise ComponentError(f"找不到 7z 且不是 zip 包: {self.sevenzip}")

            entries = [p for p in work.iterdir()]
            if len(entries) == 1 and entries[0].is_dir():
                src = entries[0]
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                os.replace(src, target)
            else:
                target.mkdir(parents=True, exist_ok=True)
                for child in entries:
                    dest = target / child.name
                    if dest.exists():
                        shutil.rmtree(dest, ignore_errors=True) if dest.is_dir() else dest.unlink()
                    os.replace(child, dest)
        finally:
            shutil.rmtree(work, ignore_errors=True)


    def _download_parts(self, cid: str, comp: dict, archive: Path, sha: str, size: int) -> Path:
        """多卷组件：逐卷下载/校验（各自可续传），再拼接并校验整体 SHA256。"""
        if archive.is_file() and sha256_file(archive) == sha:
            log(f"  缓存命中: {archive.name}")
            return archive
        d = archive.parent
        d.mkdir(parents=True, exist_ok=True)
        part_files = []
        for idx, meta in enumerate(comp["parts"]):
            pf = d / f"{sha}.part{idx:03d}"
            part_files.append(pf)
            want_size = int(meta.get("size") or 0)
            want_sha = str(meta.get("sha256") or "").lower()
            if pf.is_file() and want_sha and sha256_file(pf) == want_sha and (not want_size or pf.stat().st_size == want_size):
                log(f"  分卷命中 ({idx + 1}/{len(comp['parts'])}): {pf.name}")
                continue
            urls = meta.get("urls") or ([meta["url"]] if meta.get("url") else [])
            if not urls:
                raise ComponentError(f"{cid} 分卷 {idx} 缺少 url")
            self._download_part(cid, urls, pf, idx + 1, len(comp["parts"]), want_sha, want_size)
        log(f"  拼接 {len(part_files)} 个分卷 -> {archive.name}")
        with open(archive, "wb") as out:
            for pf in part_files:
                with open(pf, "rb") as src:
                    for block in iter(lambda: src.read(CHUNK), b""):
                        out.write(block)
        if size and archive.stat().st_size != size:
            archive.unlink(missing_ok=True)
            raise ComponentError(f"分卷拼接后大小不匹配（期望 {size}）")
        actual = sha256_file(archive)
        if actual != sha:
            archive.unlink(missing_ok=True)
            raise ComponentError(f"分卷拼接后 SHA256 不匹配（期望 {sha[:12]}…，实际 {actual[:12]}…）")
        log(f"  ✓ 分卷校验通过: {archive.name} ({human(archive.stat().st_size)})")
        return archive

    def _download_part(self, cid: str, urls, dest: Path, num: int, total: int,
                       want_sha: str, want_size: int) -> None:
        candidates = urls if isinstance(urls, (list, tuple)) else [urls]
        last_error: Exception | None = None
        for url in candidates:
            offset = dest.stat().st_size if dest.is_file() else 0
            if want_size and offset > want_size:
                dest.unlink(missing_ok=True)
                offset = 0
            log(f"  下载分卷 {num}/{total}: {dest.name} <- {str(url)[:90]}" + (f"（续传 {human(offset)}）" if offset else ""))
            try:
                with self._open(url, offset) as resp:
                    total_bytes = int(resp.headers.get("Content-Length") or 0) + offset if resp.status == 206 else int(resp.headers.get("Content-Length") or 0)
                    mode = "ab" if offset and resp.status == 206 else "wb"
                    if mode == "wb":
                        offset = 0
                    with open(dest, mode) as out:
                        while True:
                            block = resp.read(CHUNK)
                            if not block:
                                break
                            out.write(block)
                if want_size and dest.stat().st_size != want_size:
                    dest.unlink(missing_ok=True)
                    raise ComponentError(f"分卷大小不匹配（期望 {want_size}，实际 {dest.stat().st_size}）")
                if want_sha:
                    actual = sha256_file(dest)
                    if actual != want_sha:
                        dest.unlink(missing_ok=True)
                        raise ComponentError(f"分卷 SHA256 不匹配（期望 {want_sha[:12]}…，实际 {actual[:12]}…）")
                log(f"  ✓ 分卷 {num} 校验通过 ({human(dest.stat().st_size)})")
                return
            except (urllib.error.URLError, ComponentError, OSError, TimeoutError) as exc:
                last_error = exc
                log(f"  ✗ 分卷 {num} 源失败: {exc}")
                continue
        raise ComponentError(f"分卷 {num} 所有源均失败: {last_error}")

    def install(self, cid: str, force: bool = False) -> None:
        comp = self.components[cid]
        target_rel = (comp.get("install") or {}).get("target") or f"runtime/{cid}"
        target = (self.root / target_rel).resolve()
        installed = self.installed["components"].get(cid)
        if installed and not force:
            log(f"  {cid} 已安装（{installed.get('version')}），跳过；--force 可重装")
            return
        archive = self.download(cid)
        log(f"  解压 {cid} -> {target}")
        if target.exists() and force:
            shutil.rmtree(target, ignore_errors=True)
        self._extract(archive, str((comp.get("install") or {}).get("format", "7z")).lower(), target)
        self.installed["components"][cid] = {
            "id": cid,
            "version": comp.get("version"),
            "sha256": comp.get("sha256"),
            "target": target_rel,
            "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        self._save_versions()
        log(f"  ✓ {cid} 安装完成")

    def repair(self, cid: str) -> None:
        comp = self.components[cid]
        installed = self.installed["components"].get(cid)
        if not installed:
            log(f"  {cid} 未安装，执行安装")
            self.install(cid, force=True)
            return
        archive, _ = self._cache_paths(comp)
        if not archive.is_file() or sha256_file(archive) != comp["sha256"].lower():
            archive.unlink(missing_ok=True)
            self.download(cid)
        self.install(cid, force=True)

    def uninstall(self, cid: str) -> None:
        dependents = [
            other for other, entry in self.installed["components"].items()
            if other != cid and cid in (self.components.get(other, {}).get("depends_on") or [])
        ]
        if dependents:
            raise ComponentError(f"{cid} 被依赖，先卸载: {', '.join(dependents)}")
        target_rel = (self.components.get(cid) or {}).get("install", {}).get("target") or f"runtime/{cid}"
        target = (self.root / target_rel).resolve()
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        self.installed["components"].pop(cid, None)
        self._save_versions()
        log(f"  ✓ {cid} 已卸载")

    # ---------------- 展示 ----------------

    def table(self) -> list[dict]:
        rows = []
        for cid, comp in sorted(self.components.items()):
            row = {
                "id": cid,
                "version": comp.get("version"),
                "required": bool(comp.get("required")),
                "size": human(int(comp.get("size") or 0)),
                "state": "已安装" if cid in self.installed["components"] else "-",
            }
            rows.append(row)
        return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Möbius 组件下载器")
    parser.add_argument("--manifest", default="ops/l3/packs/fragments.json")
    parser.add_argument("--root", default=os.environ.get("MOBIUS_ROOT", "."))
    parser.add_argument("--cache", default="")
    parser.add_argument("--source-dir", default=os.environ.get("MOBIUS_SOURCE_DIR", ""))
    parser.add_argument("--sevenzip", default=DEFAULT_7Z)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--required", action="store_true", help="安装/更新全部必需组件")
    parser.add_argument("command", choices=["list", "install", "repair", "uninstall", "verify", "update"])
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args()

    dl = Downloader(
        Path(args.manifest), Path(args.root),
        Path(args.cache) if args.cache else None,
        sevenzip=args.sevenzip,
        source_dir=Path(args.source_dir) if args.source_dir else None,
    )

    if args.command == "list":
        for row in dl.table():
            mark = "*" if row["required"] else " "
            print(f" {mark} {row['id']:<16} {row['version']:<18} {row['size']:>10}  {row['state']}")
        return 0

    if args.command == "install":
        ids = list(args.ids)
        if args.required or not ids:
            ids = [cid for cid, c in dl.components.items() if c.get("required")]
        if not ids:
            print("用法: install <id...> | install --required", file=sys.stderr)
            return 2
        order = dl.resolve(ids)
        for cid in order:
            print(f"==> {cid}")
            dl.install(cid, force=args.force)
        return 0

    if args.command == "update":
        ids = args.ids or list(dl.installed["components"].keys())
        for cid in ids:
            print(f"==> update {cid}")
            dl.install(cid, force=True)
        return 0

    if args.command == "repair":
        for cid in args.ids:
            print(f"==> repair {cid}")
            dl.repair(cid)
        return 0

    if args.command == "uninstall":
        for cid in args.ids:
            dl.uninstall(cid)
        return 0

    if args.command == "verify":
        bad = 0
        for cid, entry in dl.installed["components"].items():
            target = dl.root / entry.get("target", "")
            comp = dl.components.get(cid) or {}
            ok = target.exists() and comp.get("sha256") == entry.get("sha256")
            print(f"  {'OK' if ok else 'BAD'}  {cid:<16} {target}")
            bad += 0 if ok else 1
        return 1 if bad else 0

    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ComponentError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1)
