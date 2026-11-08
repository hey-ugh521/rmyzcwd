"""回归测试：组件 manifest 生成 / 漂移校验（ops/l3/manifest_check.py）。

背景 bug（2026-11-08）：
  聚合逻辑内联在 build_release.ps1 的 here-string 里，与 ops/l3/packs/*.json
  各写一份。源片段换成单包后，聚合结果仍可能沿用旧多卷 parts/urls（旧分卷清单
  复活）。本测试覆盖聚合保留规则、drift 比较、原子写幂等与组件包校验。

运行：
  D:\\Anaconda3\\envs\\open-webui\\python.exe tests\\test_manifest_drift.py

说明：
  - 纯标准库；全部用临时目录 + 假文件，不读真实 packs/fragments、不联网、
    不修改仓库文件。
  - 覆盖：聚合确定性与 required/排序/release；sha 一致保留旧 parts+urls、
    sha 变化不保留；drift（sha/required/parts 数量/组件增删/release）；
    write 幂等（相同内容不写/mtime 不变）与确定性；verify_packs
    （假 .7z、分卷 .001/.002 拼接 sha、缺失、size/sha 不符）。
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "ops" / "l3" / "manifest_check.py"

spec = importlib.util.spec_from_file_location("mobius_manifest_check", SCRIPT)
mc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mc)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(name)
        print(f"  [PASS] {name}")
    else:
        FAILED.append((name, detail))
        print(f"  [FAIL] {name}  {detail}")


def frag(cid: str, sha: str = SHA_A, version: str = "1.0-20261108",
         urls=None, parts=None, size: int = 1024, name: str | None = None) -> dict:
    """构造一个源片段（模拟 ops/l3/packs/*.json）。"""
    d = {
        "id": cid,
        "name": name or cid,
        "kind": "runtime",
        "version": version,
        "required": False,
        "depends_on": [],
        "size": size,
        "sha256": sha,
        "urls": list(urls) if urls is not None else [f"https://example.invalid/{cid}-{version}.7z"],
        "install": {"target": f"runtime/{cid}", "format": "7z", "type": "archive"},
        "license": {"type": "mixed", "note": ""},
    }
    if parts is not None:
        d["parts"] = copy.deepcopy(parts)
    return d


def part(name: str, sha: str, size: int, index: int) -> dict:
    return {"index": index, "name": name, "size": size, "sha256": sha,
            "url": f"https://example.invalid/{name}"}


def manifest(*frags: dict, release=None, launcher_min_version: str = "0.2.0") -> dict:
    return {
        "schema": 1,
        "api_version": 1,
        "launcher_min_version": launcher_min_version,
        "release": release,
        "components": {f["id"]: f for f in frags},
    }


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- 1. 聚合

def test_aggregate() -> None:
    print("\n[1] 聚合：结构 / required / 排序 / release / 确定性")
    packs = [frag("python-gateway", SHA_B), frag("python-core", SHA_A),
             frag("extra-comp", SHA_C, version="9.9")]
    m = mc.aggregate(packs)

    check("顶层字段固定且顺序稳定",
          list(m) == ["schema", "api_version", "launcher_min_version", "release", "components"],
          str(list(m)))
    check("schema/api_version 固定", m["schema"] == 1 and m["api_version"] == 1, str(m["schema"]))
    check("launcher_min_version 默认 0.2.0", m["launcher_min_version"] == "0.2.0",
          repr(m["launcher_min_version"]))
    check("release 默认 None", m["release"] is None, repr(m["release"]))
    check("components 按 id 排序",
          list(m["components"]) == ["extra-comp", "python-core", "python-gateway"],
          str(list(m["components"])))
    check("REQUIRED 默认集合正确",
          set(mc.REQUIRED) == {"python-core", "python-gateway", "python-memory", "langbot",
                               "python-langbot", "postgres", "napcat-code"},
          str(mc.REQUIRED))
    check("required 按 REQUIRED 覆盖（必需=True）",
          m["components"]["python-core"]["required"] is True
          and m["components"]["python-gateway"]["required"] is True)
    check("required 按 REQUIRED 覆盖（可选=False）",
          m["components"]["extra-comp"]["required"] is False)

    custom = mc.aggregate(packs, launcher_min_version="0.3.0", release={"version": "0.2.0"},
                          required=("extra-comp",))
    check("launcher_min_version 可配置", custom["launcher_min_version"] == "0.3.0")
    check("release 可配置", custom["release"] == {"version": "0.2.0"}, repr(custom["release"]))
    check("required 参数可覆盖",
          custom["components"]["extra-comp"]["required"] is True
          and custom["components"]["python-core"]["required"] is False)

    # 确定性：输入顺序不同、重复聚合 -> 字节一致
    a1 = mc.canonical_json(mc.aggregate(packs))
    a2 = mc.canonical_json(mc.aggregate(list(reversed(packs))))
    check("聚合确定性（与输入顺序无关）", a1 == a2)
    check("输出以换行结尾", a1.endswith("\n"), repr(a1[-3:]))
    check("缩进为 2 空格", a1.splitlines()[1].startswith("  \""), repr(a1.splitlines()[1]))

    # ensure_ascii=False
    m_cn = mc.aggregate([frag("core-cn", name="核心运行时（测试）")])
    text_cn = mc.canonical_json(m_cn)
    check("中文不被转义（ensure_ascii=False）", "核心运行时（测试）" in text_cn)
    check("无 \\u 转义", "\\u6838" not in text_cn and "\\u8fd0" not in text_cn)

    # 不修改输入
    packs_before = copy.deepcopy(packs)
    mc.aggregate(packs)
    check("聚合不修改源片段", packs == packs_before)

    # load_packs：排除 fragments.json、按 id 排序、缺 id 报错
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "b.json").write_text(json.dumps(frag("bbb", SHA_B)), encoding="utf-8")
        (d / "a.json").write_text(json.dumps(frag("aaa", SHA_A)), encoding="utf-8")
        (d / "fragments.json").write_text("{}", encoding="utf-8")
        loaded = mc.load_packs(d)
        check("load_packs 排除 fragments.json", len(loaded) == 2, str(len(loaded)))
        check("load_packs 按 id 排序", [p["id"] for p in loaded] == ["aaa", "bbb"],
              str([p["id"] for p in loaded]))
        (d / "bad.json").write_text(json.dumps({"name": "无 id"}), encoding="utf-8")
        try:
            mc.load_packs(d)
            check("load_packs 缺 id 报错", False, "未抛异常")
        except ValueError:
            check("load_packs 缺 id 报错", True)


# ---------------------------------------------------------------- 2. 保留规则

def test_retention() -> None:
    print("\n[2] URL/parts 保留规则（仅 sha256 一致时沿用 existing）")
    old_parts = [part("demo-20261108.7z.001", "1" * 64, 100, 0),
                 part("demo-20261108.7z.002", "2" * 64, 200, 1)]
    old_urls = ["https://api.github.com/repos/o/r/releases/assets/123",
                "https://api.github.com/repos/o/r/releases/assets/124"]

    same_sha = manifest({
        "id": "demo", "name": "demo", "kind": "runtime", "version": "20261108",
        "required": True, "depends_on": [], "size": 300, "sha256": SHA_A,
        "urls": old_urls, "parts": old_parts,
    })
    source_parts = [part("demo-20261108.7z.001", "9" * 64, 100, 0)]
    source = [frag("demo", SHA_A, version="20261108", size=300,
                   urls=["https://src.invalid/demo-20261108.7z.001"],
                   parts=source_parts)]
    gen = mc.aggregate(source, existing=same_sha)["components"]["demo"]
    check("sha 一致：保留 existing parts", gen["parts"] == old_parts, str(gen.get("parts")))
    check("sha 一致：保留 existing urls", gen["urls"] == old_urls, str(gen.get("urls")))
    check("sha 一致：保留旧分卷（不被源片段覆盖）", len(gen["parts"]) == 2)

    # sha 变化：不得沿用旧分卷清单（防旧分卷复活）
    changed = manifest({
        "id": "demo", "name": "demo", "kind": "runtime", "version": "20261108",
        "required": True, "depends_on": [], "size": 300, "sha256": SHA_B,
        "urls": old_urls, "parts": old_parts,
    })
    gen2 = mc.aggregate(source, existing=changed)["components"]["demo"]
    check("sha 变化：parts 用源片段", gen2["parts"] == source_parts,
          str(gen2.get("parts")))
    check("sha 变化：urls 用源片段", gen2["urls"] == source[0]["urls"],
          str(gen2.get("urls")))
    check("sha 变化：旧分卷清单未复活", len(gen2["parts"]) == 1, str(len(gen2["parts"])))

    # 单包同理
    single_old = manifest({
        "id": "python-core", "name": "python-core", "kind": "runtime",
        "version": "1.0", "required": True, "depends_on": [], "size": 10,
        "sha256": SHA_A, "urls": ["https://api.github.com/repos/o/r/releases/assets/9"],
    })
    single_src = [frag("python-core", SHA_A, version="1.0", size=10,
                       urls=["https://src.invalid/python-core-1.0.7z"])]
    g1 = mc.aggregate(single_src, existing=single_old)["components"]["python-core"]
    check("单包 sha 一致：保留 existing urls",
          g1["urls"] == ["https://api.github.com/repos/o/r/releases/assets/9"], str(g1["urls"]))
    check("单包：不凭空引入 parts", "parts" not in g1)

    single_src_changed = [frag("python-core", SHA_C, version="1.0", size=10,
                               urls=["https://src.invalid/new.7z"])]
    g2 = mc.aggregate(single_src_changed, existing=single_old)["components"]["python-core"]
    check("单包 sha 变化：urls 用源片段", g2["urls"] == ["https://src.invalid/new.7z"],
          str(g2["urls"]))

    # existing 有 parts、源片段已是单包且 sha 变化 -> 不保留旧 parts
    old_split = manifest({
        "id": "demo", "name": "demo", "kind": "runtime", "version": "20261108",
        "required": False, "depends_on": [], "size": 300, "sha256": SHA_B,
        "urls": old_urls, "parts": old_parts,
    })
    single_demo = [frag("demo", SHA_C, version="20261108", size=300)]
    g3 = mc.aggregate(single_demo, existing=old_split)["components"]["demo"]
    check("切换单包后旧 parts 不复活", "parts" not in g3, str(g3.get("parts")))

    # existing 无此组件 -> 全部用源片段
    g4 = mc.aggregate(source, existing={})["components"]["demo"]
    check("无 existing 时用源片段 parts/urls",
          g4["parts"] == source_parts and g4["urls"] == source[0]["urls"])


# ---------------------------------------------------------------- 3. drift

def test_diff() -> None:
    print("\n[3] diff_manifests：sha / required / parts / 增删 / release / urls 脱敏")
    parts2 = [part("demo-20261108.7z.001", "1" * 64, 100, 0),
              part("demo-20261108.7z.002", "2" * 64, 200, 1)]
    base = mc.aggregate([frag("demo", SHA_A, parts=parts2), frag("python-core", SHA_B)])
    same = mc.aggregate([frag("demo", SHA_A, parts=parts2), frag("python-core", SHA_B)],
                        existing=base)
    check("相同 manifest 无差异", mc.diff_manifests(base, same) == [],
          str(mc.diff_manifests(base, same)))

    def drift(mutator) -> str:
        g = copy.deepcopy(base)
        mutator(g)
        return "\n".join(mc.diff_manifests(base, g))

    text = drift(lambda g: g["components"]["python-core"].update(sha256=SHA_C))
    check("发现 sha256 变化", "python-core" in text and "sha256" in text, text)
    text = drift(lambda g: g["components"]["demo"].update(required=True))
    check("发现 required 变化", "required" in text, text)
    text = drift(lambda g: g["components"]["demo"]["parts"].append(
        part("demo-20261108.7z.003", "3" * 64, 300, 2)))
    check("发现 parts 数量变化", "parts" in text and "数量" in text, text)
    text = drift(lambda g: g["components"]["demo"]["parts"][0].update(sha256="f" * 64))
    check("发现分卷 sha 变化", "parts" in text and "sha" in text.lower(), text)
    text = drift(lambda g: g["components"].pop("python-core"))
    check("发现组件仅存在于 existing", "仅存在于 existing" in text and "python-core" in text, text)
    text = drift(lambda g: g["components"].update(
        {"extra": frag("extra", SHA_C)}))
    check("发现组件新增", "仅存在于 generated" in text and "extra" in text, text)
    text = drift(lambda g: g.update(release={"version": "0.2.0"}))
    check("发现 release 变化", "顶层 release" in text, text)
    text = drift(lambda g: g.update(launcher_min_version="0.9.9"))
    check("发现 launcher_min_version 变化", "launcher_min_version" in text, text)
    text = drift(lambda g: g["components"]["python-core"].update(
        urls=["https://other.invalid/x.7z"]))
    check("发现 urls 变化", "urls" in text, text)

    # URL 不打印密钥（去查询串）+ 长 URL 截断
    g = copy.deepcopy(base)
    g["components"]["python-core"]["urls"] = ["https://example.invalid/pkg.7z?token=SUPERSECRET"]
    text = "\n".join(mc.diff_manifests(base, g))
    check("drift 不泄露 URL token", "SUPERSECRET" not in text, text)
    long_url = "https://example.invalid/" + "x" * 300 + ".7z"
    g2 = copy.deepcopy(base)
    g2["components"]["python-core"]["urls"] = [long_url]
    text = "\n".join(mc.diff_manifests(base, g2))
    check("长 URL 被截断", long_url not in text and "…" in text)


# ---------------------------------------------------------------- 4. write

def test_write() -> None:
    print("\n[4] write：确定性 / 原子写 / 内容相同不写")
    packs = [frag("python-core", SHA_A), frag("python-gateway", SHA_B)]
    m = mc.aggregate(packs)

    check("canonical_json 确定性", mc.canonical_json(m) == mc.canonical_json(copy.deepcopy(m)))

    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "packs"
        path = d / "fragments.json"
        check("首次写入返回 True", mc.write_manifest(path, m) is True)
        check("文件已创建", path.is_file())
        text = path.read_text(encoding="utf-8")
        check("写入内容即 canonical", text == mc.canonical_json(m))
        check("无 CRLF（确定性）", "\r" not in text)
        check("无临时文件残留", not list(d.glob("*.tmp")), str(list(d.glob("*.tmp"))))

        fixed = 1_700_000_000
        os.utime(path, (fixed, fixed))
        before = path.stat().st_mtime_ns
        check("相同内容再次写入返回 False", mc.write_manifest(path, m) is False)
        check("相同内容不写（mtime 不变）", path.stat().st_mtime_ns == before,
              f"{before} -> {path.stat().st_mtime_ns}")

        # 语义相同、键顺序不同 -> 仍不写
        reordered = json.loads(text)
        reordered = {k: reordered[k] for k in reversed(list(reordered))}
        os.utime(path, (fixed, fixed))
        before = path.stat().st_mtime_ns
        check("语义相同（仅键序不同）不写", mc.write_manifest(path, reordered) is False)
        check("语义相同不写（mtime 不变）", path.stat().st_mtime_ns == before)

        # 内容变化 -> 写
        changed = copy.deepcopy(m)
        changed["components"]["python-core"]["sha256"] = SHA_C
        check("内容变化时返回 True", mc.write_manifest(path, changed) is True)
        check("内容变化已落盘",
              json.loads(path.read_text(encoding="utf-8"))["components"]["python-core"]["sha256"] == SHA_C)

        # 原文件损坏也能重写
        path.write_text("{not json", encoding="utf-8")
        check("损坏文件可被重写", mc.write_manifest(path, m) is True)
        check("重写后为合法 JSON", json.loads(path.read_text(encoding="utf-8")) == m)

    # run_write/run_check 端到端（临时仓库布局；放齐 REQUIRED 源片段避免缺失告警）
    with tempfile.TemporaryDirectory() as td:
        packs_dir = Path(td) / "ops" / "l3" / "packs"
        packs_dir.mkdir(parents=True)
        for cid in mc.REQUIRED:
            (packs_dir / f"{cid}-1.0.json").write_text(
                json.dumps(frag(cid, sha256(cid.encode())), ensure_ascii=False),
                encoding="utf-8")
        w1 = mc.run_write(td)
        check("run_write 首次写入",
              w1["written"] is True and w1["component_count"] == len(mc.REQUIRED), str(w1))
        w2 = mc.run_write(td)
        check("run_write 二次幂等", w2["written"] is False, str(w2))
        c1 = mc.run_check(td)
        check("run_check 无漂移", c1["ok"] is True and c1["drift"] == [], str(c1["drift"]))
        # 改源片段 sha -> 漂移
        (packs_dir / "python-core-1.0.json").write_text(
            json.dumps(frag("python-core", SHA_C), ensure_ascii=False), encoding="utf-8")
        c2 = mc.run_check(td)
        check("run_check 检出漂移", c2["ok"] is False and any("sha256" in d for d in c2["drift"]),
              str(c2["drift"]))
        # CLI 退出码：有漂移=1，无漂移=0
        proc = subprocess.run([sys.executable, str(SCRIPT), "--root", str(td), "check", "--json"],
                              capture_output=True, text=True, encoding="utf-8")
        check("CLI check 有漂移 exit 1", proc.returncode == 1,
              f"rc={proc.returncode} err={proc.stderr[:200]}")
        try:
            payload = json.loads(proc.stdout)
            check("CLI --json 可解析", isinstance(payload.get("drift"), list), proc.stdout[:120])
        except Exception as exc:  # noqa: BLE001
            check("CLI --json 可解析", False, f"{exc}: {proc.stdout[:120]}")
        proc2 = subprocess.run([sys.executable, str(SCRIPT), "--root", str(td), "write"],
                               capture_output=True, text=True, encoding="utf-8")
        check("CLI write exit 0", proc2.returncode == 0,
              f"rc={proc2.returncode} err={proc2.stderr[:200]}")
        proc3 = subprocess.run([sys.executable, str(SCRIPT), "--root", str(td), "check"],
                               capture_output=True, text=True, encoding="utf-8")
        check("CLI check 无漂移 exit 0", proc3.returncode == 0,
              f"rc={proc3.returncode} out={proc3.stdout[-200:]}")


# ---------------------------------------------------------------- 5. verify_packs

def test_verify_packs() -> None:
    print("\n[5] verify_packs：单包 / 分卷拼接 sha / 缺失 / size/sha 不符")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)

        # 单包
        data = b"hello mobius" * 100
        name = "python-core-1.0.7z"
        (d / name).write_bytes(data)
        comps = {"python-core": {"id": "python-core", "version": "1.0", "size": len(data),
                                 "sha256": sha256(data)}}
        res = mc.verify_packs(comps, d)
        check("单包校验通过", res["ok"] is True and res["checked"] == 1, str(res))
        check("单包缺失时报告",
              "缺少组件包" in "\n".join(mc.verify_packs(
                  {"x": {"id": "x", "version": "1.0", "sha256": SHA_A}}, d)["errors"]))
        bad = mc.verify_packs({"python-core": {"id": "python-core", "version": "1.0",
                                               "size": len(data) + 1,
                                               "sha256": "f" * 64}}, d)
        joined = "\n".join(bad["errors"])
        check("单包 size 不符被检出", "大小不符" in joined, joined)
        check("单包 sha256 不符被检出", "sha256 不符" in joined.lower(), joined)

        # 分卷：.001/.002 拼接后的整体 sha
        b1 = b"A" * 1000
        b2 = b"B" * 2500
        p1, p2 = "python-memory-2.0.7z.001", "python-memory-2.0.7z.002"
        (d / p1).write_bytes(b1)
        (d / p2).write_bytes(b2)
        split = {"python-memory": {
            "id": "python-memory", "version": "2.0", "size": len(b1) + len(b2),
            "sha256": sha256(b1 + b2),
            "parts": [part(p1, sha256(b1), len(b1), 0), part(p2, sha256(b2), len(b2), 1)],
        }}
        res = mc.verify_packs(split, d)
        check("分卷逐卷 + 拼接整体 sha 通过", res["ok"] is True and res["checked"] == 2, str(res))

        # 整体 sha 不符（逐卷都对）
        wrong_overall = copy.deepcopy(split)
        wrong_overall["python-memory"]["sha256"] = "0" * 64
        joined = "\n".join(mc.verify_packs(wrong_overall, d)["errors"])
        check("分卷拼接整体 sha 不符被检出", "整体 sha256" in joined, joined)

        # 缺分卷
        (d / p2).unlink()
        missing = mc.verify_packs(split, d)
        check("缺少分卷被检出", missing["ok"] is False
              and "缺少分卷" in "\n".join(missing["missing"]), str(missing))
        # 恢复
        (d / p2).write_bytes(b2)

        # 分卷 size 不符
        (d / p2).write_bytes(b2 + b"X")
        joined = "\n".join(mc.verify_packs(split, d)["errors"])
        check("分卷 size 不符被检出", "大小不符" in joined, joined)
        (d / p2).write_bytes(b2)

        # 分卷 sha 不符（同长度内容不同）
        (d / p1).write_bytes(b"C" * 1000)
        res = mc.verify_packs(split, d)
        check("分卷 sha256 不符被检出",
              res["ok"] is False and "sha256 不符" in "\n".join(res["errors"]).lower(),
              str(res["errors"]))
        (d / p1).write_bytes(b1)

        # 接受整份 manifest
        full = {"components": {"python-memory": split["python-memory"]}}
        res = mc.verify_packs(full, d)
        check("接受整份 manifest 输入", res["ok"] is True, str(res))

        # 空组件集合
        empty = mc.verify_packs({}, d)
        check("空组件集合 OK", empty["ok"] is True and empty["checked"] == 0, str(empty))


def test_release_inherit() -> None:
    """回归：finalize 写入的 release 元数据，不能被下一次 aggregate/write 抹掉。"""
    import tempfile, shutil, pathlib

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="manifest-release-"))
    try:
        packs_dir = tmp / "packs"
        packs_dir.mkdir()
        (packs_dir / "x-1.json").write_text(
            json.dumps({"id": "x", "version": "1", "sha256": "a" * 64, "size": 1, "urls": ["u"]}),
            encoding="utf-8",
        )
        gen = mc.aggregate(mc.load_packs(packs_dir),
                           existing={"release": {"repo": "o/r", "tag": "t"}, "components": {}})
        check("aggregate 继承 existing.release",
              gen.get("release") == {"repo": "o/r", "tag": "t"}, str(gen.get("release")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("==> 组件 manifest 生成 / 漂移校验测试")
    test_aggregate()
    test_retention()
    test_diff()
    test_write()
    test_verify_packs()
    test_release_inherit()
    print(f"\n结果: {len(PASSED)}/{len(PASSED) + len(FAILED)} 通过")
    if FAILED:
        for name, detail in FAILED:
            print(f"  FAIL: {name} {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
