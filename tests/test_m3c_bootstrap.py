"""M3c 安装器/首启 bootstrap 回归（静态契约，防止干净机器再缺件）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name if ok else (name, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def _test_extract_replaces_stale_target() -> bool:
    """旧 target 残树不能被 ignore_errors 掩盖成 WinError 5。"""
    import shutil
    import tempfile
    import zipfile

    sys.path.insert(0, str(ROOT))
    from ops.l3 import downloader  # noqa: E402

    class Dummy:
        sevenzip = str(ROOT / "ops" / "l3" / "definitely-missing-7z.exe")

    tmp = Path(tempfile.mkdtemp(prefix="m3c-extract-"))
    try:
        target = tmp / "runtime" / "python-langbot"
        (target / "Lib").mkdir(parents=True)
        (target / "Lib" / "old.txt").write_text("old", encoding="utf-8")
        archive = tmp / "pack.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("python-langbot/Lib/new.txt", "new")
        downloader.Downloader._extract(Dummy(), archive, "zip", target)
        return (target / "Lib" / "new.txt").is_file() and not (target / "Lib" / "old.txt").exists()
    except Exception as exc:  # noqa: BLE001
        print("  extract detail:", repr(exc))
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    launcher = (ROOT / "launcher" / "mobius_launcher.py").read_text(encoding="utf-8")
    check("CLI 暴露 --headless bootstrap", '"install-components", "bootstrap"' in launcher)
    check("bootstrap 路由存在", 'if args.headless == "bootstrap"' in launcher)
    check("bootstrap 注入本地 7z", 'MOBIUS_7Z' in launcher)
    check("component_states 读取使用 .read()",
          'component_states.json"), encoding="utf-8").read()' in launcher)
    check("launcher 本机鉴权头存在", "_core_auth_headers" in launcher)

    downloader = (ROOT / "ops" / "l3" / "downloader.py").read_text(encoding="utf-8")
    check("downloader 对 GBK 控制台兜底", "UnicodeEncodeError" in downloader)
    check("downloader 默认使用随包 7z", 'with_name("7z.exe")' in downloader)
    check("旧组件残树可原子替换", _test_extract_replaces_stale_target())

    iss = (ROOT / "installer" / "mobius.iss").read_text(encoding="utf-8")
    check("默认安装到 localappdata", "DefaultDirName={localappdata}\\Programs\\Mobius" in iss)
    check("安装器包含 7z.exe", "ops\\l3\\7z.exe" in iss)
    check("安装器包含 7z.dll", "ops\\l3\\7z.dll" in iss)
    check("安装器 postinstall 运行 bootstrap", '"--headless bootstrap"' in iss)

    cfg = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
    pg = (cfg.get("services") or {}).get("postgres") or {}
    check("随包 VC 运行库存在", (ROOT / "vendor" / "msvc-runtime" / "vcruntime140.dll").is_file())
    check("bootstrap 补齐 PG 运行库", "_ensure_pg_runtime" in launcher)
    check("安装器包装 VC 运行库", "vendor\\msvc-runtime" in iss)
    check("PG 组件路径与归档根一致",
          pg.get("exe") == "${RUNTIME}/postgres/bin/pg_ctl.exe", str(pg.get("exe")))
    check("数据目录外置", (cfg.get("paths") or {}).get("DATA") == "${LOCALAPPDATA}/Mobius/data")

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
