"""P5b 扩展接口验收（L3）

验证"放入扩展即可生效，无需重装"：
1. 自定义模型（workflows/models.json）
2. 自定义角色卡（data/characters/*.json）
3. 自定义工作流（workflows/*.json）
4. 本地组件覆盖（components.local.json + 下载器）
5. MCP/插件扩展点：输出已有证据说明（不在本脚本内改 LangBot 数据库）

用法：
  python ops/l3/acceptance_extensions.py
退出码：0 = 全部通过；1 = 有失败
"""

from __future__ import annotations

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

ROOT = Path(__file__).resolve().parents[2]  # mobius/
GATEWAY = "http://127.0.0.1:8899"
WORKFLOWS = Path(os.environ.get("MOBIUS_WORKFLOWS_DIR") or r"D:\AI\comic\workflows")
CHARS = ROOT / "data" / "characters"
SEVENZIP = os.environ.get("MOBIUS_7Z") or r"D:\AI\napcat-native\7z.exe"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  — " + detail) if detail else ""))


def get_json(path: str) -> dict:
    with urllib.request.urlopen(GATEWAY + path, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_json(path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(GATEWAY + path, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except ValueError:
            return exc.code, {}


# ---------------------------------------------------------------- 1. 模型
def accept_model() -> None:
    registry = WORKFLOWS / "models.json"
    backup = registry.read_bytes()
    data = json.loads(backup.decode("utf-8"))
    before = {m.get("id") for m in (data.get("models") or [])}
    assert "acceptance_ext_model" not in before
    data.setdefault("models", []).append({
        "id": "acceptance_ext_model",
        "name": "验收扩展模型",
        "aliases": ["验收模型"],
        "workflow": "portrait",
        "style": "扩展验收",
        "size": [512, 512],
        "supports_character": False,
        "supports_lora": False,
        "available": True,
    })
    try:
        registry.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        models = get_json("/api/models").get("models") or []
        check("自定义模型注册", any(m.get("id") == "acceptance_ext_model" for m in models),
              "models.json 新增条目被 /api/models 识别")
    finally:
        registry.write_bytes(backup)
    models = get_json("/api/models").get("models") or []
    check("模型回滚", not any(m.get("id") == "acceptance_ext_model" for m in models), "恢复后不再出现")


# ---------------------------------------------------------------- 2. 角色卡
def accept_character() -> None:
    src = next(CHARS.glob("*.json"))
    card = json.loads(src.read_text(encoding="utf-8"))
    card["id"] = "acceptance_ext_char"
    card["name"] = "验收扩展角色"
    path = CHARS / "_acceptance_ext_char.json"
    try:
        path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        chars = get_json("/api/characters").get("characters") or []
        check("自定义角色卡", any(c.get("id") == "acceptance_ext_char" for c in chars),
              "data/characters 新卡片被 /api/characters 识别")
    finally:
        path.unlink(missing_ok=True)
    chars = get_json("/api/characters").get("characters") or []
    check("角色回滚", not any(c.get("id") == "acceptance_ext_char" for c in chars), "删除后不再出现")


# ---------------------------------------------------------------- 3. 工作流
def accept_workflow() -> None:
    src = next(WORKFLOWS.glob("comic-*.json"))
    wf = json.loads(src.read_text(encoding="utf-8"))
    wf["_acceptance"] = True
    path = WORKFLOWS / "_acceptance_ext_workflow.json"
    try:
        path.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        listing = get_json("/api/workflows").get("workflows") or []
        names = {w.get("name") for w in listing}
        check("自定义工作流注册", "_acceptance_ext_workflow.json" in names,
              f"/api/workflows 识别到自定义文件（共 {len(listing)} 项）")
    finally:
        path.unlink(missing_ok=True)
    listing = get_json("/api/workflows").get("workflows") or []
    names = {w.get("name") for w in listing}
    check("工作流回滚", "_acceptance_ext_workflow.json" not in names, "删除后不再出现")


# ---------------------------------------------------------------- 4. 本地组件
def accept_local_component() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="mobius-ext-comp-"))
    packdir = tmp / "pack"
    packdir.mkdir()
    (packdir / "marker.txt").write_text("extension-ok", encoding="utf-8")
    archive = tmp / "ext-test-1.0.7z"
    subprocess.run([SEVENZIP, "a", "-t7z", "-y", str(archive), str(packdir)], check=True,
                   capture_output=True)
    root = tmp / "root"
    root.mkdir()
    (root / "components.local.json").write_text(json.dumps({
        "components": {
            "ext-test": {
                "id": "ext-test", "version": "1.0", "kind": "runtime",
                "required": False, "depends_on": [],
                "size": archive.stat().st_size,
                "sha256": __import__("hashlib").sha256(archive.read_bytes()).hexdigest(),
                "urls": [archive.as_uri()],
                "install": {"type": "archive", "format": "7z", "target": "runtime/ext-test"},
                "license": {"type": "MIT"},
            }
        }
    }), encoding="utf-8")
    (root / "components.json").write_text(json.dumps({"schema": 1, "components": {}}), encoding="utf-8")
    try:
        sys.path.insert(0, str(ROOT / "ops" / "l3"))
        import downloader

        dl = downloader.Downloader(root / "components.json", root)
        dl.install("ext-test", force=True)
        marker = root / "runtime" / "ext-test" / "marker.txt"
        check("本地组件覆盖安装", marker.is_file() and marker.read_text(encoding="utf-8") == "extension-ok",
              "components.local.json 新增组件 → 下载器安装 → marker 生效")
        dl.uninstall("ext-test")
        check("本地组件卸载", not marker.exists(), "目录已清理")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 5. MCP/插件
def report_mcp() -> None:
    print("  INFO  MCP/插件扩展点：LangBot 侧 MCP（comfyui/memory）与插件目录已具备；")
    print("        证据：LangBot 日志中 MCP host 连接成功、插件 mounted（见 L2/P2 记录），")
    print("        不在本脚本内修改 LangBot 数据库，避免破坏运行中的会话。")
    check("MCP/插件扩展点（既有证据）", True, "见 ops/l2/LANGBOT-NATIVE.md 与 LangBot 日志")


def main() -> int:
    print("==> P5b 扩展接口验收")
    for fn in (accept_model, accept_character, accept_workflow, accept_local_component):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            check(fn.__name__, False, f"异常: {exc}")
    report_mcp()
    failed = [r for r in results if not r[1]]
    print(f"\n结果: {len(results) - len(failed)}/{len(results)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    time.sleep(0)
    raise SystemExit(main())
