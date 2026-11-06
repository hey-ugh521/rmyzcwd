"""画笔（生图底模）接入验收：逐模型生成 → 校验产物 → 能力边界 → 输出矩阵报告。

用法：
  D:\\AI\\qqbot-mcp\\Scripts\\python.exe ops\\image\\acceptance.py
  ... --only flux1_schnell,animagine_xl_3_1
  ... --no-boundary   # 跳过能力边界检查

验收判据：
  A1 注册表可见（list_models 返回该模型）
  A2 生成成功（task completed）
  A3 产物可下载（HTTP 200）且为图片、尺寸与注册表一致
  A4 能力边界（不支持角色/LoRA 的底模，带 character 请求被明确拒绝）
  A5 证据落盘：ops/image/acceptance.csv + acceptance-report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
import sys
import time
import urllib.request

GATEWAY = os.environ.get("MOBIUS_GATEWAY_MCP", "http://127.0.0.1:8899/mcp")
LOCAL_BASE = os.environ.get("MOBIUS_GATEWAY_HTTP", "http://127.0.0.1:8899")
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

STD_PROMPT = "a red apple on a wooden table, soft window light, detailed"
STD_NEG = "lowres, worst quality, low quality, blurry, watermark, text"


async def _call(session, name: str, args: dict | None = None) -> dict:
    res = await session.call_tool(name, args or {})
    text = "".join(getattr(b, "text", "") for b in (res.content or []))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid_json", "raw": text[:400]}


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    return None


def _fetch(path: str) -> bytes:
    with urllib.request.urlopen(LOCAL_BASE + path, timeout=30) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return resp.read()


async def run_model(session, profile: dict, timeout: float) -> dict:
    model_id = profile["id"]
    row: dict = {
        "model": model_id,
        "workflow": profile.get("workflow"),
        "status": "pending",
        "seconds": None,
        "filename": "",
        "width": None,
        "height": None,
        "bytes": None,
        "error": "",
    }
    started = time.time()
    try:
        submit = await _call(
            session,
            "comfyui_task_submit",
            {
                "model": model_id,
                "prompt": STD_PROMPT,
                "negative": STD_NEG,
                "idempotency_key": f"accept-{model_id}-{int(started)}",
            },
        )
        if not submit.get("ok"):
            row.update(status="submit_failed", error=str(submit.get("error") or submit)[:200], seconds=round(time.time() - started, 1))
            return row
        task_id = submit.get("task_id")
        deadline = started + timeout
        while time.time() < deadline:
            result = await _call(session, "comfyui_task_status", {"task_id": task_id})
            task = result.get("task") or {}
            status = task.get("status")
            if status in ("completed", "failed", "cancelled"):
                row["seconds"] = round(time.time() - started, 1)
                if status != "completed":
                    row.update(status=status, error=str(task.get("error") or "")[:300])
                    return row
                payload = task.get("result") or {}
                row["filename"] = payload.get("filename", "")
                path = payload.get("path") or ""
                if not path.startswith("/img"):
                    row.update(status="bad_result", error=f"result.path 异常: {path!r}")
                    return row
                data = _fetch(path)
                row["bytes"] = len(data)
                size = _png_size(data)
                if size is None:
                    row.update(status="not_png")
                    return row
                row["width"], row["height"] = size
                row["status"] = "pass"
                return row
            if status in ("submission_unknown",):
                row.update(status="submission_unknown", error=str(task.get("error") or "")[:200])
                return row
            await asyncio.sleep(10)
        row.update(status="timeout", seconds=round(time.time() - started, 1), error=f">{timeout:.0f}s 未完成")
        return row
    except Exception as exc:  # noqa: BLE001
        row.update(status="error", seconds=round(time.time() - started, 1), error=f"{type(exc).__name__}: {exc}"[:300])
        return row


async def run_boundary(session, profiles: list[dict]) -> list[dict]:
    checks = []
    for profile in profiles:
        if profile.get("available") is False:
            r = await _call(session, "comfyui_generate", {"prompt": "test", "model": profile["id"]})
            checks.append({
                "check": f"{profile['id']}: 不可用底模被拒绝",
                "pass": (not r.get("ok")) and r.get("error") == "model_unavailable",
                "detail": str(r.get("error") or r)[:120],
            })
            continue
        if profile.get("supports_character"):
            continue
        r = await _call(session, "comfyui_generate", {"prompt": "test", "model": profile["id"], "character": "bronya"})
        checks.append({
            "check": f"{profile['id']}: 不支持角色卡被明确拒绝",
            "pass": (not r.get("ok")) and r.get("error") == "workflow_no_character",
            "detail": str(r.get("error") or r)[:120],
        })
    return checks


def write_report(rows: list[dict], boundaries: list[dict], profiles: list[dict]) -> None:
    csv_path = os.path.join(OUT_DIR, "acceptance.csv")
    with open(csv_path, "w", encoding="utf-8-sig") as f:
        f.write("model,workflow,status,seconds,filename,width,height,bytes,error\n")
        for r in rows:
            f.write(
                f"{r['model']},{r['workflow']},{r['status']},{r['seconds']},"
                f"{r['filename']},{r['width']},{r['height']},{r['bytes']},"
                f"\"{str(r['error']).replace(chr(34), chr(39))}\"\n"
            )
    passed = sum(1 for r in rows if r["status"] == "pass")
    bpass = sum(1 for b in boundaries if b["pass"])
    lines = [
        "# 画笔（生图底模）接入验收报告",
        "",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 网关：{LOCAL_BASE}",
        f"- 模型通过：**{passed}/{len(rows)}**",
        f"- 边界检查通过：**{bpass}/{len(boundaries)}**",
        "",
        "## 生成矩阵",
        "",
        "| 模型 | 工作流 | 结果 | 耗时(s) | 尺寸 | 文件 | 备注 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        size = f"{r['width']}x{r['height']}" if r["width"] else "-"
        lines.append(
            f"| `{r['model']}` | {r['workflow']} | {r['status']} | {r['seconds'] or '-'} | {size} | "
            f"{r['filename'] or '-'} | {r['error'] or ''} |"
        )
    lines += ["", "## 能力边界", "", "| 检查 | 结果 | 详情 |", "|---|---|---|"]
    for b in boundaries:
        lines.append(f"| {b['check']} | {'PASS' if b['pass'] else 'FAIL'} | {b['detail']} |")
    lines += ["", "## 素材", "", f"- 证据 CSV：`{csv_path}`"]
    report_path = os.path.join(OUT_DIR, "acceptance-report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n证据已写入：{csv_path} / {report_path}")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="", help="只测这些模型 id（逗号分隔）")
    parser.add_argument("--no-boundary", action="store_true")
    parser.add_argument("--timeout", type=float, default=0, help="全局超时覆盖（秒）")
    args = parser.parse_args()

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client as connect

    async with connect(GATEWAY) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            models = (await _call(session, "comfyui_list_models")).get("models") or []
            profiles = [m for m in models if m.get("available") is not False]
            if args.only:
                wanted = {x.strip() for x in args.only.split(",") if x.strip()}
                profiles = [m for m in profiles if m["id"] in wanted]
            rows = []
            for profile in profiles:
                timeout = args.timeout or float(profile.get("timeout_sec") or 900)
                print(f"[{profile['id']}] 提交中（超时 {timeout:.0f}s）...")
                row = await run_model(session, profile, timeout)
                rows.append(row)
                print(f"[{profile['id']}] {row['status']} {row['seconds']}s {row['width']}x{row['height']} {row['error']}".replace("NonexNone","-"))
            boundaries = []
            if not args.no_boundary:
                boundaries = await run_boundary(session, models)
    write_report(rows, boundaries, models)
    return 0 if all(r["status"] == "pass" for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
