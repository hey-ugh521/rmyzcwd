"""Möbius MVP 自动化验收。

用法（在 mobius 目录或任意目录都行，用 Core 的环境解释器）：
    D:\\Anaconda3\\envs\\open-webui\\python.exe tests\\verify.py
    ... tests\\verify.py --quick        # 跳过真实生图/语音，只验连通性与机制
    ... tests\\verify.py --gateway http://127.0.0.1:8899

退出码：0 全过 / 1 有失败。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from core import store  # noqa: E402

CORE = os.environ.get("MOBIUS_CORE", "http://127.0.0.1:8900")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

RESULT: list[dict] = []


def _gateway_token() -> str:
    """与网关同口径：env MOBIUS_GATEWAY_TOKEN → secrets.json gateway.token。"""
    tok = os.environ.get("MOBIUS_GATEWAY_TOKEN", "").strip()
    if tok:
        return tok
    try:
        from core import secrets as _secrets

        return _secrets.get("gateway", "token")
    except Exception:  # noqa: BLE001
        return ""


def rec(cid: str, name: str, ok: bool, detail: str = "", skipped: bool = False) -> None:
    RESULT.append({"id": cid, "name": name, "ok": ok, "detail": detail, "skipped": skipped})
    icon = "SKIP" if skipped else ("PASS" if ok else "FAIL")
    color = "\033[36m" if skipped else ("\033[32m" if ok else "\033[31m")
    print(f"{color}[{icon}]\033[0m {cid:<5} {name}" + (f"  —— {detail}" if detail else ""), flush=True)


async def poll_gateway_task(client: httpx.AsyncClient, gw: str, task_id: str,
                            timeout: float = 300) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = await client.get(f"{gw}/api/tasks/{task_id}")
        last = r.json()
        obj = last.get("task") or last
        # ADR-002 状态词（completed/cancelled 双写 l；submission_unknown 也是终态）
        if obj.get("status") in ("completed", "failed", "cancelled", "submission_unknown"):
            return obj
        await asyncio.sleep(2)
    return last.get("task") or last


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--gateway", default=None)
    ap.add_argument("--timeout", type=float, default=300)
    args = ap.parse_args()

    tok = _gateway_token()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        # ---------- A0 连通性 ----------
        try:
            r = await client.get(f"{CORE}/health")
            rec("A0", "Core 存活", r.status_code == 200, f"HTTP {r.status_code}")
        except Exception as e:
            rec("A0", "Core 存活", False, str(e))
            return summarize()

        try:
            st = (await client.get(f"{CORE}/api/status")).json()
            svc = st["media"]["services"]
            rec("A0b", "Core 状态接口", True,
                f"mode={st['media']['mode']} 服务={list(svc)}")
            gw_url = args.gateway or store.CFG.get("gateway", {}).get("url", "http://127.0.0.1:8899")
            gw = gw_url.rstrip("/")
            rec("A0c", "媒体模式可读", st["media"]["mode"] in ("off", "on_demand", "prewarm"),
                st["media"]["mode"])
        except Exception as e:
            rec("A0b", "Core 状态接口", False, str(e))
            return summarize()

        # ---------- 网关 ----------
        try:
            g = (await client.get(f"{gw}/api/status")).json()
            rec("A0d", "Media Gateway 存活", bool(g.get("ok")),
                f"comfyui={g.get('comfyui', {}).get('state')} 版本={g.get('gateway', {}).get('version')}")
        except Exception as e:
            rec("A0d", "Media Gateway 存活", False, f"{gw} -> {e}")

        try:
            lor = (await client.get(f"{gw}/api/loras")).json()
            names = [x["name"] for x in lor.get("loras", [])]
            rec("A1a", "LoRA 枚举", len(names) >= 20,
                f"{len(names)} 个，含 Bronya={'Bronya_Zaychik24in1_Hi3_IL-03.safetensors' in names}")
        except Exception as e:
            rec("A1a", "LoRA 枚举", False, str(e))

        try:
            chs = (await client.get(f"{CORE}/api/characters")).json()["characters"]
            ids = [c["id"] for c in chs]
            rec("A1b", "角色档案", len(ids) >= 3, f"{ids}")
        except Exception as e:
            rec("A1b", "角色档案", False, str(e))

        try:
            mods = (await client.get(f"{CORE}/api/models")).json().get("models", [])
            rec("A1c", "checkpoint 枚举", len(mods) >= 5, f"{len(mods)} 个")
        except Exception as e:
            rec("A1c", "checkpoint 枚举", False, str(e))

        # ---------- A1 直接网关生图（带角色 LoRA） ----------
        if args.quick:
            rec("A1", "网关生图（bronya + LoRA）", True, "quick 模式跳过", skipped=True)
        else:
            try:
                t0 = time.time()
                body = {"character": "bronya", "prompt": "在雪地里看雪", "async": True,
                        "seed": 12345}
                r = await client.post(f"{gw}/api/generate", json=body, timeout=320)
                j = r.json()
                if not j.get("ok"):
                    rec("A1", "网关生图（bronya + LoRA）", False, f"提交失败 {j}")
                else:
                    tid = j["task_id"]
                    print(f"       · 网关任务 {tid} 已提交，LoRA={[l['name'] for l in j.get('loras', [])]}", flush=True)
                    out = await poll_gateway_task(client, gw, tid, args.timeout)
                    ok = out.get("status") == "completed"
                    res = out.get("result") or {}
                    detail = f"{out.get('status')} {res.get('filename')} {round(time.time()-t0,1)}s"
                    if ok:
                        # 经网关媒体代理下载校验，不依赖 ComfyUI 本机输出目录
                        r_img = await client.get(f"{gw}{res.get('path')}", timeout=60)
                        img = r_img.content
                        ok = (r_img.status_code == 200
                              and img.startswith(PNG_SIGNATURE) and len(img) > 50_000)
                        detail += (f" {len(img) // 1024}KB" if ok else
                                   f" 下载校验失败 HTTP {r_img.status_code} {len(img)}B")
                    else:
                        detail += f" error={out.get('error')}"
                    rec("A1", "网关生图（bronya + LoRA）", ok, detail)
            except Exception as e:
                rec("A1", "网关生图（bronya + LoRA）", False, str(e))

        # ---------- A1e 经 Core Agent 真生图（端到端） ----------
        if args.quick:
            rec("A1e", "Core Agent 端到端生图", True, "quick 模式跳过", skipped=True)
        else:
            try:
                t0 = time.time()
                body = {
                    "model": "mobius-core", "stream": False, "user": "qq:verify",
                    "messages": [{"role": "user",
                                  "content": "画一张布洛妮娅在雪地里的图，直接画，不用问我"}],
                }
                r = await client.post(f"{CORE}/v1/chat/completions", json=body, timeout=args.timeout)
                content = r.json()["choices"][0]["message"]["content"]
                has_img = "img?filename=" in content
                # 到任务中心找对应任务
                tasks = (await client.get(f"{CORE}/api/tasks?limit=10")).json()["tasks"]
                img_task = next((t for t in tasks if t["kind"] == "image" and t["source"] == "qq"), None)
                ok = has_img and img_task is not None and img_task["status"] == "succeeded"
                detail = (f"{round(time.time()-t0,1)}s 图片链接={has_img} "
                          f"任务={(img_task or {}).get('status')} "
                          f"角色={(img_task or {}).get('character')}")
                rec("A1e", "Core Agent 端到端生图", ok, detail)
                if img_task:
                    rec("A4a", "任务中心记录生图", img_task["status"] == "succeeded",
                        f"id={img_task['id']} 用时={round(time.time()-t0,1)}s")
            except Exception as e:
                rec("A1e", "Core Agent 端到端生图", False, str(e))

        # ---------- A3 TTS ----------
        if args.quick:
            rec("A3", "TTS 合成", True, "quick 模式跳过", skipped=True)
        else:
            try:
                r = await client.post(f"{gw}/api/tts",
                                      json={"text": "今天也辛苦了，早点休息。",
                                            "engine": "kokoro"}, timeout=180)
                j = r.json()
                ok = bool(j.get("ok") and j.get("audio_url"))
                fn = j.get("filename") or ""
                if ok and j.get("path"):
                    # 经网关媒体代理下载校验，不依赖 tts_output 本机目录
                    r_audio = await client.get(f"{gw}{j['path']}", timeout=60)
                    audio = r_audio.content
                    ok = r_audio.status_code == 200 and len(audio) > 8000
                    detail = (f"{fn} {len(audio) // 1024}KB" if ok else
                              f"{fn} 下载校验失败 HTTP {r_audio.status_code} {len(audio)}B")
                else:
                    detail = str(j)[:200]
                rec("A3", "TTS 合成", ok, detail)
            except Exception as e:
                rec("A3", "TTS 合成", False, str(e))

        # ---------- A6 原生 SSE ----------
        try:
            events: list[str] = []
            text_parts: list[str] = []
            async with client.stream("POST", f"{CORE}/v1/chat",
                                     json={"source": "web", "user_id": "web:verify",
                                           "session_id": "verify:sse",
                                           "text": "用一句话介绍一下你自己"},
                                     timeout=120) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        events.append(line.split(":", 1)[1].strip())
                    elif line.startswith("data:"):
                        try:
                            d = json.loads(line.split(":", 1)[1].strip())
                        except Exception:
                            continue
                        if d.get("delta"):
                            text_parts.append(d["delta"])
            ok = "meta" in events and "done" in events and "text" in events and bool(text_parts)
            rec("A6", "原生 SSE 对话", ok,
                f"事件={sorted(set(events))} 回复={''.join(text_parts)[:40]}")
        except Exception as e:
            rec("A6", "原生 SSE 对话", False, str(e))

        # ---------- A5 记忆机制 ----------
        try:
            uid = "verify:mem"
            store.mem_forget(uid, "称呼")
            c = store.cand_add(uid, "称呼", "博士", 0.9, "verify", evidence="以后叫我博士")
            cands = (await client.get(f"{CORE}/api/memory/candidates?status=pending")).json()["candidates"]
            found = any(x["id"] == c["id"] for x in cands)
            r = await client.post(f"{CORE}/api/memory/candidates/{c['id']}/approve", json={})
            prof = (await client.get(f"{CORE}/api/memory/user/{uid}")).json()["profile"]
            applied = any(m["key"] == "称呼" and m["value"] == "博士" for m in prof)
            forgot = (await client.post(f"{CORE}/api/memory/forget",
                                        json={"user_id": uid, "key": "称呼"})).json().get("ok")
            after = (await client.get(f"{CORE}/api/memory/user/{uid}")).json()["profile"]
            gone = not any(m["key"] == "称呼" for m in after)
            rec("A5", "记忆候选→审批→遗忘", found and applied and forgot and gone,
                f"候选可见={found} 审批生效={applied} 遗忘={forgot and gone}")
        except Exception as e:
            rec("A5", "记忆候选→审批→遗忘", False, str(e))

        # ---------- A4b 审计 ----------
        try:
            au = (await client.get(f"{CORE}/api/audit?limit=50")).json()["audit"]
            kinds = {a["action"] for a in au}
            rec("A4b", "审计留痕", len(au) > 0,
                f"{len(au)} 条，样例={sorted(kinds)[:4]}")
        except Exception as e:
            rec("A4b", "审计留痕", False, str(e))

        # ---------- A4c 取消 ----------
        if args.quick:
            rec("A4c", "任务取消", True, "quick 模式跳过", skipped=True)
        else:
            try:
                t1 = (await client.post(f"{gw}/api/generate",
                                        json={"prompt": "a cat", "async": True},
                                        timeout=60)).json()
                t2 = (await client.post(f"{gw}/api/generate",
                                        json={"prompt": "a dog", "async": True},
                                        timeout=60)).json()
                victim = t2.get("task_id")
                await asyncio.sleep(1)
                rc = (await client.post(f"{gw}/api/tasks/{victim}/cancel")).json()
                st = (rc.get("task") or {}).get("status") or rc.get("status")
                rec("A4c", "任务取消", st == "cancelled", f"{victim} -> {st}")
            except Exception as e:
                rec("A4c", "任务取消", False, str(e))

        # ---------- Console ----------
        try:
            r = await client.get(f"{CORE}/console/")
            body = r.text
            rec("A2a", "Console 页面", r.status_code == 200 and len(body) > 1000,
                f"HTTP {r.status_code} {len(body)}B")
        except Exception as e:
            rec("A2a", "Console 页面", False, str(e))

        # ---------- A2b 媒体开关 ----------
        try:
            cur = (await client.get(f"{CORE}/api/media/config")).json()
            rec("A2b", "媒体配置读写", "idle_sleep_sec" in cur, json.dumps(cur, ensure_ascii=False))
        except Exception as e:
            rec("A2b", "媒体配置读写", False, str(e))

    return summarize()


def summarize() -> int:
    print("\n" + "=" * 70)
    total = len(RESULT)
    fails = [r for r in RESULT if not r["ok"]]
    skips = [r for r in RESULT if r["skipped"]]
    passes = [r for r in RESULT if r["ok"] and not r["skipped"]]
    print(f"合计 {total} 项：通过 {len(passes)}，跳过 {len(skips)}，失败 {len(fails)}")
    if fails:
        print("\n失败项：")
        for f in fails:
            print(f"  - {f['id']} {f['name']}: {f['detail']}")
    print("=" * 70)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
