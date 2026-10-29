"""把 Möbius Pipe 安装到本机 Open WebUI（幂等）。

用法：
    D:\\Anaconda3\\envs\\open-webui\\python.exe scripts\\install-openwebui-pipe.py
    ... --base http://127.0.0.1:3000 --email admin@openwebui.local --password xxx
    ... --dry-run

做的事：登录取 token → 创建/更新 function → 启用 → 验证 /api/models 里出现 Möbius。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
PIPE_FILE = BASE_DIR / "openwebui" / "mobius_pipe.py"
FUNC_ID = "mobius"
FUNC_NAME = "Möbius"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:3000")
    ap.add_argument("--email", default="admin@openwebui.local")
    ap.add_argument("--password", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not PIPE_FILE.exists():
        print(f"找不到 Pipe 文件: {PIPE_FILE}")
        return 2
    content = PIPE_FILE.read_text(encoding="utf-8")
    print(f"Pipe 文件 {PIPE_FILE.name}: {len(content)} 字节")

    if args.dry_run:
        print("dry-run：不实际调用 API")
        return 0

    with httpx.Client(base_url=args.base, timeout=30) as c:
        r = c.post("/api/v1/auths/signin",
                   json={"email": args.email, "password": args.password})
        if r.status_code != 200:
            print(f"登录失败 HTTP {r.status_code}: {r.text[:300]}")
            return 1
        token = r.json().get("token")
        if not token:
            print(f"登录响应里没有 token: {r.text[:300]}")
            return 1
        print(f"登录成功（{args.email}）")
        h = {"Authorization": f"Bearer {token}"}

        payload = {
            "id": FUNC_ID,
            "name": FUNC_NAME,
            "content": content,
            "meta": {
                "description": "把 Open WebUI 的对话转发到 Möbius Core（记忆/角色/任务/媒体统一入口）",
                "manifest": {},
            },
        }
        created = c.post("/api/v1/functions/create", headers=h, json=payload)
        if created.status_code in (200, 201):
            print("已创建 function")
        elif created.status_code == 400 and "already" in created.text.lower():
            print("已存在，改为更新")
        else:
            # 已存在时 create 可能报 id 重复，直接走更新
            upd = c.post(f"/api/v1/functions/id/{FUNC_ID}/update", headers=h, json=payload)
            if upd.status_code >= 400:
                print(f"创建失败 HTTP {created.status_code}: {created.text[:300]}")
                print(f"更新也失败 HTTP {upd.status_code}: {upd.text[:300]}")
                return 1
            print("已更新 function")

        tog = c.post(f"/api/v1/functions/id/{FUNC_ID}/toggle", headers=h)
        if tog.status_code >= 400:
            print(f"启用失败 HTTP {tog.status_code}: {tog.text[:300]}")
            return 1
        print("已启用")

        def _safe_json(resp):
            try:
                return resp.json()
            except Exception:
                return None

        fns = c.get("/api/v1/functions", headers=h)
        data = _safe_json(fns) or []
        names = [f.get("id") for f in data if isinstance(f, dict)]
        print(f"functions: {names}")

        # Pipe 提供的模型会以 "<function_id>.<pipe_id>" 形式出现在模型列表
        models = c.get("/api/models", headers=h)
        mdata = _safe_json(models) or {}
        ids = [m.get("id") for m in (mdata.get("data") or [])]
        hit = [i for i in ids if i and "mobius" in i.lower()]
        if hit:
            print(f"成功：模型列表里出现 {hit}")
        else:
            print(f"警告：/api/models 里没看到 mobius（当前 {ids[:8]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
