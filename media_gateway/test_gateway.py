#!/usr/bin/env python
"""Möbius Media Gateway 自测脚本（真跑，不是 mock）。

覆盖：
  1. /health、/api/status（ComfyUI/Kokoro/Core/队列/设备）
  2. /api/models 列模型
  3. /api/loras 列 LoRA（含 q 过滤、size/mtime/tags）
  4. /api/characters 列角色档案
  5. /api/voices 列音色
  6. 无 LoRA 同步生图（真实 PNG，校验字节数与签名）
  7. 带 LoRA 同步生图（真实 PNG，报告文件路径与字节数）
  8. character 自动解析 + 异步任务轮询
  9. 取消：排队中直接删 + 运行中中断 ComfyUI
 10. TTS（Kokoro，真实 wav）
 11. /api/upload + /upload/{id} 回读
 12. MCP 面 /mcp：工具清单（4 个旧工具名必须存在）+ 调用若干工具

用法（gateway 必须已启动，调试端口 8898）：
    D:\\AI\\qqbot-mcp\\Scripts\\python.exe test_gateway.py --base http://127.0.0.1:8898
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback

import httpx

DEFAULT_BASE = "http://127.0.0.1:8898"
LEGACY_TOOLS = {"comfyui_generate", "comfyui_status", "comfyui_list_models", "tts_speak"}
NEW_TOOLS = {
    "comfyui_list_loras",
    "comfyui_list_characters",
    "comfyui_task_submit",
    "comfyui_task_status",
    "comfyui_task_cancel",
    "comfyui_queue_status",
    "tts_list_voices",
    "media_status",
    "media_wake",
    "media_sleep",
    "media_set_mode",
}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

CONTEXT: dict = {}
RESULTS: list[dict] = []


def _print(msg: str = "") -> None:
    print(msg, flush=True)


def _short(value: object, limit: int = 400) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def run_case(name: str, func) -> bool:
    started = time.time()
    try:
        detail = func()
        elapsed = round(time.time() - started, 1)
        RESULTS.append({"name": name, "ok": True, "seconds": elapsed, "detail": detail})
        _print(f"[PASS] {name}  ({elapsed}s)  {_short(detail or '')}")
        return True
    except Exception as exc:  # noqa: BLE001
        elapsed = round(time.time() - started, 1)
        RESULTS.append(
            {
                "name": name,
                "ok": False,
                "seconds": elapsed,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        _print(f"[FAIL] {name}  ({elapsed}s)  {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return False


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def get_json(client: httpx.Client, path: str, **kwargs) -> dict:
    resp = client.get(path, **kwargs)
    try:
        data = resp.json()
    except Exception:
        raise AssertionError(f"GET {path} -> HTTP {resp.status_code}, 非 JSON: {resp.text[:300]}")
    if resp.status_code >= 400:
        raise AssertionError(f"GET {path} -> HTTP {resp.status_code}: {_short(data)}")
    return data


def post_json(client: httpx.Client, path: str, payload: dict | None = None, **kwargs) -> dict:
    resp = client.post(path, json=payload or {}, **kwargs)
    try:
        data = resp.json()
    except Exception:
        raise AssertionError(f"POST {path} -> HTTP {resp.status_code}, 非 JSON: {resp.text[:300]}")
    if resp.status_code >= 400:
        raise AssertionError(f"POST {path} -> HTTP {resp.status_code}: {_short(data)}")
    return data


def wait_task(client: httpx.Client, task_id: str, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        data = get_json(client, f"/api/tasks/{task_id}")
        last = data.get("task") or {}
        if last.get("status") in ("succeeded", "failed", "canceled"):
            return last
        time.sleep(1.5)
    raise AssertionError(f"等待任务 {task_id} 超时，最后状态: {_short(last)}")


def fetch_image(client: httpx.Client, result: dict) -> bytes:
    path = result.get("path")
    expect(bool(path), f"任务结果缺少 path: {result}")
    resp = client.get(path, timeout=60.0)
    expect(resp.status_code == 200, f"GET {path} -> HTTP {resp.status_code}")
    data = resp.content
    expect(data.startswith(PNG_SIGNATURE), f"{path} 不是 PNG（前 8 字节={data[:8]!r}）")
    expect(len(data) > 50_000, f"{path} 只有 {len(data)} 字节，疑似空图")
    return data


# ---------------------------------------------------------------------------
# 各项测试
# ---------------------------------------------------------------------------


def test_health(client: httpx.Client) -> str:
    data = get_json(client, "/health")
    expect(data.get("ok") is True, f"/health 非 ok: {data}")
    expect(data.get("service") == "mobius-media-gateway", f"service 字段异常: {data}")
    return f"v{data.get('version')} port={data.get('port')} uptime={data.get('uptime_sec')}s"


def test_status(client: httpx.Client) -> str:
    data = get_json(client, "/api/status")
    expect(data.get("ok") is True, f"/api/status 非 ok: {_short(data)}")
    comfy = data.get("comfyui") or {}
    expect(comfy.get("state") in ("ready", "busy"), f"ComfyUI 状态非 ready/busy: {_short(comfy)}")
    queue = data.get("queue") or {}
    expect(int(queue.get("concurrency", 0)) == 1, f"并发应为 1: {_short(queue)}")
    kokoro = data.get("kokoro") or {}
    expect(kokoro.get("state") == "ready", f"Kokoro 未就绪: {_short(kokoro)}")
    CONTEXT["status"] = data
    return (
        f"comfyui={comfy.get('state')}(v{comfy.get('version')}, queue {comfy.get('queue_running')}/{comfy.get('queue_pending')}) "
        f"kokoro={kokoro.get('state')} core={(data.get('core') or {}).get('state')} "
        f"device={(data.get('device') or {}).get('name')}"
    )


def test_models(client: httpx.Client) -> str:
    data = get_json(client, "/api/models")
    models = data.get("models") or []
    expect(len(models) >= 5, f"checkpoint 数量过少: {models}")
    expect(
        "Illustrious-XL-v1.0.safetensors" in models,
        f"缺少 Illustrious-XL-v1.0.safetensors: {models}",
    )
    CONTEXT["models"] = models
    return f"{len(models)} 个 checkpoint，含 Illustrious: {models}"


def test_loras(client: httpx.Client) -> str:
    data = get_json(client, "/api/loras")
    loras = data.get("loras") or []
    expect(len(loras) >= 20, f"LoRA 数量过少: {data.get('count')}")
    names = [item.get("name") for item in loras]
    target = "Bronya_Zaychik24in1_Hi3_IL-03.safetensors"
    expect(target in names, f"缺少 {target}")
    bronya = next(item for item in loras if item.get("name") == target)
    expect(bool(bronya.get("size")), f"LoRA 缺少 size: {bronya}")
    expect(bool(bronya.get("mtime")), f"LoRA 缺少 mtime: {bronya}")
    expect(bool(bronya.get("tags")), f"LoRA 缺少 tags: {bronya}")
    filtered = get_json(client, "/api/loras?q=bronya")
    filtered_names = [item.get("name") for item in filtered.get("loras") or []]
    expect(target in filtered_names, f"q=bronya 未命中: {filtered_names}")
    CONTEXT["lora_name"] = target
    CONTEXT["lora"] = bronya
    return (
        f"{len(loras)} 个 LoRA；{target} size={bronya['size']} "
        f"mtime={bronya['mtime']} tags={bronya['tags']} q=bronya -> {filtered_names}"
    )


def test_characters(client: httpx.Client) -> str:
    data = get_json(client, "/api/characters")
    characters = data.get("characters") or []
    ids = [item.get("id") for item in characters]
    for expected in ("bronya", "kiana", "elysia"):
        expect(expected in ids, f"缺少角色 {expected}: {ids}")
    bronya = next(item for item in characters if item.get("id") == "bronya")
    expect(
        bronya.get("loras") and bronya["loras"][0]["name"] == "Bronya_Zaychik24in1_Hi3_IL-03.safetensors",
        f"bronya 的 LoRA 档案异常: {_short(bronya.get('loras'))}",
    )
    single = get_json(client, "/api/characters/bronya")
    expect((single.get("character") or {}).get("id") == "bronya", f"单个角色接口异常: {_short(single)}")
    return f"{ids}；bronya checkpoint={bronya.get('checkpoint')} loras={bronya.get('loras')}"


def test_voices(client: httpx.Client) -> str:
    data = get_json(client, "/api/voices")
    voices = [item.get("id") for item in data.get("voices") or []]
    expect("zf_xiaobei" in voices, f"缺少默认音色 zf_xiaobei: {voices[:20]}")
    expect((data.get("engines") or {}).get("kokoro") is True, f"Kokoro 引擎状态异常: {_short(data)}")
    return f"{len(voices)} 个 Kokoro 音色（含 zf_xiaobei），cloned={len(data.get('cloned') or [])}"


def test_generate_no_lora(client: httpx.Client) -> str:
    body = {
        "prompt": "1girl, solo, standing in a snowy street, winter, detailed background, masterpiece, best quality",
        "workflow": "portrait",
        "width": 832,
        "height": 1216,
        "async": False,
    }
    started = time.time()
    data = post_json(client, "/api/generate", body, timeout=400.0)
    expect(data.get("ok") is True, f"生图失败: {_short(data)}")
    png = fetch_image(client, data)
    local_path = data.get("local_path")
    expect(bool(local_path), f"缺少 local_path: {_short(data)}")
    expect(os.path.isfile(local_path), f"本地文件不存在: {local_path}")
    expect(os.path.getsize(local_path) == len(png), "本地文件大小与 HTTP 下载不一致")
    CONTEXT["no_lora_png"] = png
    CONTEXT["no_lora"] = data
    return (
        f"filename={data['filename']} bytes={len(png)} path={local_path} "
        f"seconds={data.get('seconds')} 总耗时={round(time.time() - started, 1)}s"
    )


def test_generate_with_lora(client: httpx.Client) -> str:
    body = {
        "prompt": "bronya zaychik (honkai impact 3rd), in the snow, holding a rifle, masterpiece, best quality",
        "checkpoint": "Illustrious-XL-v1.0.safetensors",
        "loras": [{"name": "Bronya_Zaychik24in1_Hi3_IL-03.safetensors", "weight": 0.75}],
        "workflow": "portrait",
        "width": 832,
        "height": 1216,
        "async": False,
    }
    started = time.time()
    data = post_json(client, "/api/generate", body, timeout=400.0)
    expect(data.get("ok") is True, f"带 LoRA 生图失败: {_short(data)}")
    expect(
        data.get("loras") == [{"name": "Bronya_Zaychik24in1_Hi3_IL-03.safetensors", "weight": 0.75}],
        f"响应 LoRA 不符: {_short(data.get('loras'))}",
    )
    png = fetch_image(client, data)
    local_path = data.get("local_path")
    expect(bool(local_path) and os.path.isfile(local_path), f"本地文件不存在: {local_path}")
    CONTEXT["lora_png"] = png
    CONTEXT["lora_result"] = data
    return (
        f"filename={data['filename']} bytes={len(png)} path={local_path} "
        f"loras={_short(data.get('loras'))} seconds={data.get('seconds')} "
        f"总耗时={round(time.time() - started, 1)}s"
    )


def test_async_character(client: httpx.Client) -> str:
    body = {
        "character": "bronya",
        "prompt": "standing in a snowy forest, looking at viewer",
        "async": True,
        "seed": 123456789,
    }
    submitted = post_json(client, "/api/generate", body)
    expect(submitted.get("ok") is True, f"异步提交失败: {_short(submitted)}")
    task_id = submitted.get("task_id")
    expect(str(task_id).startswith("g_"), f"task_id 前缀异常: {task_id}")
    expect(submitted.get("status") == "queued", f"提交状态应为 queued: {_short(submitted)}")
    CONTEXT["async_task_id"] = task_id
    task = wait_task(client, task_id, timeout=400.0)
    expect(task.get("status") == "succeeded", f"异步任务失败: {_short(task)}")
    params = task.get("params") or {}
    expect(
        params.get("checkpoint") == "Illustrious-XL-v1.0.safetensors",
        f"角色档案 checkpoint 未补齐: {_short(params)}",
    )
    loras = params.get("loras") or []
    expect(
        any(item.get("name") == "Bronya_Zaychik24in1_Hi3_IL-03.safetensors" for item in loras),
        f"角色档案 LoRA 未补齐: {_short(loras)}",
    )
    expect(
        "bronya zaychik" in (task.get("prompt") or "").lower(),
        f"角色 style_prompt 未并入 prompt: {_short(task.get('prompt'))}",
    )
    applied = params.get("loras_applied") or []
    expect(bool(applied), f"缺少 loras_applied（LoRA 注入证据）: {_short(params)}")
    png = fetch_image(client, task.get("result") or {})
    CONTEXT["character_task"] = task
    return (
        f"task={task_id} status=succeeded filename={(task.get('result') or {}).get('filename')} "
        f"bytes={len(png)} checkpoint={params.get('checkpoint')} "
        f"loras={_short(loras)} 注入节点={_short(applied)} prompt 已并入画风"
    )


def test_cancel(client: httpx.Client) -> str:
    # A：真正跑到 running，验证“已提交 ComfyUI 的取消”
    a = post_json(
        client,
        "/api/generate",
        {"prompt": "1girl, solo, snow mountain, detailed landscape", "workflow": "portrait", "async": True},
    )
    task_a = a.get("task_id")
    expect(a.get("ok") is True and task_a, f"任务 A 提交失败: {_short(a)}")
    deadline = time.time() + 120
    running_task: dict = {}
    while time.time() < deadline:
        running_task = get_json(client, f"/api/tasks/{task_a}").get("task") or {}
        params = running_task.get("params") or {}
        if running_task.get("status") == "running" and params.get("prompt_id"):
            break
        expect(
            running_task.get("status") not in ("failed", "canceled"),
            f"任务 A 提前失败: {_short(running_task)}",
        )
        time.sleep(1.0)
    expect(
        running_task.get("status") == "running" and (running_task.get("params") or {}).get("prompt_id"),
        f"任务 A 未进入 running/未提交 ComfyUI: {_short(running_task)}",
    )
    remote_id = (running_task.get("params") or {}).get("prompt_id")

    # B：排在 A 后面，验证“队列中直接删”
    b = post_json(
        client,
        "/api/generate",
        {"prompt": "1girl, solo, rainy city at night", "workflow": "portrait", "async": True},
    )
    task_b = b.get("task_id")
    expect(b.get("ok") is True and task_b, f"任务 B 提交失败: {_short(b)}")
    before_b = get_json(client, f"/api/tasks/{task_b}").get("task") or {}
    expect(before_b.get("status") == "queued", f"任务 B 应为 queued: {_short(before_b)}")
    cancel_b = post_json(client, f"/api/tasks/{task_b}/cancel")
    expect(cancel_b.get("ok") is True, f"取消排队任务失败: {_short(cancel_b)}")
    expect(cancel_b.get("action") == "removed_from_queue", f"取消排队动作异常: {_short(cancel_b)}")
    expect((cancel_b.get("task") or {}).get("status") == "canceled", f"B 状态未变 canceled: {_short(cancel_b)}")

    # 取消运行中的 A（应由网关中断 ComfyUI）
    cancel_a = post_json(client, f"/api/tasks/{task_a}/cancel")
    expect(cancel_a.get("ok") is True, f"取消运行任务失败: {_short(cancel_a)}")
    expect(
        cancel_a.get("action") in ("interrupted", "deleted_from_queue"),
        f"A 的取消动作不是真中断: {_short(cancel_a)}",
    )
    time.sleep(3.0)
    after_a = get_json(client, f"/api/tasks/{task_a}").get("task") or {}
    expect(after_a.get("status") == "canceled", f"A 取消后状态不对: {_short(after_a)}")
    expect(after_a.get("result") is None, f"取消的 A 不应有结果: {_short(after_a)}")

    # ComfyUI 队列应最终清空（取消真的生效，而不只是标记）
    deadline = time.time() + 20
    queue_snapshot: dict = {}
    while time.time() < deadline:
        status = get_json(client, "/api/status")
        comfy = status.get("comfyui") or {}
        queue_snapshot = comfy
        if int(comfy.get("queue_running") or 0) == 0 and int(comfy.get("queue_pending") or 0) == 0:
            break
        time.sleep(1.0)
    expect(
        int(queue_snapshot.get("queue_running") or 0) == 0
        and int(queue_snapshot.get("queue_pending") or 0) == 0,
        f"ComfyUI 队列未清空: {_short(queue_snapshot)}",
    )
    CONTEXT["cancel"] = {"task_a": task_a, "task_b": task_b, "remote_id": remote_id}
    return (
        f"A({task_a}) running+remote_id={remote_id} 取消 -> {cancel_a.get('action')}；"
        f"B({task_b}) queued 取消 -> removed_from_queue；ComfyUI 队列已清空"
    )


def test_tts(client: httpx.Client) -> str:
    data = post_json(
        client,
        "/api/tts",
        {"text": "你好，我是布洛妮娅。欢迎来到雪原。", "character": "bronya"},
        timeout=180.0,
    )
    expect(data.get("ok") is True, f"TTS 失败: {_short(data)}")
    expect(data.get("engine") == "kokoro", f"角色默认引擎应为 kokoro: {_short(data)}")
    expect(data.get("voice") == "zf_xiaobei", f"角色默认音色错误: {_short(data)}")
    filename = data.get("filename")
    expect(bool(filename), f"缺少 filename: {_short(data)}")
    local_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tts_output", filename)
    expect(os.path.isfile(local_path), f"音频文件不存在: {local_path}")
    size = os.path.getsize(local_path)
    expect(size > 5000, f"音频过小: {size} 字节")
    resp = client.get(data["path"], timeout=60.0)
    expect(resp.status_code == 200, f"GET {data['path']} -> HTTP {resp.status_code}")
    expect(len(resp.content) == size, "HTTP 下载音频大小与本地不一致")
    CONTEXT["tts"] = data
    return (
        f"filename={filename} bytes={size} seconds={data.get('seconds')} "
        f"engine={data.get('engine')} voice={data.get('voice')} path={data['path']}"
    )


def test_upload(client: httpx.Client) -> str:
    png = CONTEXT.get("lora_png") or CONTEXT.get("no_lora_png")
    expect(bool(png), "没有可上传的 PNG（前面的生图测试失败）")
    files = {"file": ("reference_test.png", png, "image/png")}
    resp = client.post("/api/upload", files=files, timeout=60.0)
    expect(resp.status_code == 200, f"/api/upload -> HTTP {resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    expect(payload.get("ok") is True, f"上传失败: {_short(payload)}")
    upload_id = payload.get("upload_id")
    expect(str(upload_id).startswith("up_"), f"upload_id 异常: {_short(payload)}")
    back = client.get(payload["path"], timeout=60.0)
    expect(back.status_code == 200, f"GET {payload['path']} -> HTTP {back.status_code}")
    expect(back.content == png, "回读的参考图与上传内容不一致")
    CONTEXT["upload"] = payload
    return (
        f"upload_id={upload_id} filename={payload.get('filename')} bytes={payload.get('size')} "
        f"回读 {payload['path']} 一致"
    )


def test_tasks_list(client: httpx.Client) -> str:
    data = get_json(client, "/api/tasks?limit=20")
    tasks = data.get("tasks") or []
    expect(bool(tasks), "任务列表为空")
    statuses = {task.get("status") for task in tasks}
    canceled = get_json(client, "/api/tasks?status=canceled").get("tasks") or []
    expect(bool(canceled), f"按 status=canceled 过滤为空（statuses={statuses}）")
    return f"{len(tasks)} 个任务，statuses={sorted(statuses)}，canceled={len(canceled)}"


def test_wake(client: httpx.Client) -> str:
    data = post_json(client, "/api/wake")
    expect(data.get("ok") is True, f"唤醒失败: {_short(data)}")
    expect(data.get("state") == "ready", f"唤醒后状态非 ready: {_short(data)}")
    return f"state={data.get('state')} action={data.get('action')} seconds={data.get('seconds')}"


def _mcp_sync(base: str) -> str:
    """MCP 面测试（streamable HTTP，使用 mcp 2.x 高层 Client）。"""
    from mcp import Client  # 延迟导入：REST 测试不依赖 mcp 包

    async def runner() -> str:
        async with Client(f"{base}/mcp") as client:
            listed = await client.list_tools()
            names = [tool.name for tool in listed.tools]
            missing_legacy = LEGACY_TOOLS - set(names)
            missing_new = NEW_TOOLS - set(names)
            expect(not missing_legacy, f"缺少旧工具名: {missing_legacy}")
            expect(not missing_new, f"缺少新增工具: {missing_new}")

            models = await client.call_tool("comfyui_list_models", {})
            model_text = models.content[0].text  # type: ignore[attr-defined]
            model_data = json.loads(model_text)
            expect(model_data.get("ok") is True, f"comfyui_list_models 失败: {model_text[:300]}")
            expect(len(model_data.get("models") or []) >= 5, "MCP 列模型数量过少")

            queue = await client.call_tool("comfyui_queue_status", {})
            queue_data = json.loads(queue.content[0].text)  # type: ignore[attr-defined]
            expect(queue_data.get("ok") is True, f"comfyui_queue_status 失败: {short(queue_data)}")

            task_id = CONTEXT.get("async_task_id")
            if task_id:
                task = await client.call_tool("comfyui_task_status", {"task_id": task_id})
                task_data = json.loads(task.content[0].text)  # type: ignore[attr-defined]
                expect(task_data.get("ok") is True, f"comfyui_task_status 失败: {short(task_data)}")
                expect(
                    (task_data.get("task") or {}).get("status") == "succeeded",
                    f"MCP 查到的任务状态异常: {short(task_data)}",
                )
            return (
                f"{len(names)} 个工具，旧工具 {sorted(LEGACY_TOOLS)} 全部保留；"
                f"MCP 列模型 {len(model_data.get('models') or [])} 个；队列状态 ok"
            )

    return asyncio.run(runner())


def short(value: object) -> str:
    return _short(value, 300)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Möbius Media Gateway 自测")
    parser.add_argument("--base", default=os.environ.get("MOBIUS_GATEWAY_TEST_BASE", DEFAULT_BASE))
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()
    base = args.base.rstrip("/")

    _print("=" * 78)
    _print(f"Möbius Media Gateway 自测  base={base}")
    _print(f"time={time.strftime('%Y-%m-%d %H:%M:%S')}")
    _print("=" * 78)

    client = httpx.Client(
        base_url=base,
        timeout=httpx.Timeout(args.timeout, connect=10.0),
    )
    try:
        try:
            health = get_json(client, "/health")
            _print(f"连通性 OK: {_short(health)}")
        except Exception as exc:  # noqa: BLE001
            _print(f"[FATAL] 无法连接 gateway {base}: {exc}")
            _print("请先启动：D:\\AI\\qqbot-mcp\\Scripts\\python.exe server.py --port 8898")
            return 2

        cases = [
            ("01 /health", lambda: test_health(client)),
            ("02 /api/status", lambda: test_status(client)),
            ("03 /api/models 列模型", lambda: test_models(client)),
            ("04 /api/loras 列 LoRA", lambda: test_loras(client)),
            ("05 /api/characters 列角色", lambda: test_characters(client)),
            ("06 /api/voices 列音色", lambda: test_voices(client)),
            ("07 无 LoRA 生图（同步）", lambda: test_generate_no_lora(client)),
            ("08 带 LoRA 生图（同步）", lambda: test_generate_with_lora(client)),
            ("09 character 解析 + 异步任务轮询", lambda: test_async_character(client)),
            ("10 取消（排队/运行中）", lambda: test_cancel(client)),
            ("11 TTS（Kokoro）", lambda: test_tts(client)),
            ("12 /api/upload + 回读", lambda: test_upload(client)),
            ("13 /api/tasks 列表与过滤", lambda: test_tasks_list(client)),
            ("14 /api/wake", lambda: test_wake(client)),
            ("15 MCP 面 /mcp", lambda: _mcp_sync(base)),
        ]
        for name, func in cases:
            run_case(name, func)
    finally:
        client.close()

    passed = sum(1 for item in RESULTS if item["ok"])
    failed = len(RESULTS) - passed
    total_seconds = round(sum(item["seconds"] for item in RESULTS), 1)
    _print("-" * 78)
    _print(f"结果: {passed} passed / {failed} failed，总耗时 {total_seconds}s")
    for item in RESULTS:
        if not item["ok"]:
            _print(f"  FAILED: {item['name']} -> {item.get('error')}")
    _print("-" * 78)

    report = {
        "base": base,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "passed": passed,
        "failed": failed,
        "total_seconds": total_seconds,
        "results": RESULTS,
        "artifacts": {
            "no_lora_image": (CONTEXT.get("no_lora") or {}).get("local_path"),
            "lora_image": (CONTEXT.get("lora_result") or {}).get("local_path"),
            "character_image": ((CONTEXT.get("character_task") or {}).get("result") or {}).get("local_path"),
            "tts_audio": (CONTEXT.get("tts") or {}).get("local_path"),
            "upload_id": (CONTEXT.get("upload") or {}).get("upload_id"),
        },
    }
    report_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "test_report.json")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    _print(f"报告已写入 {report_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
