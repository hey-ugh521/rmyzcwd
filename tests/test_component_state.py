"""tests/test_component_state.py — 组件安装状态机 + Downloader 集成（独立脚本）。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ops" / "l3"))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + ((" — " + detail) if detail else ""))


def make_manifest(root: Path, cid: str = "x") -> Path:
    manifest = {
        "schema": 1,
        "api_version": 1,
        "components": {
            cid: {
                "id": cid,
                "version": "1.0",
                "sha256": "a" * 64,
                "size": 1,
                "required": True,
                "urls": ["https://example.invalid/x.7z"],
                "install": {"type": "archive", "format": "7z", "target": f"runtime/{cid}"},
            }
        },
    }
    p = root / "manifest.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return p


def main() -> int:
    import component_state as cs

    # 1) 转移表全量
    bad = 0
    for old in cs.STATES:
        for new in cs.STATES:
            got = cs.transition_allowed(old, new)
            expect = old == new or new in cs.TRANSITIONS.get(old, set())
            if got != expect:
                bad += 1
    check("转移表全量一致", bad == 0, f"bad={bad}")
    check("非法转移识别", cs.transition_allowed("installed", "installing") is False)
    check("合法转移识别", cs.transition_allowed("downloading", "installing") is True)

    tmp = Path(tempfile.mkdtemp(prefix="component-state-"))
    try:
        sp = tmp / "component_states.json"
        st = cs.ComponentStateStore(sp)
        check("初始 absent", st.state_of("x") == "absent")

        # 2) 正常流程
        st.mark_downloading("x", "1.0", "a" * 64)
        check("downloading", st.state_of("x") == "downloading")
        st.mark_installing("x")
        check("installing", st.state_of("x") == "installing")
        st.mark_installed("x", "1.0", "a" * 64)
        check("installed", st.state_of("x") == "installed")
        data = json.loads(sp.read_text(encoding="utf-8"))
        ent = data["components"]["x"]
        check("持久化 attempts=1", ent.get("attempts") == 1, str(ent.get("attempts")))
        check("持久化 finished_at", bool(ent.get("finished_at")))

        # 3) 非法转移
        try:
            st.transition("x", "installing")
            check("installed->installing 应报错", False)
        except cs.StateTransitionError:
            check("installed->installing 报错", True)

        # 4) 失败 + 重试 attempts 递增
        st2 = cs.ComponentStateStore(tmp / "s2.json")
        st2.mark_downloading("y", "1.0", "")
        st2.mark_failed("y", "network down")
        check("failed 带错误", (st2.get("y") or {}).get("error") == "network down")
        st2.mark_downloading("y", "1.0", "")
        check("重试 attempts=2", int((st2.get("y") or {}).get("attempts") or 0) == 2)

        # 5) 崩溃恢复：stale downloading -> failed
        tmp2 = tmp / "stale.json"
        now = __import__("time").time()
        tmp2.write_text(json.dumps({"components": {"z": {
            "id": "z", "state": "installing", "updated_ts": now - 100,
            "updated_at": "old", "attempts": 1}}}), encoding="utf-8")
        st3 = cs.ComponentStateStore(tmp2, stale_seconds=10)
        check("stale installing 恢复为 failed", st3.state_of("z") == "failed", str(st3.get("z")))

        # 6) Downloader 集成
        import downloader as dlmod

        root = tmp / "root"
        root.mkdir()
        manifest = make_manifest(root)
        dl = dlmod.Downloader(manifest, root)
        check("Downloader 初始 absent", dl.state_store.state_of("x") == "absent")

        # 失败路径
        def boom(cid):
            raise dlmod.ComponentError("download boom")

        dl.download = boom  # type: ignore[method-assign]
        try:
            dl.install("x", force=True)
            check("失败安装应抛错", False)
        except dlmod.ComponentError:
            check("失败安装抛错", True)
        check("失败后状态 failed", dl.state_store.state_of("x") == "failed")
        check("失败原因记录", "download boom" in str((dl.state_store.get("x") or {}).get("error")))

        # 成功路径（假归档 + 假解压）
        fake_archive = root / "fake.7z"
        fake_archive.write_bytes(b"fake")
        dl.download = lambda cid: fake_archive  # type: ignore[method-assign]
        dl._extract = lambda archive, fmt, target: (target.mkdir(parents=True, exist_ok=True), (target / "ok.txt").write_text("ok"))  # type: ignore[method-assign]
        dl.install("x", force=True)
        check("成功安装 installed", dl.state_store.state_of("x") == "installed")
        check("versions.json 有记录", "x" in dl.installed["components"])
        check("目标目录存在", (root / "runtime" / "x").is_dir())
        row = [r for r in dl.table() if r["id"] == "x"][0]
        check("table 暴露状态", row.get("state") == "installed" and row.get("installed") is True, str(row))

        # 7) 卸载 -> absent
        dl.uninstall("x")
        check("卸载后 absent", dl.state_store.state_of("x") == "absent")
        check("versions 移除", "x" not in dl.installed["components"])

        # 8) 旧 versions 回填
        root2 = tmp / "root2"
        root2.mkdir()
        (root2 / "versions.json").write_text(json.dumps({"components": {"x": {
            "id": "x", "version": "1.0", "sha256": "a" * 64, "target": "runtime/x"}}}), encoding="utf-8")
        dl2 = dlmod.Downloader(make_manifest(root2), root2)
        check("versions 回填 installed", dl2.state_store.state_of("x") == "installed")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name in FAILED:
            print("  FAIL:", name)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
