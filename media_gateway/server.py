#!/usr/bin/env python
"""Möbius Media Gateway v2.

单进程同时提供三张脸（契约：mobius/docs/ARCHITECTURE.md §1 / §5 / §6 / §7）：

1. MCP 面        /mcp   Streamable HTTP（LangBot / Open WebUI 直连调试）
2. REST 面       /api/*  （Möbius Core 生产路径）
3. 媒体代理面    /img /audio /upload/{id} /health

运行（必须用装了 mcp 2.x 的解释器）：
    D:\\AI\\qqbot-mcp\\Scripts\\python.exe server.py --port 8899

向后兼容：旧版 D:\\AI\\qqbot-mcp\\comfyui_mcp.py 的 4 个工具名
comfyui_generate / comfyui_status / comfyui_list_models / tts_speak 全部保留，
返回 JSON 字符串（含旧字段 image_url / audio_url）。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import functools
import io
import json
import logging
import mimetypes
import os
import random
import re
import time
import urllib.parse
import wave
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import Field

import httpx
import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

try:  # 既支持 `python media_gateway/server.py`，也支持包方式导入
    from taskstore import (  # type: ignore[import-not-found]
        DurableTaskStore, TaskConflict, TaskRecord, TERMINAL,
        STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED,
        STATUS_PENDING, STATUS_RUNNING, STATUS_UNKNOWN, normalize_status,
    )
except ImportError:  # pragma: no cover
    from .taskstore import (  # type: ignore[no-redef]
        DurableTaskStore, TaskConflict, TaskRecord, TERMINAL,
        STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED,
        STATUS_PENDING, STATUS_RUNNING, STATUS_UNKNOWN, normalize_status,
    )

# ---------------------------------------------------------------------------
# 常量 / 路径
# ---------------------------------------------------------------------------

VERSION = "2.0.0"
GATEWAY_DIR = os.path.dirname(os.path.abspath(__file__))
MOBIUS_ROOT = os.path.dirname(GATEWAY_DIR)

DEFAULT_WORKFLOWS_DIR = r"D:\AI\comic\workflows"
DEFAULT_CHECKPOINTS_DIR = (
    r"D:\AI\comic\ComfyUI_windows_portable\ComfyUI\models\checkpoints"
)
DEFAULT_LORAS_DIR = r"D:\AI\comic\ComfyUI_windows_portable\ComfyUI\models\loras"
DEFAULT_COMFY_OUTPUT_DIR = r"D:\AI\comic\ComfyUI_windows_portable\ComfyUI\output"

# 旧版工作流名 -> 文件名（向后兼容）
WORKFLOW_MAP = {
    "portrait": "comic-portrait.json",
    "landscape": "comic-landscape.json",
    "panel": "comic-panel-faceid.json",
    "upscale": "comic-upscale.json",
    "removebg": "comic-remove-bg.json",
    "qwen": "comic-qwen.json",
    "flux3": "comic-flux3.json",
}
# 千问 Image 是新架构（UNETLoader + CLIPLoader type=qwen_image + 专用 VAE +
# SamplerCustomAdvanced/ManualSigmas），没有 CheckpointLoaderSimple，
# 因此不能叠加 SDXL 的角色 LoRA，也不接受 character 档案。
QWEN_WORKFLOWS = {"qwen"}
# 需要输入参考图 / 额外模型的工作流：MVP 只支持单张参考图注入
IMAGE_INPUT_WORKFLOWS = {"upscale", "removebg"}
# MVP 明确不做（§10）：多参考图 FaceID + ControlNet
UNSUPPORTED_WORKFLOWS = {"panel"}

LORA_WEIGHT_MIN = 0.0
LORA_WEIGHT_MAX = 1.5
MAX_VIEW_TIMEOUT = 600.0
MAX_TASK_TIMEOUT = 1800.0  # P0.3：worker 执行上限（与视图等待分离）

START_TS = time.time()
log = logging.getLogger("mobius.gateway")

# 媒体活动时间：Core 的「闲置休眠」必须看到这里，
# 否则 QQ 走 LangBot→网关 生图时 Core 完全不知情，会在刚出图后把 ComfyUI 睡掉。
LAST_ACTIVITY_TS = START_TS


def touch_activity() -> None:
    global LAST_ACTIVITY_TS
    LAST_ACTIVITY_TS = time.time()


# ---------------------------------------------------------------------------
# 配置（mobius/config.json + 环境变量）
# ---------------------------------------------------------------------------


def _base_url(value: Any, default_port: int) -> str:
    """把 health_url / port 归一成 scheme://host:port。"""
    if isinstance(value, str) and value.strip():
        parts = urllib.parse.urlsplit(value.strip())
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    return f"http://127.0.0.1:{default_port}"


def _port_of(url: str) -> int:
    try:
        p = urllib.parse.urlsplit(url).port
        return int(p) if p else (443 if url.startswith("https") else 80)
    except Exception:
        return 0


def _env(name: str, default: Any = "") -> Any:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


class Settings:
    """网关配置。默认值 + mobius/config.json + 环境变量。"""

    def __init__(self) -> None:
        self.mobius_root = MOBIUS_ROOT
        self.config_path = str(
            _env("MOBIUS_CONFIG", os.path.join(MOBIUS_ROOT, "config.json"))
        )
        raw: dict[str, Any] = {}
        if os.path.isfile(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    raw = loaded
            except Exception as exc:  # pragma: no cover - 配置坏了不应阻断启动
                log.warning("读取配置失败 %s: %s", self.config_path, exc)
        self.raw = raw

        gw = raw.get("gateway") or {}
        core = raw.get("core") or {}
        services = raw.get("services") or {}
        comfy = services.get("comfyui") or {}
        kokoro = services.get("kokoro") or {}
        f5 = services.get("f5") or {}

        self.host = str(_env("MOBIUS_GATEWAY_HOST", gw.get("host", "0.0.0.0")))
        self.port = int(_env("MOBIUS_GATEWAY_PORT", gw.get("port", 8899)))
        self.comfy_url = str(
            _env(
                "MOBIUS_COMFYUI_URL",
                _base_url(comfy.get("health_url"), 8188),
            )
        ).rstrip("/")
        self.kokoro_url = str(
            _env("MOBIUS_KOKORO_URL", _base_url(kokoro.get("health_url"), 7870))
        ).rstrip("/")
        self.f5_url = str(
            _env("MOBIUS_F5_URL", _base_url(f5.get("health_url"), int(f5.get("port", 7871))))
        ).rstrip("/")
        core_host = str(core.get("host", "127.0.0.1"))
        if core_host in ("0.0.0.0", "::", ""):
            core_host = "127.0.0.1"
        self.core_url = str(
            _env("MOBIUS_CORE_URL", f"http://{core_host}:{int(core.get('port', 8900))}")
        ).rstrip("/")
        self.core_api_key = str(_env("MOBIUS_CORE_API_KEY", core.get("api_key", "<REDACTED>")))

        self.workflows_dir = str(_env("MOBIUS_WORKFLOWS_DIR", DEFAULT_WORKFLOWS_DIR))
        self.checkpoints_dir = str(_env("MOBIUS_CHECKPOINTS_DIR", DEFAULT_CHECKPOINTS_DIR))
        self.loras_dir = str(_env("MOBIUS_LORAS_DIR", DEFAULT_LORAS_DIR))
        self.comfy_output_dir = str(_env("MOBIUS_COMFY_OUTPUT_DIR", DEFAULT_COMFY_OUTPUT_DIR))

        chars_dir = raw.get("characters_dir") or os.path.join("data", "characters")
        self.characters_dir = (
            chars_dir if os.path.isabs(str(chars_dir)) else os.path.join(MOBIUS_ROOT, str(chars_dir))
        )
        self.tts_dir = os.path.join(GATEWAY_DIR, "tts_output")
        self.uploads_dir = os.path.join(GATEWAY_DIR, "uploads")
        self.logs_dir = os.path.join(GATEWAY_DIR, "logs")

        self.public_base = str(
            _env("MOBIUS_GATEWAY_PUBLIC_URL", f"http://host.docker.internal:{self.port}")
        ).rstrip("/")

        self.comfy_start_timeout = float(
            _env("MOBIUS_COMFY_START_TIMEOUT", comfy.get("start_timeout_sec", 240))
        )
        self.view_timeout = float(_env("MOBIUS_VIEW_TIMEOUT", 180))
        # P0.3：worker 执行上限与「API 视图等待」分离，避免长生成被视图超时误杀
        self.task_timeout = float(_env("MOBIUS_TASK_TIMEOUT", 900))
        # P0.4-B：同一任务的两次有效状态查询最小间隔（毫秒）
        self.poll_min_interval_ms = float(_env("MOBIUS_POLL_MIN_INTERVAL_MS", 5000))
        self.poll_interval = float(_env("MOBIUS_POLL_INTERVAL", 1.5))
        self.max_voice_bytes = int(_env("MOBIUS_MAX_VOICE_BYTES", 8 * 1024 * 1024))
        self.tts_timeout = float(_env("MOBIUS_TTS_TIMEOUT", 120))
        self.concurrency = 1  # 契约：并发 1，GPU 串行

    def apply_port(self, port: int) -> None:
        if not _env("MOBIUS_GATEWAY_PORT") and "MOBIUS_GATEWAY_PUBLIC_URL" not in os.environ:
            self.public_base = f"http://host.docker.internal:{int(port)}"
        self.port = int(port)

    def ensure_dirs(self) -> None:
        for path in (self.tts_dir, self.uploads_dir, self.logs_dir):
            os.makedirs(path, exist_ok=True)


settings = Settings()


def _setup_logging(cfg: Settings) -> None:
    os.makedirs(cfg.logs_dir, exist_ok=True)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)
    try:
        file_handler = logging.FileHandler(
            os.path.join(cfg.logs_dir, "gateway.log"), encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except Exception:  # pragma: no cover
        pass
    # ComfyUI 轮询是 1.5s 一次，httpx 的 INFO 日志会把 gateway.log 灌爆
    # （实测 10 分钟 40KB → 一天几百 MB）。降噪，故障排查看 mobius.gateway 自己的日志。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("mcp.server.streamable_http").setLevel(logging.WARNING)


settings.ensure_dirs()
_setup_logging(settings)


# ---------------------------------------------------------------------------
# 通用错误 / 工具
# ---------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, error: str, hint: str = "", status: int = 400, **extra: Any) -> None:
        super().__init__(error)
        self.error = error
        self.hint = hint
        self.status = status
        self.extra = extra


class TaskCanceled(Exception):
    """任务被取消（不是失败）。"""


class _TaskCanceled(TaskCanceled):
    pass


def _now() -> float:
    return round(time.time(), 3)


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _error_payload(err: ApiError) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": err.error}
    if err.hint:
        payload["hint"] = err.hint
    if err.extra:
        payload.update(err.extra)
    return payload


def _wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            rate = w.getframerate() or 24000
            return round(w.getnframes() / float(rate), 2)
    except Exception:
        return round(len(data) / 32000.0, 2)


async def _url_ok(url: str, timeout: float = 2.0, headers: dict | None = None) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, headers=headers or {})
            return 200 <= resp.status_code < 500
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 工作流：加载 / 覆盖 / LoRA 链式注入（契约 §6）
# ---------------------------------------------------------------------------


def resolve_workflow_path(cfg: Settings, name: str) -> str:
    name = str(name or "").strip()
    if name in WORKFLOW_MAP:
        path = os.path.join(cfg.workflows_dir, WORKFLOW_MAP[name])
        if not os.path.isfile(path):
            raise ApiError(
                "workflow_not_found",
                f"工作流文件不存在: {path}（可用 MOBIUS_WORKFLOWS_DIR 覆盖）",
                500,
            )
        return path
    if name.endswith(".json"):
        path = os.path.join(cfg.workflows_dir, os.path.basename(name))
        if os.path.isfile(path):
            return path
    raise ApiError(
        "unknown_workflow",
        f"未知工作流 {name!r}；可用: {', '.join(WORKFLOW_MAP)}",
    )


def load_workflow_nodes(cfg: Settings, name: str) -> dict[str, dict]:
    path = resolve_workflow_path(cfg, name)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # 契约 §6：旧格式 {"nodes": {id: node}}（dict 不是 list）；顺手兼容 list 格式
    nodes = data.get("nodes", data) if isinstance(data, dict) else data
    if isinstance(nodes, list):
        converted: dict[str, dict] = {}
        for node in nodes:
            if not isinstance(node, dict):
                continue
            nid = str(node.get("id", len(converted) + 1))
            converted[nid] = {
                k: v for k, v in node.items() if k in ("class_type", "inputs", "_meta")
            }
        nodes = converted
    if not isinstance(nodes, dict) or not nodes:
        raise ApiError("invalid_workflow", f"工作流 {name} 节点为空或格式不支持", 500)
    return copy.deepcopy(nodes)


def _node_title(node: dict) -> str:
    return str((node.get("_meta") or {}).get("title") or "")


# ---------------------------------------------------------------------------
# 通用模型注册表（§ 新增）
# 目的：新增模型只加一条 JSON + 一个工作流模板，不动代码。
# 模型 = 「生成档案(profile)」：决定用哪个工作流模板、默认尺寸、采样参数、
# 是否支持角色/LoRA。查询与调用都走 id / 别名。
# ---------------------------------------------------------------------------


def models_file(cfg: Settings) -> str:
    return str(_env("MOBIUS_MODELS_FILE", os.path.join(cfg.workflows_dir, "models.json")))


def load_model_registry(cfg: Settings) -> list[dict[str, Any]]:
    path = models_file(cfg)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        log.warning("模型注册表不存在: %s（只有内置工作流可用）", path)
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning("模型注册表读取失败 %s: %s", path, exc)
        return []
    entries = data.get("models") if isinstance(data, dict) else data
    out: list[dict[str, Any]] = []
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("id"):
            out.append(entry)
    return out


def find_model(cfg: Settings, key: str) -> dict[str, Any] | None:
    needle = str(key or "").strip().lower()
    if not needle:
        return None
    for entry in load_model_registry(cfg):
        names = [entry.get("id"), entry.get("name")] + list(entry.get("aliases") or [])
        if any(str(n).strip().lower() == needle for n in names if n):
            return entry
    return None


def public_models(cfg: Settings) -> list[dict[str, Any]]:
    """给模型看的精简视图（不含内部路径）。"""
    out = []
    for e in load_model_registry(cfg):
        out.append({
            "id": e.get("id"),
            "name": e.get("name"),
            "aliases": e.get("aliases") or [],
            "workflow": e.get("workflow"),
            "style": e.get("style", ""),
            "size": e.get("size"),
            "supports_character": bool(e.get("supports_character")),
            "supports_lora": bool(e.get("supports_lora")),
            "notes": e.get("notes", ""),
        })
    return out


def apply_model_params(nodes: dict[str, dict], params: dict[str, Any]) -> dict[str, dict]:
    """把档案里的采样参数写进图。只覆盖档案显式给出的键。"""
    if not params:
        return nodes
    for node in nodes.values():
        cls = node.get("class_type", "")
        inputs = node.setdefault("inputs", {})
        if cls == "KSampler":
            for key in ("steps", "cfg", "sampler_name", "scheduler", "denoise"):
                if key in params:
                    inputs[key] = params[key]
        elif cls == "CFGGuider":
            if "cfg" in params:
                inputs["cfg"] = params["cfg"]
        elif cls == "ManualSigmas":
            if params.get("sigmas"):
                inputs["sigmas"] = params["sigmas"]
    return nodes


def apply_overrides(
    nodes: dict[str, dict],
    prompt: str,
    negative: str,
    seed: int | None,
    width: int | None,
    height: int | None,
    filename_prefix: str,
    checkpoint: str | None = None,
) -> dict[str, dict]:
    """正向/负向/seed/尺寸/文件名前缀/checkpoint 覆盖（与旧版语义一致）。"""
    text_nodes = [
        (nid, node) for nid, node in nodes.items() if node.get("class_type") == "CLIPTextEncode"
    ]
    pos_ids = [nid for nid, node in text_nodes if "正向" in _node_title(node) or _node_title(node).lower() == "positive"]
    neg_ids = [nid for nid, node in text_nodes if "负向" in _node_title(node) or _node_title(node).lower() == "negative"]
    if not pos_ids and text_nodes:
        pos_ids = [text_nodes[0][0]]
    if not neg_ids and len(text_nodes) > 1:
        neg_ids = [text_nodes[1][0]]

    for nid, node in nodes.items():
        cls = node.get("class_type", "")
        inputs = node.setdefault("inputs", {})
        if nid in pos_ids:
            inputs["text"] = prompt
        elif nid in neg_ids and negative:
            inputs["text"] = negative
        elif cls == "KSampler":
            inputs["seed"] = seed if seed is not None else random.randint(0, 2**31 - 1)
        elif cls == "RandomNoise":
            # 千问走 SamplerCustomAdvanced，种子在 RandomNoise 上而不是 KSampler
            if seed is not None:
                inputs["noise_seed"] = seed
        elif cls == "TextEncodeQwenImage21":
            # 千问把正向/负向放在同一个节点里
            inputs["prompt"] = prompt
            if negative:
                inputs["negative_prompt"] = negative
        elif cls in ("EmptyLatentImage", "EmptyFlux2LatentImage", "EmptySD3LatentImage"):
            if width is not None:
                inputs["width"] = int(width)
            if height is not None:
                inputs["height"] = int(height)
        elif cls == "ModelSamplingFlux":
            # FLUX 系的 shift 计算依赖画布尺寸，尺寸变了这里也要跟着变
            if width is not None:
                inputs["width"] = int(width)
            if height is not None:
                inputs["height"] = int(height)
        elif cls == "SaveImage":
            inputs["filename_prefix"] = filename_prefix
        elif cls == "CheckpointLoaderSimple" and checkpoint:
            inputs["ckpt_name"] = checkpoint
    return nodes


def normalize_loras(raw: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """去重（同名取最大权重）+ 权重截断到 0–1.5，返回 (loras, warnings)。"""
    warnings: list[str] = []
    if raw is None:
        return [], warnings
    if isinstance(raw, str):
        parsed = parse_loras_param(raw)
        raw = parsed if parsed is not None else []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ApiError("invalid_loras", "loras 必须是数组，如 [{\"name\":..., \"weight\":0.75}]")

    merged: dict[str, float] = {}
    for item in raw:
        if isinstance(item, str):
            name, weight = item.strip(), 1.0
        elif isinstance(item, dict):
            name = str(item.get("name") or item.get("lora") or item.get("file") or "").strip()
            weight = item.get("weight", item.get("strength", 1.0))
        else:
            warnings.append(f"忽略无法解析的 LoRA 项: {item!r}")
            continue
        if not name:
            warnings.append("忽略空 LoRA 名称")
            continue
        try:
            w = float(weight)
        except (TypeError, ValueError):
            warnings.append(f"LoRA {name} 权重 {weight!r} 非法，按 1.0 处理")
            w = 1.0
        if w < LORA_WEIGHT_MIN:
            warnings.append(f"LoRA {name} 权重 {w} < {LORA_WEIGHT_MIN}，截断为 {LORA_WEIGHT_MIN}")
            w = LORA_WEIGHT_MIN
        if w > LORA_WEIGHT_MAX:
            warnings.append(f"LoRA {name} 权重 {w} > {LORA_WEIGHT_MAX}，截断为 {LORA_WEIGHT_MAX}")
            w = LORA_WEIGHT_MAX
        if name in merged:
            warnings.append(f"重复 LoRA {name} 去重，取最大权重")
            merged[name] = max(merged[name], w)
        else:
            merged[name] = w
    return [{"name": name, "weight": round(w, 4)} for name, w in merged.items()], warnings


def parse_loras_param(value: Any) -> list[Any] | None:
    """兼容 MCP / HTTP 里 loras 传 JSON 字符串、'name:weight,name2' 等写法。"""
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except Exception:
        pass
    items: list[Any] = []
    for part in re.split(r"[,;\n]", text):
        part = part.strip()
        if not part:
            continue
        weight = 1.0
        match = re.match(r"^(.*?)[:@]\s*(-?\d+(?:\.\d+)?)$", part)
        if match:
            part = match.group(1).strip()
            weight = float(match.group(2))
        items.append({"name": part, "weight": weight})
    return items


def inject_loras(
    nodes: dict[str, dict], loras: list[dict[str, Any]]
) -> tuple[dict[str, dict], list[dict[str, Any]], list[str]]:
    """按 loras[] 顺序链式插入 LoraLoader 并重接 model/clip（契约 §6）。

    CheckpointLoaderSimple -> LoraLoader(lora_1) -> ... -> LoraLoader(lora_N)
    原引用 ["1", 0](MODEL) / ["1", 1](CLIP) 改为链末端；["1", 2](VAE) 保持不动。
    """
    warnings: list[str] = []
    if not loras:
        return nodes, [], warnings
    ckpt_id = next(
        (
            nid
            for nid, node in nodes.items()
            if node.get("class_type") == "CheckpointLoaderSimple"
        ),
        None,
    )
    if ckpt_id is None:
        raise ApiError(
            "workflow_no_checkpoint",
            "工作流里没有 CheckpointLoaderSimple 节点，无法注入 LoRA",
            500,
        )

    model_ref: list[Any] = [ckpt_id, 0]
    clip_ref: list[Any] = [ckpt_id, 1]
    applied: list[dict[str, Any]] = []
    for index, lora in enumerate(loras, start=1):
        node_id = f"lora_{index}"
        if node_id in nodes:
            node_id = f"lora_{index}_{uuid.uuid4().hex[:4]}"
        nodes[node_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "model": list(model_ref),
                "clip": list(clip_ref),
                "lora_name": lora["name"],
                "strength_model": lora["weight"],
                "strength_clip": lora["weight"],
            },
            "_meta": {"title": f"LoRA {index}: {lora['name']} ({lora['weight']})"},
        }
        applied.append({"node": node_id, **lora})
        model_ref = [node_id, 0]
        clip_ref = [node_id, 1]

    lora_ids = {item["node"] for item in applied}
    for nid, node in nodes.items():
        if nid == ckpt_id or nid in lora_ids:
            continue
        inputs = node.get("inputs") or {}
        for key, value in list(inputs.items()):
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and isinstance(value[1], int)
                and value[0] == ckpt_id
            ):
                if value[1] == 0:  # MODEL
                    inputs[key] = list(model_ref)
                elif value[1] == 1:  # CLIP
                    inputs[key] = list(clip_ref)
                # 其余（如 VAE 的 ["1", 2]）保持原样
    return nodes, applied, warnings


def build_nodes(cfg: Settings, spec: dict[str, Any]) -> dict[str, dict]:
    nodes = load_workflow_nodes(cfg, spec["workflow"])
    nodes = apply_overrides(
        nodes,
        prompt=spec["prompt"],
        negative=spec["negative"],
        seed=spec["seed"],
        width=spec["width"],
        height=spec["height"],
        filename_prefix=f"mobius_{spec['workflow']}",
        checkpoint=spec["checkpoint"],
    )
    nodes = apply_model_params(nodes, spec.get("params") or {})
    nodes, applied, warnings = inject_loras(nodes, spec["loras"])
    spec["loras_applied"] = applied
    spec["warnings"].extend(warnings)
    return nodes


def _find_load_image_nodes(nodes: dict[str, dict]) -> list[str]:
    return [nid for nid, node in nodes.items() if node.get("class_type") == "LoadImage"]


# ---------------------------------------------------------------------------
# 角色档案（§4.5）
# ---------------------------------------------------------------------------


def load_characters(cfg: Settings) -> list[dict[str, Any]]:
    characters: list[dict[str, Any]] = []
    directory = cfg.characters_dir
    if not os.path.isdir(directory):
        return characters
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(directory, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("id", os.path.splitext(filename)[0])
                characters.append(data)
        except Exception as exc:
            log.warning("读取角色档案失败 %s: %s", path, exc)
    return characters


def find_character(cfg: Settings, key: str) -> dict[str, Any] | None:
    needle = str(key or "").strip().lower()
    if not needle:
        return None
    for character in load_characters(cfg):
        candidates = [character.get("id", ""), character.get("name", "")]
        candidates.extend(character.get("aliases") or [])
        if any(str(c).strip().lower() == needle for c in candidates if c):
            return character
    return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_spec(cfg: Settings, body: dict[str, Any], kind: str = "image") -> dict[str, Any]:
    """解析请求：model 档案 > 显式参数 > 角色档案 > 工作流默认。"""
    warnings: list[str] = []
    profile: dict[str, Any] | None = None
    model_key = str(body.get("model") or "").strip()
    if model_key:
        profile = find_model(cfg, model_key)
        if profile is None:
            available = ", ".join(str(m.get("id")) for m in load_model_registry(cfg))
            raise ApiError(
                "unknown_model",
                f"未找到模型 {model_key!r}；可用: {available or '(注册表为空)'}",
            )
    character_key = str(body.get("character") or "").strip()
    character: dict[str, Any] | None = None
    if character_key:
        character = find_character(cfg, character_key)
        if character is None:
            available = ", ".join(c.get("id", "?") for c in load_characters(cfg))
            raise ApiError(
                "unknown_character",
                f"未找到角色 {character_key!r}；可用: {available or '(无)'}",
            )

    policy = (character or {}).get("media_policy") or {}
    policy_key = "allow_image" if kind == "image" else "allow_voice"
    if character and policy.get(policy_key) is False:
        raise ApiError(
            "media_disabled",
            f"角色 {character.get('id')} 的 media_policy.{policy_key} = false，禁止该媒体类型",
            403,
        )

    # workflow：model 档案 > 显式 workflow > 角色档案 > 默认
    if profile and profile.get("workflow"):
        workflow = str(profile["workflow"])
    elif body.get("workflow"):
        workflow = str(body["workflow"]).strip()
    elif character and character.get("workflow"):
        workflow = str(character["workflow"])
    else:
        workflow = "portrait"
    resolve_workflow_path(cfg, workflow)  # 尽早校验，避免入队后才失败
    if workflow in UNSUPPORTED_WORKFLOWS:
        raise ApiError(
            "workflow_unsupported",
            f"工作流 {workflow} 需要多张参考图 + FaceID/ControlNet，MVP 范围外（契约 §10）",
        )
    if workflow in QWEN_WORKFLOWS:
        # 千问没有 CheckpointLoaderSimple，角色档案的 checkpoint/LoRA 都注入不了，
        # 与其静默丢弃，不如直接说清楚。
        if character:
            raise ApiError(
                "workflow_no_character",
                f"工作流 {workflow}（千问 Image）不支持角色档案：{character.get('name') or character.get('id')} 的 "
                "LoRA 与 checkpoint 是 SDXL 专用的。请去掉 character，直接用 prompt 描述角色；"
                "要角色一致性请用 workflow=portrait",
                400,
            )
        if body.get("loras"):
            raise ApiError(
                "workflow_no_lora",
                f"工作流 {workflow}（千问 Image）不支持 LoRA 注入（现有 LoRA 均为 SDXL 训练）",
                400,
            )
        if body.get("checkpoint"):
            raise ApiError(
                "workflow_no_checkpoint_param",
                f"工作流 {workflow}（千问 Image）用 UNETLoader 加载模型，不接受 checkpoint 参数",
                400,
            )

    # checkpoint
    checkpoint = str(body.get("checkpoint") or "").strip() or None
    if not checkpoint and profile and profile.get("checkpoint"):
        checkpoint = str(profile["checkpoint"])
    if not checkpoint and character and character.get("checkpoint"):
        checkpoint = str(character["checkpoint"])

    # loras：显式给了（哪怕是空数组）就覆盖角色档案
    if "loras" in body and body.get("loras") is not None:
        raw_loras = body.get("loras")
        lora_source = "explicit"
    elif character and character.get("loras") is not None:
        raw_loras = character.get("loras")
        lora_source = f"character:{character.get('id')}"
    else:
        raw_loras = []
        lora_source = "default"
    loras, lora_warnings = normalize_loras(raw_loras)
    warnings.extend(lora_warnings)

    # prompt：角色 style_prompt 打底 + 用户 prompt 追加
    user_prompt = str(body.get("prompt") or body.get("text") or "").strip()
    style_prompt = str((character or {}).get("style_prompt") or "").strip()
    if style_prompt and user_prompt:
        prompt = f"{style_prompt}, {user_prompt}"
    elif style_prompt:
        prompt = style_prompt
    else:
        prompt = user_prompt
    if not prompt:
        raise ApiError("missing_prompt", "prompt 不能为空（或指定一个带 style_prompt 的 character）")

    negative = str(body.get("negative") or "").strip()
    if not negative and character and character.get("negative_prompt"):
        negative = str(character["negative_prompt"])

    # size
    char_size = (character or {}).get("size")
    char_w = _as_int(char_size[0]) if isinstance(char_size, (list, tuple)) and len(char_size) >= 2 else None
    char_h = _as_int(char_size[1]) if isinstance(char_size, (list, tuple)) and len(char_size) >= 2 else None
    width = _as_int(body.get("width"))
    height = _as_int(body.get("height"))
    if width is None:
        width = char_w
    if height is None:
        height = char_h
    prof_size = (profile or {}).get("size")
    if width is None and height is None and isinstance(prof_size, (list, tuple)) and len(prof_size) >= 2:
        width = _as_int(prof_size[0])
        height = _as_int(prof_size[1])
    if workflow in QWEN_WORKFLOWS:
        # 千问模板默认 1024x1024；显式写进 spec，结果里能回报真实尺寸，也便于复刻
        width = width or 1024
        height = height or 1024
    for key, value in (("width", width), ("height", height)):
        if value is not None and (value < 64 or value > 4096):
            raise ApiError("invalid_size", f"{key}={value} 超出 64–4096 范围")

    seed = _as_int(body.get("seed"))
    if seed is None:
        # 实际使用的种子必须在提交前就确定并落库，否则这张图永远复刻不出来
        # （之前是在 apply_overrides 里现随机、然后丢掉）。
        seed = random.randint(0, 2**31 - 1)

    # 参考图
    reference_image = str(
        body.get("reference_image") or body.get("reference_image_url") or ""
    ).strip()
    if workflow in IMAGE_INPUT_WORKFLOWS and not reference_image:
        raise ApiError(
            "reference_image_required",
            f"工作流 {workflow} 需要 reference_image（先 POST /api/upload 上传参考图）",
        )

    spec: dict[str, Any] = {
        "kind": kind,
        "model": (profile or {}).get("id"),
        "params": dict((profile or {}).get("params") or {}),
        "character": (character or {}).get("id") if character else None,
        "character_name": (character or {}).get("name") if character else None,
        "workflow": workflow,
        "checkpoint": checkpoint,
        "loras": loras,
        "lora_source": lora_source,
        "prompt": prompt,
        "user_prompt": user_prompt,
        "style_prompt": style_prompt,
        "negative": negative,
        "width": width,
        "height": height,
        "seed": seed,
        "reference_image": reference_image,
        "warnings": warnings,
        "loras_applied": [],
    }
    return spec


# ---------------------------------------------------------------------------
# ComfyUI / Core / TTS 客户端
# ---------------------------------------------------------------------------


async def comfy_health(cfg: Settings, timeout: float = 3.0) -> tuple[bool, dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{cfg.comfy_url}/system_stats")
        if resp.status_code == 200:
            try:
                return True, resp.json()
            except Exception:
                return True, {}
        return False, {"status_code": resp.status_code, "text": resp.text[:300]}
    except Exception as exc:
        return False, {"error": str(exc)}


async def comfy_get_json(
    cfg: Settings, path: str, timeout: float = 20.0
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{cfg.comfy_url}{path}")
    if resp.status_code != 200:
        raise RuntimeError(f"ComfyUI GET {path} -> HTTP {resp.status_code}")
    return resp.json()


async def comfy_object_info(cfg: Settings, node_name: str, timeout: float = 20.0) -> dict[str, Any]:
    data = await comfy_get_json(cfg, f"/object_info/{node_name}", timeout=timeout)
    return data.get(node_name) or {}


def _object_info_choices(required: dict[str, Any], key: str) -> list[str]:
    """ComfyUI object_info 的枚举形如 [[name1, name2, ...], {tooltip}]。"""
    value = required.get(key)
    if not isinstance(value, list) or not value:
        return []
    first = value[0]
    if isinstance(first, list):
        return [str(item) for item in first if isinstance(item, str)]
    if isinstance(first, str):
        return [str(item) for item in value if isinstance(item, str)]
    return []


async def comfy_list_models(cfg: Settings) -> tuple[list[str], str]:
    """返回 (checkpoint 列表, 来源)。ComfyUI 不可达时回退到模型目录。"""
    try:
        info = await comfy_object_info(cfg, "CheckpointLoaderSimple")
        required = ((info.get("input") or {}).get("required") or {})
        names = _object_info_choices(required, "ckpt_name")
        if names:
            return names, "comfyui"
    except Exception as exc:
        log.warning("从 ComfyUI 列模型失败，回退目录: %s", exc)
    names: list[str] = []
    if os.path.isdir(cfg.checkpoints_dir):
        for filename in sorted(os.listdir(cfg.checkpoints_dir)):
            if filename.lower().endswith((".safetensors", ".ckpt", ".pt")):
                names.append(filename)
    return names, "directory"


async def comfy_list_lora_names(cfg: Settings) -> tuple[list[str], str]:
    try:
        info = await comfy_object_info(cfg, "LoraLoader")
        required = ((info.get("input") or {}).get("required") or {})
        names = _object_info_choices(required, "lora_name")
        if names:
            return names, "comfyui"
    except Exception as exc:
        log.warning("从 ComfyUI 列 LoRA 失败，回退目录: %s", exc)
    names = []
    if os.path.isdir(cfg.loras_dir):
        for root, _dirs, files in os.walk(cfg.loras_dir):
            for filename in files:
                if filename.lower().endswith((".safetensors", ".pt", ".ckpt")):
                    rel = os.path.relpath(os.path.join(root, filename), cfg.loras_dir)
                    names.append(rel.replace("\\", "/"))
    return sorted(names), "directory"


def _lora_tags(name: str) -> list[str]:
    stem = os.path.splitext(os.path.basename(name))[0]
    tokens = re.split(r"[_.\-\s]+", stem)
    tags: list[str] = []
    for token in tokens:
        if len(token) >= 3 and token.lower() not in {"safetensors", "lora"} and token not in tags:
            tags.append(token)
    return tags


def _lora_file_stats(cfg: Settings, name: str) -> tuple[int | None, float | None, str | None]:
    candidate = os.path.join(cfg.loras_dir, name.replace("/", os.sep))
    if os.path.isfile(candidate):
        stat = os.stat(candidate)
        return stat.st_size, round(stat.st_mtime, 3), candidate
    return None, None, None


async def comfy_loras(cfg: Settings, query: str = "") -> dict[str, Any]:
    names, source = await comfy_list_lora_names(cfg)
    needle = str(query or "").strip().lower()
    loras: list[dict[str, Any]] = []
    for name in names:
        if needle and needle not in name.lower():
            continue
        size, mtime, path = _lora_file_stats(cfg, name)
        loras.append(
            {
                "name": name,
                "size": size,
                "mtime": mtime,
                "tags": _lora_tags(name),
                "path": path,
            }
        )
    return {"loras": loras, "count": len(loras), "query": query, "source": source}


async def comfy_get_queue(cfg: Settings, timeout: float = 5.0) -> dict[str, Any]:
    try:
        return await comfy_get_json(cfg, "/queue", timeout=timeout)
    except Exception:
        return {}


async def comfy_submit(cfg: Settings, nodes: dict[str, dict]) -> str:
    payload = {"prompt": nodes, "client_id": "mobius-media-gateway"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(f"{cfg.comfy_url}/prompt", json=payload)
    if resp.status_code != 200:
        detail = resp.text[:1200]
        try:
            data = resp.json()
            detail = _json(data)[:1200]
        except Exception:
            pass
        raise RuntimeError(f"ComfyUI /prompt 拒绝 (HTTP {resp.status_code}): {detail}")
    data = resp.json()
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI 未返回 prompt_id: {_json(data)[:500]}")
    return str(prompt_id)


def _summarize_comfy_messages(status: dict[str, Any]) -> str:
    messages = status.get("messages") or []
    for item in messages:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        kind, payload = item[0], item[1]
        if kind == "execution_error" and isinstance(payload, dict):
            node = payload.get("node_id")
            node_type = payload.get("node_type")
            message = payload.get("exception_message") or payload.get("exception_type")
            return f"node {node}({node_type}): {str(message).strip()}"
    return _json(status)[:500]


async def comfy_wait_output(
    cfg: Settings,
    prompt_id: str,
    task: "Task | None" = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    limit = min(timeout or cfg.view_timeout, MAX_TASK_TIMEOUT)
    deadline = time.time() + limit
    async with httpx.AsyncClient(timeout=20.0) as client:
        while time.time() < deadline:
            if task is not None:
                _check_cancel(task)
            try:
                resp = await client.get(f"{cfg.comfy_url}/history/{prompt_id}")
            except Exception as exc:
                log.warning("轮询 ComfyUI history 失败: %s", exc)
                await asyncio.sleep(cfg.poll_interval)
                continue
            if resp.status_code == 200:
                data = resp.json()
                if prompt_id in data:
                    entry = data[prompt_id]
                    outputs = entry.get("outputs") or {}
                    for node_id, output in outputs.items():
                        images = (output or {}).get("images") or []
                        if images:
                            image = images[0]
                            return {
                                "node_id": node_id,
                                "filename": image.get("filename", ""),
                                "subfolder": image.get("subfolder", ""),
                                "type": image.get("type", "output"),
                                "prompt": entry.get("prompt", {}),
                            }
                    status = entry.get("status") or {}
                    if status.get("status_str") == "error":
                        raise RuntimeError(
                            "ComfyUI 执行失败: " + _summarize_comfy_messages(status)
                        )
            await asyncio.sleep(cfg.poll_interval)
    raise TimeoutError(
        f"等待 ComfyUI 输出超时（{limit:.0f}s），prompt_id={prompt_id}"
    )


async def comfy_cancel_prompt(cfg: Settings, prompt_id: str | None) -> str:
    """取消已在 ComfyUI 里的任务：pending 从 /queue 删，running 走 /interrupt。"""
    if not prompt_id:
        return "no_remote_id"
    queue = await comfy_get_queue(cfg)
    running_ids: list[str] = []
    pending_ids: list[str] = []
    for item in queue.get("queue_running") or []:
        if isinstance(item, (list, tuple)) and len(item) > 1:
            running_ids.append(str(item[1]))
    for item in queue.get("queue_pending") or []:
        if isinstance(item, (list, tuple)) and len(item) > 1:
            pending_ids.append(str(item[1]))
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if prompt_id in pending_ids:
                await client.post(f"{cfg.comfy_url}/queue", json={"delete": [prompt_id]})
                return "deleted_from_queue"
            if prompt_id in running_ids:
                await client.post(f"{cfg.comfy_url}/interrupt")
                return "interrupted"
            # 兜底：history 还没有但队列已空，尝试删一次（ComfyUI 对不存在的 id 无害）
            await client.post(f"{cfg.comfy_url}/queue", json={"delete": [prompt_id]})
            return "not_in_queue"
    except Exception as exc:
        log.warning("取消 ComfyUI 任务失败 %s: %s", prompt_id, exc)
        return f"cancel_failed: {exc}"


async def _core_ensure(cfg: Settings) -> dict[str, Any]:
    """请求 Core 唤醒 ComfyUI：优先 /v1/media/ensure，兼容 /api/media/wake。"""
    headers = {"Authorization": f"Bearer {cfg.core_api_key}"}
    last: dict[str, Any] = {
        "ok": False,
        "error": "core_unavailable",
        "detail": "Core 未响应唤醒请求",
    }
    for path in ("/v1/media/ensure", "/api/media/wake"):
        url = f"{cfg.core_url}{path}"
        try:
            timeout = httpx.Timeout(cfg.comfy_start_timeout + 30, connect=3.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    url,
                    json={"source": "media_gateway", "reason": "media_gateway_generate"},
                    headers=headers,
                )
            if resp.status_code == 404:
                last = {"ok": False, "url": url, "status_code": 404, "error": "core_route_404"}
                continue
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "url": url,
                    "status_code": resp.status_code,
                    "error": "core_rejected",
                    "body": resp.text[:500],
                }
            try:
                data = resp.json()
            except Exception:
                data = {}
            return {"ok": True, "url": url, "data": data}
        except Exception as exc:
            last = {"ok": False, "url": url, "error": f"core_unreachable: {exc}"}
    return last


def _comfy_hint(ensure: dict[str, Any]) -> str:
    core = ensure.get("core") or {}
    core_detail = _json(core)[:400] if core else "(未尝试)"
    return (
        "ComfyUI 不可达且自动唤醒失败。请确认 Möbius Core (8900) 已启动并能处理 "
        "POST /v1/media/ensure，或手动启动 ComfyUI 后重试。"
        f" core={core_detail}"
    )


async def ensure_comfy(cfg: Settings, wait_sec: float | None = None) -> dict[str, Any]:
    """ComfyUI 不可达时调 Core ensure，成功后等它就绪。返回结果 dict。"""
    ok, info = await comfy_health(cfg, timeout=2.0)
    if ok:
        return {"ok": True, "state": "ready", "action": "already_ready", "seconds": 0.0}
    started = time.time()
    core_result = await _core_ensure(cfg)
    if not core_result.get("ok"):
        return {
            "ok": False,
            "error": "comfyui_unavailable",
            "hint": _comfy_hint({"core": core_result}),
            "core": core_result,
            "comfy": info,
        }
    wait = float(wait_sec if wait_sec is not None else cfg.comfy_start_timeout)
    deadline = time.time() + wait
    while time.time() < deadline:
        ok, _ = await comfy_health(cfg, timeout=3.0)
        if ok:
            return {
                "ok": True,
                "state": "ready",
                "action": "core_ensure",
                "seconds": round(time.time() - started, 1),
                "core": core_result,
            }
        await asyncio.sleep(2.0)
    return {
        "ok": False,
        "error": "comfyui_unavailable",
        "hint": (
            f"Core 已接受唤醒请求，但 ComfyUI 在 {wait:.0f}s 内仍未就绪"
            f"（{cfg.comfy_url}/system_stats）。请检查 ComfyUI 日志。"
        ),
        "core": core_result,
    }


async def require_comfy(cfg: Settings) -> dict[str, Any]:
    ensure = await ensure_comfy(cfg)
    if not ensure.get("ok"):
        raise ApiError(
            str(ensure.get("error") or "comfyui_unavailable"),
            str(ensure.get("hint") or ""),
            503,
        )
    return ensure


async def comfy_upload_image(cfg: Settings, data: bytes, filename: str) -> str:
    files = {"image": (filename, data, mimetypes.guess_type(filename)[0] or "image/png")}
    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{cfg.comfy_url}/upload/image",
            files=files,
            data={"overwrite": "true", "type": "input"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"ComfyUI /upload/image -> HTTP {resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    name = str(payload.get("name") or filename)
    subfolder = str(payload.get("subfolder") or "")
    return f"{subfolder}/{name}" if subfolder else name


# ---------------------------------------------------------------------------
# 异步任务队列（并发 1）
# ---------------------------------------------------------------------------


class _CancelView:
    """执行期间检查取消标记的轻量视图（每次读库，不依赖内存对象）。"""

    def __init__(self, store: "TaskStore", task_id: str) -> None:
        self._store = store
        self._task_id = task_id

    @property
    def cancel_requested(self) -> bool:
        rec = self._store.get(self._task_id)
        return bool(rec and rec.cancel_requested)

    @property
    def status(self) -> str:
        rec = self._store.get(self._task_id)
        return rec.status if rec is not None else STATUS_CANCELLED


class TaskStore(DurableTaskStore):
    """持久化任务队列（SQLite，并发 1）。

    P0.3 变更：内存 dict + asyncio.Queue 换成 SQLite 快照表 + 事务性 outbox；
    状态词汇统一为 pending/running/completed/failed/cancelled/submission_unknown；
    支持幂等键与重启恢复。执行逻辑（_run_image/_run_voice）保持原样。
    """

    def __init__(self, cfg: Settings) -> None:
        db_path = os.environ.get("MOBIUS_TASK_DB") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "data", "tasks.db"
        )
        super().__init__(db_path)
        self.cfg = cfg
        self._worker: asyncio.Task | None = None
        self._outbox_task: asyncio.Task | None = None
        self.active_id: str | None = None
        log.info("任务库：%s", db_path)

    # -- 生命周期 ---------------------------------------------------------
    async def bootstrap(self) -> None:
        """启动时恢复未完成任务，再拉起 worker 与 outbox 投递循环。"""
        try:
            stats = await self.recover(self._probe_comfy)
            log.info("任务恢复完成：%s", stats)
        except Exception:  # noqa: BLE001
            log.exception("任务恢复失败（继续启动）")

        # 恢复后仍是 running 的任务（ComfyUI 里还在跑）：重新接上等待，
        # 否则它们会永远停在 running，没人收尾。
        running = [t for t in self.list(limit=200) if t.status == STATUS_RUNNING]
        for task in running:
            asyncio.create_task(self._resume_running(task))
        if running:
            log.info("已重新接管 %d 个运行中的任务", len(running))

        self.ensure_worker()
        if self._outbox_task is None or self._outbox_task.done():
            self._outbox_task = asyncio.create_task(self._outbox_loop())

    async def _resume_running(self, task: TaskRecord) -> None:
        """重启后接管一个已在 ComfyUI 执行的图片任务。"""
        prompt_id = task.remote_id
        if not prompt_id or task.kind != "image":
            return
        cancel_view = _CancelView(self, task.id)
        try:
            output = await comfy_wait_output(
                self.cfg, prompt_id, cancel_view, timeout=self.cfg.task_timeout
            )
            if (self.get(task.id) or task).cancel_requested:
                self.finish(task.id, status=STATUS_CANCELLED)
                return
            result = self._build_image_result(task, task.spec, prompt_id, output, 0.0)
            self.finish(task.id, status=STATUS_COMPLETED, result=result)
            log.info("重启后接管的任务 %s 已完成：%s", task.id, result.get("filename"))
        except _TaskCanceled:
            self.finish(task.id, status=STATUS_CANCELLED)
        except Exception as exc:  # noqa: BLE001
            self.finish(
                task.id, status=STATUS_FAILED, error=str(exc),
                error_code=type(exc).__name__,
            )
            log.warning("重启后接管的任务 %s 失败：%s", task.id, exc)

    def ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._worker_loop())
            log.info("任务 worker 已启动（持久化队列，并发 1）")

    async def _outbox_loop(self) -> None:
        while True:
            try:
                if self.outbox_pending_count():
                    await self.flush_outbox(self._send_core_event)
            except Exception:  # noqa: BLE001
                log.exception("outbox 投递循环异常")
            await asyncio.sleep(5)

    async def _send_core_event(self, item: dict[str, Any]) -> bool:
        """把任务事实上报 Core。

        Core 侧接口在 P0.4 才授权添加；未配置时保留在 outbox（不丢、不阻塞）。
        """
        url = os.environ.get("MOBIUS_CORE_TASK_EVENT_URL", "").strip()
        if not url:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=item)
            return resp.status_code < 300
        except Exception as exc:  # noqa: BLE001
            log.warning("上报 Core 失败（保留重试）：%s", exc)
            return False

    # -- 恢复探测 ---------------------------------------------------------
    async def _probe_comfy(self, prompt_id: str) -> tuple[str, dict[str, Any] | None]:
        """判断 prompt_id 在 ComfyUI 里的状态：completed / active / absent / unknown。"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.cfg.comfy_url}/history/{prompt_id}")
                if resp.status_code == 200:
                    entry = (resp.json() or {}).get(prompt_id)
                    if entry:
                        outputs = entry.get("outputs") or {}
                        for _node_id, output in outputs.items():
                            images = (output or {}).get("images") or []
                            if images:
                                image = images[0]
                                query = urllib.parse.urlencode(
                                    {
                                        "filename": image.get("filename", ""),
                                        "subfolder": image.get("subfolder", ""),
                                        "type": image.get("type", "output"),
                                    }
                                )
                                return (
                                    "completed",
                                    {
                                        "url": f"{self.cfg.public_base}/img?{query}",
                                        "path": f"/img?{query}",
                                        "filename": image.get("filename", ""),
                                        "subfolder": image.get("subfolder", ""),
                                        "type": image.get("type", "output"),
                                        "prompt_id": prompt_id,
                                        "recovered": True,
                                    },
                                )
                queue_resp = await client.get(f"{self.cfg.comfy_url}/queue")
                if queue_resp.status_code == 200:
                    data = queue_resp.json() or {}
                    for bucket in ("queue_running", "queue_pending"):
                        for item in data.get(bucket) or []:
                            if isinstance(item, (list, tuple)) and len(item) > 1 and item[1] == prompt_id:
                                return ("active", None)
        except Exception as exc:  # noqa: BLE001
            log.warning("恢复探测失败 prompt_id=%s: %s", prompt_id, exc)
            return ("unknown", None)
        return ("absent", None)

    # -- worker -----------------------------------------------------------
    async def _worker_loop(self) -> None:
        while True:
            try:
                task = self.claim_next()
            except Exception:  # noqa: BLE001
                log.exception("领取任务失败")
                await asyncio.sleep(2)
                continue
            if task is None:
                await asyncio.sleep(1.0)
                continue

            self.active_id = task.id
            try:
                if task.cancel_requested:
                    self.finish(task.id, status=STATUS_CANCELLED)
                    continue
                if task.kind == "image":
                    result = await self._run_image(task)
                elif task.kind == "voice":
                    result = await self._run_voice(task)
                else:
                    raise ApiError("unknown_kind", f"未知任务类型 {task.kind}", 400)

                latest = self.get(task.id)
                if latest is not None and latest.cancel_requested:
                    self.finish(task.id, status=STATUS_CANCELLED, result=result)
                    log.info("task %s 执行完成但已请求取消，记为 cancelled", task.id)
                else:
                    self.finish(task.id, status=STATUS_COMPLETED, result=result)
                    log.info(
                        "task %s succeeded: %s",
                        task.id,
                        (result or {}).get("filename") or (result or {}).get("audio_url") or "",
                    )
            except _TaskCanceled:
                self.finish(task.id, status=STATUS_CANCELLED)
                log.info("task %s canceled", task.id)
            except ApiError as exc:
                self.finish(
                    task.id,
                    status=STATUS_FAILED,
                    error=exc.hint or exc.error,
                    error_code=str(exc.error),
                )
                log.warning("task %s failed: %s %s", task.id, exc.error, exc.hint)
            except Exception as exc:  # noqa: BLE001
                self.finish(
                    task.id,
                    status=STATUS_FAILED,
                    error=str(exc),
                    error_code=type(exc).__name__,
                )
                log.exception("task %s failed", task.id)
            finally:
                # 任务结束也是一次「活动」：只在提交时记账会让 Core 误判闲置，
                # 一次 90s 的生图就可能把刚出完图的 ComfyUI 睡掉。
                touch_activity()
                self.active_id = None

    # -- 兼容旧接口 -------------------------------------------------------
    async def wait(self, task_id: str, timeout: float) -> TaskRecord:
        task_id = str(task_id)
        if self.get(task_id) is None:
            raise ApiError("unknown_task", f"未知任务 {task_id}", 404)
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            rec = self.get(task_id)
            if rec is not None and rec.status in TERMINAL:
                return rec
            await asyncio.sleep(0.5)
        rec = self.get(task_id)
        if rec is not None and rec.status in TERMINAL:
            return rec
        raise ApiError(
            "task_timeout", f"等待任务 {task_id} 超时（{timeout:.0f}s）", 504
        )

    async def cancel(self, task_id: str) -> dict[str, Any]:
        task_id = str(task_id)
        rec = self.get(task_id)
        if rec is None:
            raise ApiError("unknown_task", f"未知任务 {task_id}", 404)
        if rec.status in TERMINAL:
            return {
                "ok": False,
                "error": "already_finished",
                "status": rec.status,
                "task": rec.public(),
            }
        was_running = rec.status == STATUS_RUNNING
        self.request_cancel(task_id)
        if was_running:
            action = await comfy_cancel_prompt(self.cfg, rec.remote_id)
            self.finish(task_id, status=STATUS_CANCELLED)
            log.info("task %s canceled while running (remote=%s, action=%s)",
                     task_id, rec.remote_id, action)
        else:
            action = "removed_from_queue"
            log.info("task %s canceled while pending", task_id)
        final = self.get(task_id)
        return {
            "ok": True,
            "status": final.status if final else STATUS_CANCELLED,
            "action": action,
            "task": final.public() if final else None,
        }

    def record_voice_task(
        self,
        spec: dict[str, Any],
        result: dict[str, Any] | None,
        error: str | None = None,
        source: str = "console",
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> TaskRecord:
        """语音同步执行后补记（直接写终态，不经过 worker）。"""
        return self.record_terminal(
            kind="voice",
            payload=spec,
            spec=spec,
            status=STATUS_COMPLETED if result else STATUS_FAILED,
            result=result,
            error=error,
            error_code=None if result else "voice_failed",
            source=source,
            user_id=user_id,
            session_id=session_id,
        )

    # -- 执行 -------------------------------------------------------------
    def _add_warning(self, task_id: str, message: str) -> None:
        rec = self.get(task_id)
        warnings = list((rec.params.get("warnings") if rec else None) or [])
        warnings.append(message)
        self.update_params(task_id, {"warnings": warnings})

    async def _run_image(self, task: TaskRecord) -> dict[str, Any]:
        spec = task.spec
        cancel_view = _CancelView(self, task.id)
        _check_cancel(cancel_view)
        nodes = build_nodes(self.cfg, spec)
        self.update_params(
            task.id,
            {
                "loras_applied": spec.get("loras_applied") or [],
                "checkpoint": spec.get("checkpoint"),
                "warnings": list(spec.get("warnings") or []),
            },
        )
        _check_cancel(cancel_view)

        if spec.get("reference_image"):
            reference_name = await self._prepare_reference(task, nodes, spec["reference_image"])
            if reference_name:
                self.update_params(task.id, {"reference_image_name": reference_name})

        ensure = await ensure_comfy(self.cfg)
        if not ensure.get("ok"):
            raise ApiError(
                str(ensure.get("error") or "comfyui_unavailable"),
                str(ensure.get("hint") or ""),
                503,
            )
        _check_cancel(cancel_view)

        started = time.time()
        prompt_id = await comfy_submit(self.cfg, nodes)
        self.set_remote_id(task.id, prompt_id)
        self.update_params(task.id, {"prompt_id": prompt_id})
        _check_cancel(cancel_view)

        output = await comfy_wait_output(
            self.cfg, prompt_id, cancel_view, timeout=self.cfg.task_timeout
        )
        seconds = round(time.time() - started, 1)
        return self._build_image_result(task, spec, prompt_id, output, seconds)

    def _build_image_result(
        self,
        task: TaskRecord,
        spec: dict[str, Any],
        prompt_id: str,
        output: dict[str, Any],
        seconds: float,
    ) -> dict[str, Any]:
        """把 ComfyUI 输出整理成对外的结果（恢复路径也复用）。"""
        filename = output["filename"]
        subfolder = output.get("subfolder", "")
        image_type = output.get("type", "output")
        query = urllib.parse.urlencode(
            {"filename": filename, "subfolder": subfolder, "type": image_type}
        )
        result: dict[str, Any] = {
            "url": f"{self.cfg.public_base}/img?{query}",
            "path": f"/img?{query}",
            "filename": filename,
            "subfolder": subfolder,
            "type": image_type,
            "prompt_id": prompt_id,
            "seconds": seconds,
            "model": spec.get("model"),
            "seed": spec.get("seed"),
            "checkpoint": spec.get("checkpoint"),
            "workflow": spec.get("workflow"),
            "loras": spec.get("loras"),
            "prompt": spec.get("prompt"),
            "negative": spec.get("negative"),
            "width": spec.get("width"),
            "height": spec.get("height"),
            "character": spec.get("character"),
        }
        local_path = (
            os.path.join(self.cfg.comfy_output_dir, subfolder, filename)
            if subfolder
            else os.path.join(self.cfg.comfy_output_dir, filename)
        )
        if os.path.isfile(local_path):
            result["local_path"] = local_path
            result["bytes"] = os.path.getsize(local_path)
        return result

    async def _prepare_reference(
        self, task: TaskRecord, nodes: dict[str, dict], reference: str
    ) -> str | None:
        load_ids = _find_load_image_nodes(nodes)
        if not load_ids:
            self._add_warning(
                task.id, f"工作流没有 LoadImage 节点，reference_image={reference} 被忽略"
            )
            return None
        if len(load_ids) > 1:
            self._add_warning(
                task.id, f"工作流有 {len(load_ids)} 个 LoadImage，仅填充第一个 {load_ids[0]}"
            )
        data, filename = await resolve_reference_bytes(self.cfg, reference)
        uploaded_name = await comfy_upload_image(self.cfg, data, filename)
        nodes[load_ids[0]].setdefault("inputs", {})["image"] = uploaded_name
        return uploaded_name

    async def _run_voice(self, task: TaskRecord) -> dict[str, Any]:
        spec = task.spec
        return await synth_tts(
            self.cfg,
            text=spec["prompt"],
            voice=spec.get("voice") or "",
            engine=spec.get("engine") or "kokoro",
            speed=spec.get("speed") or 1.0,
        )


# 兼容旧类型标注
Task = TaskRecord

def _check_cancel(task) -> None:
    """执行期间检查取消标记（兼容 TaskRecord / _CancelView）。"""
    if task is not None and (getattr(task, "cancel_requested", False) or
                             getattr(task, "status", "") in ("canceled", STATUS_CANCELLED)):
        raise _TaskCanceled()


store = TaskStore(settings)


# ---------------------------------------------------------------------------
# 参考图 / 上传索引
# ---------------------------------------------------------------------------


async def resolve_reference_bytes(cfg: Settings, reference: str) -> tuple[bytes, str]:
    """把 upload_id / 文件名 / 绝对路径 / http URL 解析成 (bytes, filename)。"""
    reference = str(reference or "").strip()
    if not reference:
        raise ApiError("reference_image_missing", "reference_image 为空")
    if reference.startswith(("http://", "https://")):
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(reference)
        if resp.status_code != 200:
            raise ApiError(
                "reference_image_fetch_failed",
                f"下载参考图失败: HTTP {resp.status_code}",
                400,
            )
        filename = os.path.basename(urllib.parse.urlsplit(reference).path) or "reference.png"
        return resp.content, filename
    # upload_id（up_xxx）或直接文件名
    candidates: list[str] = []
    if os.path.isabs(reference) and os.path.isfile(reference):
        candidates.append(reference)
    candidates.append(os.path.join(cfg.uploads_dir, reference))
    if os.path.isdir(cfg.uploads_dir):
        for filename in os.listdir(cfg.uploads_dir):
            if filename.startswith(reference + ".") and not filename.endswith(".meta.json"):
                candidates.append(os.path.join(cfg.uploads_dir, filename))
    for candidate in candidates:
        if os.path.isfile(candidate):
            with open(candidate, "rb") as f:
                data = f.read()
            return data, _upload_original_name(cfg, candidate)
    raise ApiError(
        "reference_image_not_found",
        f"找不到参考图 {reference!r}，请先 POST /api/upload",
        404,
    )


def _sidecar_path(cfg: Settings, file_path: str) -> str:
    return os.path.splitext(file_path)[0] + ".meta.json"


def _upload_original_name(cfg: Settings, file_path: str) -> str:
    sidecar = _sidecar_path(cfg, file_path)
    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            meta = json.load(f)
        name = str(meta.get("original_name") or "").strip()
        if name:
            return name
    except Exception:
        pass
    return os.path.basename(file_path)


def find_upload_file(cfg: Settings, upload_id: str) -> str | None:
    upload_id = str(upload_id or "")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", upload_id):
        return None
    if not os.path.isdir(cfg.uploads_dir):
        return None
    for filename in os.listdir(cfg.uploads_dir):
        if filename.startswith(upload_id + ".") and not filename.endswith(".meta.json"):
            return os.path.join(cfg.uploads_dir, filename)
    return None


def _safe_suffix(filename: str, content_type: str) -> str:
    suffix = os.path.splitext(filename or "")[1].lower()
    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        return suffix
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip())
    if guessed in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        return guessed
    return ".png"


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------


async def list_voices(cfg: Settings) -> dict[str, Any]:
    voices: list[dict[str, Any]] = []
    kokoro_ok = False
    error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{cfg.kokoro_url}/v1/models")
        if resp.status_code == 200:
            kokoro_ok = True
            data = resp.json()
            for item in data.get("data") or []:
                voice_id = str(item.get("id") or "")
                if voice_id:
                    voices.append(
                        {
                            "id": voice_id,
                            "name": voice_id,
                            "engine": "kokoro",
                            "owned_by": item.get("owned_by"),
                        }
                    )
        else:
            error = f"kokoro /v1/models -> HTTP {resp.status_code}"
    except Exception as exc:
        error = f"kokoro_unreachable: {exc}"
    cloned: list[dict[str, Any]] = []
    f5_ok = await _url_ok(f"{cfg.f5_url}/v1/models", 1.5)
    if f5_ok:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{cfg.f5_url}/v1/models")
            if resp.status_code == 200:
                for item in (resp.json().get("data") or []):
                    voice_id = str(item.get("id") or "")
                    if voice_id:
                        cloned.append({"id": voice_id, "name": voice_id, "engine": "mobius"})
        except Exception:
            pass
    return {
        "ok": True,
        "voices": voices,
        "cloned": cloned,
        "default": "zf_xiaobei",
        "korean": False,
        "engines": {"kokoro": kokoro_ok, "f5": f5_ok},
        "error": error,
    }


async def synth_tts(
    cfg: Settings, text: str, voice: str = "", engine: str = "kokoro", speed: float = 1.0
) -> dict[str, Any]:
    text = str(text or "").strip()
    if not text:
        raise ApiError("missing_text", "text 不能为空")
    if len(text) > 4000:
        raise ApiError("text_too_long", "text 过长（>4000 字符），请拆分后合成")
    engine = str(engine or "kokoro").strip().lower()
    if engine not in ("kokoro", "mobius", "f5"):
        raise ApiError("unknown_engine", f"未知 TTS 引擎 {engine!r}；可用: kokoro / mobius")
    base_url = cfg.kokoro_url if engine == "kokoro" else cfg.f5_url
    if not voice:
        voice = "mobius" if engine in ("mobius", "f5") else "zf_xiaobei"
    speed = _as_float(speed) or 1.0
    speed = max(0.5, min(2.0, speed))

    payload = {
        "model": "tts",
        "input": text,
        "voice": voice,
        "response_format": "wav",
        "speed": speed,
    }
    try:
        async with httpx.AsyncClient(timeout=cfg.tts_timeout) as client:
            resp = await client.post(f"{base_url}/v1/audio/speech", json=payload)
    except Exception as exc:
        raise ApiError(
            "tts_unavailable",
            f"TTS 引擎 {engine} 不可达（{base_url}）: {exc}",
            503,
        )
    if resp.status_code != 200:
        raise ApiError(
            "tts_failed",
            f"TTS 引擎返回 HTTP {resp.status_code}: {resp.text[:300]}",
            502,
        )
    audio = resp.content
    if not audio:
        raise ApiError("tts_empty_audio", "TTS 返回空音频", 502)
    if len(audio) > cfg.max_voice_bytes:
        raise ApiError(
            "tts_audio_too_large",
            f"音频 {len(audio)} 字节超过上限 {cfg.max_voice_bytes}",
            502,
        )
    os.makedirs(cfg.tts_dir, exist_ok=True)
    filename = f"tts_{int(time.time())}_{random.randint(100, 999)}.wav"
    filepath = os.path.join(cfg.tts_dir, filename)
    with open(filepath, "wb") as f:
        f.write(audio)
    seconds = _wav_seconds(audio)
    query = urllib.parse.urlencode({"file": filename})
    return {
        "ok": True,
        "engine": engine,
        "voice": voice,
        "speed": speed,
        "seconds": seconds,
        "bytes": len(audio),
        "filename": filename,
        "audio_url": f"{cfg.public_base}/audio?{query}",
        "path": f"/audio?{query}",
        "local_path": filepath,
    }


async def do_tts(
    cfg: Settings,
    body: dict[str, Any],
    source: str = "console",
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    character = None
    character_key = str(body.get("character") or "").strip()
    if character_key:
        character = find_character(cfg, character_key)
        if character is None:
            raise ApiError("unknown_character", f"未找到角色 {character_key!r}")
        policy = character.get("media_policy") or {}
        if policy.get("allow_voice") is False:
            raise ApiError(
                "media_disabled", f"角色 {character.get('id')} 禁止语音", 403
            )
    voice_cfg = (character or {}).get("voice") or {}
    text = str(body.get("text") or "").strip()
    engine = str(body.get("engine") or voice_cfg.get("engine") or "kokoro")
    voice = str(body.get("voice") or voice_cfg.get("voice") or "")
    speed = _as_float(body.get("speed"))
    if speed is None:
        speed = _as_float(voice_cfg.get("speed")) or 1.0

    spec = {
        "kind": "voice",
        "character": (character or {}).get("id"),
        "prompt": text,
        "engine": engine,
        "voice": voice,
        "speed": speed,
    }
    try:
        result = await synth_tts(cfg, text=text, voice=voice, engine=engine, speed=speed)
    except ApiError as exc:
        store.record_voice_task(
            spec, None, error=exc.error, source=source, user_id=user_id, session_id=session_id
        )
        raise
    task = store.record_voice_task(
        spec, result, source=source, user_id=user_id, session_id=session_id
    )
    result = dict(result)
    result["task_id"] = task.id
    return result


# ---------------------------------------------------------------------------
# 状态汇总
# ---------------------------------------------------------------------------


async def collect_status(cfg: Settings) -> dict[str, Any]:
    comfy_ok, comfy_info = await comfy_health(cfg, timeout=2.5)
    comfy: dict[str, Any] = {
        "state": "down",
        "url": cfg.comfy_url,
        "port": _port_of(cfg.comfy_url),
        "last_check": _now(),
    }
    if comfy_ok:
        queue = await comfy_get_queue(cfg)
        running = len(queue.get("queue_running") or [])
        pending = len(queue.get("queue_pending") or [])
        comfy.update(
            {
                "state": "busy" if running else "ready",
                "queue_running": running,
                "queue_pending": pending,
            }
        )
        devices = comfy_info.get("devices") or []
        if devices:
            comfy["device"] = devices[0]
        comfy["version"] = (comfy_info.get("system") or {}).get("comfyui_version")
    else:
        comfy["error"] = (comfy_info or {}).get("error") or "unreachable"

    kokoro_ok = await _url_ok(f"{cfg.kokoro_url}/v1/models", 2.5)
    f5_ok = await _url_ok(f"{cfg.f5_url}/v1/models", 1.0)
    core_ok = await _url_ok(f"{cfg.core_url}/api/status", 1.5) or await _url_ok(
        f"{cfg.core_url}/v1/models", 1.5
    )

    tasks = store.list(limit=200)
    return {
        "gateway": {
            "state": "ready",
            "url": cfg.public_base,
            "port": cfg.port,
            "uptime_sec": round(time.time() - START_TS, 1),
            "version": VERSION,
        },
        "comfyui": comfy,
        "kokoro": {
            "state": "ready" if kokoro_ok else "down",
            "url": cfg.kokoro_url,
            "port": _port_of(cfg.kokoro_url),
        },
        "f5": {
            "state": "ready" if f5_ok else "down",
            "url": cfg.f5_url,
            "port": _port_of(cfg.f5_url),
        },
        "core": {"state": "ready" if core_ok else "down", "url": cfg.core_url},
        "activity": {
            "last_activity_at": round(LAST_ACTIVITY_TS, 3),
            "seconds_since": round(time.time() - LAST_ACTIVITY_TS, 1),
            "active_tasks": sum(
                1 for t in tasks if t.status in (STATUS_PENDING, STATUS_RUNNING)
            ),
        },
        "queue": {
            "concurrency": cfg.concurrency,
            "active": 1 if store.active_id else 0,
            "active_task": store.active_id,
            "pending": store.pending_count(),  # noqa: SLF001
            "total": len(tasks),
            "completed": sum(1 for t in tasks if t.status == STATUS_COMPLETED),
            "failed": sum(1 for t in tasks if t.status == "failed"),
        },
        "device": comfy.get("device"),
    }


# ---------------------------------------------------------------------------
# 生成流程（REST 与 MCP 共用）
# ---------------------------------------------------------------------------


async def submit_generate(cfg: Settings, body: dict[str, Any], async_mode: bool) -> dict[str, Any]:
    spec = resolve_spec(cfg, body, kind="image")
    await require_comfy(cfg)  # 契约 §5.1：不可达先 ensure，失败立即返回错误
    idem = str(body.get("idempotency_key") or "").strip() or None
    try:
        task, idempotent_hit = store.submit(
            kind="image",
            payload={
                key: value
                for key, value in body.items()
                if key not in ("source", "user_id", "session_id")
            },
            spec=spec,
            source=str(body.get("source") or "console"),
            user_id=body.get("user_id"),
            session_id=body.get("session_id"),
            idempotency_key=idem,
        )
    except TaskConflict as exc:
        raise ApiError(
            "idempotency_conflict",
            f"幂等键已被任务 {exc.task_id} 使用，但请求内容不同",
            409,
            existing_task_id=exc.task_id,
        ) from exc
    if async_mode:
        return {
            "ok": True,
            "task_id": task.id,
            "status": task.status,
            "idempotent_hit": idempotent_hit,
            "workflow": spec["workflow"],
            "checkpoint": spec["checkpoint"],
            "loras": spec["loras"],
            "warnings": spec["warnings"],
        }
    finished = await store.wait(task.id, cfg.view_timeout + 120)
    if finished.status == STATUS_COMPLETED:
        result = dict(finished.result or {})
        result.update(
            {
                "ok": True,
                "task_id": finished.id,
                "status": STATUS_COMPLETED,
                "workflow": spec["workflow"],
                "checkpoint": spec["checkpoint"],
                "loras": spec["loras"],
                "character": spec["character"],
                "warnings": spec["warnings"],
            }
        )
        return result
    if finished.status == STATUS_CANCELLED:
        raise ApiError("task_canceled", "任务已被取消", 409)
    raise ApiError(
        finished.error or "task_failed",
        str(finished.params.get("hint") or ""),
        500 if finished.status == STATUS_FAILED else 503,
    )


# ---------------------------------------------------------------------------
# P0.4-B 轮询限流（仅作用于 comfyui_task_status 的读取路径）
# 说明：状态放在内存里（Gateway 重启后重置为「首次查询」），
#       不改任务状态机与持久化结构。
# ---------------------------------------------------------------------------

_poll_state: dict[str, dict[str, float]] = {}


def _poll_gate(task_id: str, status: str) -> dict[str, Any] | None:
    """返回 None 表示放行；返回 dict 表示要告知调用方 poll_too_fast。"""
    now = time.time()
    state = _poll_state.setdefault(task_id, {"last_valid": 0.0, "attempts": 0.0, "valid": 0.0})
    state["attempts"] += 1

    # 终态任务：允许立即读取，不受间隔限制
    if status in TERMINAL:
        state["valid"] += 1
        state["last_valid"] = now
        return None

    interval_s = max(settings.poll_min_interval_ms, 0.0) / 1000.0
    if state["last_valid"] and interval_s > 0:
        since = now - state["last_valid"]
        if since < interval_s:
            retry_ms = max(int(round((interval_s - since) * 1000)), 1)
            # 关键：不更新 last_valid，避免被拒绝的查询把等待窗口不断推后
            log.warning(
                "poll_too_fast task=%s since=%.2fs retry=%dms (valid=%d attempts=%d)",
                task_id, since, retry_ms, int(state["valid"]), int(state["attempts"]),
            )
            return {
                "ok": False,
                "error": "poll_too_fast",
                "task_id": task_id,
                "retry_after_ms": retry_ms,
                "interval_ms": int(interval_s * 1000),
                "valid_queries": int(state["valid"]),
                "attempted_queries": int(state["attempts"]),
            }

    state["valid"] += 1
    state["last_valid"] = now
    return None


def _poll_stats(task_id: str) -> dict[str, int]:
    state = _poll_state.get(task_id) or {"last_valid": 0.0, "attempts": 0.0, "valid": 0.0}
    return {
        "valid_queries": int(state["valid"]),
        "attempted_queries": int(state["attempts"]),
    }


# ---------------------------------------------------------------------------
# MCP 服务器 + REST 路由
# ---------------------------------------------------------------------------

server = MCPServer(
    "mobius-media-gateway",
    title="Möbius Media Gateway（ComfyUI 生图 + TTS + 异步任务）",
    instructions=(
        "本服务器同时提供 ComfyUI 生图与 TTS 语音工具，并保留旧版工具名以兼容 LangBot。"
        "生图工具返回图片 URL，agent 应在回复中用 markdown 图片引用该 URL；"
        "tts_speak 返回 audio_url，必须把该 URL 以纯文本原样放在回复最后一行，LangBot 会自动转成语音消息。"
        "异步任务请用 comfyui_task_submit 提交后用 comfyui_task_status 查询。"
        "【生图必须走异步流程，P0.4】生图 = comfyui_task_submit（立即拿 task_id）→ 每 5~10 秒"
        "comfyui_task_status 轮询（最多 60 次）→ 按状态处理：pending/running 继续等，"
        "completed 交付产物，failed 报错并附 error_code，cancelled 如实说已取消，"
        "submission_unknown 说明结果不确定且禁止自动重提。"
        "| 不要用 comfyui_generate（同步阻塞，已标弃用）；异步失败时也不要退回它。"
        "| 每次用户主动生图都要生成新的 idempotency_key；同一次逻辑请求重试必须复用同一个 key。"
        "| 轮询 60 次仍未结束：停止轮询、告知用户任务仍在后台执行并给出 task_id；"
        "轮询耗尽不等于失败，也不要取消后台任务。"
        "【生图优先用角色档案】character 参数可传角色 id、中文名或别名"
        "（如 幽兰黛尔 / 布洛妮娅 / 符华 / 琪亚娜 / 爱莉希雅 / 德丽莎 …），"
        "系统会自动补齐 checkpoint、LoRA、权重、尺寸与画风提示词——比手工拼 LoRA 更稳。"
        "完整角色列表见 comfyui_list_characters；LoRA 见 comfyui_list_loras。"
        "注意：character 为空时才需要自己传 loras 数组。"
        "【可复现】generate 的返回里带 seed / checkpoint / loras / prompt，"
        "想复刻同一张图就带上同样的 seed 重发。"
        "【按画风选模型】生图统一入口是 comfyui_generate(model=...)，不要自己猜参数。"
        "路由规则：二次元/动漫人物/需要角色一致性或 LoRA → model=anime_illustrious；"
        "写实、摄影感、电影感、强构图 → model=flux3_klein；"
        "写实但要求更强的中文语义理解、或想更快更省 → model=qwen_image_2_1。"
        "拿不准或用户描述含糊时，先调 comfyui_list_models（返回每个模型的 style/尺寸/是否支持角色），"
        "选好再生成；不要默认就用动漫模型。"
        "限制：只有 anime_illustrious 支持 character 与 LoRA；qwen_image_2_1 与 flux3_klein 都不支持，"
        "对它们传 character/loras 会直接报错。"
        "不传 model 时才会退回旧参数 workflow（portrait/landscape/upscale/removebg）。"
    ),
)


def api_route(path: str, methods: list[str], name: str | None = None):
    """把 Starlette handler 注册到 MCP 的 custom_route（本 venv 无 fastapi）。"""

    def decorator(func):
        @functools.wraps(func)
        async def endpoint(request: Request) -> Response:
            try:
                return await func(request)
            except ApiError as exc:
                return JSONResponse(_error_payload(exc), status_code=exc.status)
            except Exception as exc:  # noqa: BLE001
                log.exception("REST %s %s 崩溃", request.method, request.url.path)
                return JSONResponse(
                    {"ok": False, "error": "internal_error", "detail": str(exc)},
                    status_code=500,
                )

        server.custom_route(path, methods=methods, name=name or func.__name__)(endpoint)
        return endpoint

    return decorator


async def _read_json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        raise ApiError("invalid_json", "请求体必须是合法 JSON")
    if not isinstance(body, dict):
        raise ApiError("invalid_json", "请求体必须是 JSON 对象")
    return body


# ---- 健康 / 状态 / 目录 ----------------------------------------------------


@api_route("/health", ["GET"])
async def route_health(request: Request) -> Response:
    return JSONResponse(
        {
            "ok": True,
            "service": "mobius-media-gateway",
            "version": VERSION,
            "port": settings.port,
            "uptime_sec": round(time.time() - START_TS, 1),
        }
    )


@api_route("/api/status", ["GET"])
async def route_status(request: Request) -> Response:
    status = await collect_status(settings)
    return JSONResponse({"ok": True, **status})


@api_route("/api/pulse", ["GET"])
async def route_pulse(request: Request) -> Response:
    """轻量心跳：不探测任何后端，纯内存读取。

    给 Core 的「闲置休眠」和 watchdog 的「MCP 会话自愈」用。
    /api/status 会依次探测 ComfyUI/Kokoro/F5/Core，最坏要 9 秒，
    调用方 6 秒超时就会失败——这正是「自愈检查每分钟都超时」的原因。
    """
    return JSONResponse({
        "ok": True,
        "version": VERSION,
        "port": settings.port,
        "uptime_sec": round(time.time() - START_TS, 1),
        "last_activity_at": round(LAST_ACTIVITY_TS, 3),
        "seconds_since": round(time.time() - LAST_ACTIVITY_TS, 1),
        "active_tasks": sum(
            1 for t in store.list(limit=200)
            if t.status in (STATUS_PENDING, STATUS_RUNNING)
        ),
        "queue_pending": store.pending_count(),  # noqa: SLF001
    })


@api_route("/api/models", ["GET"])
async def route_models(request: Request) -> Response:
    checkpoints, source = await comfy_list_models(settings)
    profiles = public_models(settings)
    return JSONResponse({
        "ok": True,
        "models": profiles,          # 通用入口：模型档案
        "count": len(profiles),
        "checkpoints": checkpoints,  # 底层 SDXL checkpoint
        "source": source,
    })


@api_route("/api/loras", ["GET"])
async def route_loras(request: Request) -> Response:
    query = request.query_params.get("q", "")
    data = await comfy_loras(settings, query=query)
    return JSONResponse({"ok": True, **data})


@api_route("/api/characters", ["GET"])
async def route_characters(request: Request) -> Response:
    characters = load_characters(settings)
    return JSONResponse(
        {"ok": True, "characters": characters, "count": len(characters), "dir": settings.characters_dir}
    )


@api_route("/api/characters/{character_id}", ["GET"])
async def route_character(request: Request) -> Response:
    character_id = request.path_params.get("character_id", "")
    character = find_character(settings, character_id)
    if character is None:
        raise ApiError("unknown_character", f"未找到角色 {character_id!r}", 404)
    return JSONResponse({"ok": True, "character": character})


@api_route("/api/voices", ["GET"])
async def route_voices(request: Request) -> Response:
    return JSONResponse(await list_voices(settings))


# ---- 生成 / TTS / 上传 -----------------------------------------------------


@api_route("/api/generate", ["POST"])
async def route_generate(request: Request) -> Response:
    body = await _read_json(request)
    async_mode = body.get("async", True)
    if isinstance(async_mode, str):
        async_mode = async_mode.strip().lower() not in ("false", "0", "no", "off")
    result = await submit_generate(settings, body, bool(async_mode))
    return JSONResponse(result)


@api_route("/api/tts", ["POST"])
async def route_tts(request: Request) -> Response:
    body = await _read_json(request)
    result = await do_tts(
        settings,
        body,
        source=str(body.get("source") or "console"),
        user_id=body.get("user_id"),
        session_id=body.get("session_id"),
    )
    return JSONResponse(result)


@api_route("/api/upload", ["POST"])
async def route_upload(request: Request) -> Response:
    try:
        form = await request.form()
    except Exception as exc:
        raise ApiError("invalid_multipart", f"解析 multipart 失败: {exc}")
    upload = form.get("file")
    if upload is None:
        raise ApiError("missing_file", "multipart 字段 file 缺失")
    data = await upload.read()
    if not data:
        raise ApiError("empty_file", "上传文件为空")
    if len(data) > 25 * 1024 * 1024:
        raise ApiError("file_too_large", "上传文件超过 25MB")
    original_name = os.path.basename(str(getattr(upload, "filename", "") or "upload.png"))
    content_type = str(getattr(upload, "content_type", "") or "")
    suffix = _safe_suffix(original_name, content_type)
    upload_id = "up_" + uuid.uuid4().hex[:12]
    file_path = os.path.join(settings.uploads_dir, upload_id + suffix)
    with open(file_path, "wb") as f:
        f.write(data)
    with open(_sidecar_path(settings, file_path), "w", encoding="utf-8") as f:
        json.dump(
            {
                "upload_id": upload_id,
                "original_name": original_name,
                "content_type": content_type,
                "size": len(data),
                "created_at": _now(),
            },
            f,
            ensure_ascii=False,
        )
    return JSONResponse(
        {
            "ok": True,
            "upload_id": upload_id,
            "filename": original_name,
            "saved_as": os.path.basename(file_path),
            "size": len(data),
            "url": f"{settings.public_base}/upload/{upload_id}",
            "path": f"/upload/{upload_id}",
        }
    )


# ---- 任务中心 --------------------------------------------------------------


@api_route("/api/tasks", ["GET"])
async def route_tasks(request: Request) -> Response:
    params = request.query_params
    limit = _as_int(params.get("limit")) or 50
    status = params.get("status") or None
    source = params.get("source") or None
    tasks = store.list(limit=limit, status=status, source=source)
    return JSONResponse(
        {"ok": True, "tasks": [task.public() for task in tasks], "count": len(tasks)}
    )


@api_route("/api/tasks/{task_id}", ["GET"])
async def route_task(request: Request) -> Response:
    task_id = request.path_params.get("task_id", "")
    task = store.get(task_id)
    if task is None:
        raise ApiError("unknown_task", f"未知任务 {task_id}", 404)
    return JSONResponse({"ok": True, "task": task.public()})


@api_route("/api/tasks/{task_id}/cancel", ["POST"])
async def route_task_cancel(request: Request) -> Response:
    task_id = request.path_params.get("task_id", "")
    result = await store.cancel(task_id)
    status_code = 200 if result.get("ok") else 409
    return JSONResponse(result, status_code=status_code)


# ---- 唤醒 ------------------------------------------------------------------


@api_route("/api/wake", ["POST"])
async def route_wake(request: Request) -> Response:
    result = await ensure_comfy(settings)
    if not result.get("ok"):
        return JSONResponse(
            {
                "ok": False,
                "error": result.get("error", "comfyui_unavailable"),
                "hint": result.get("hint", ""),
                "core": result.get("core"),
            },
            status_code=503,
        )
    return JSONResponse(
        {
            "ok": True,
            "state": result.get("state", "ready"),
            "action": result.get("action"),
            "seconds": result.get("seconds", 0),
            "comfyui": settings.comfy_url,
        }
    )


# ---- 代理路由 --------------------------------------------------------------


@api_route("/img", ["GET"])
async def route_img(request: Request) -> Response:
    filename = request.query_params.get("filename", "")
    subfolder = request.query_params.get("subfolder", "")
    image_type = request.query_params.get("type", "output")
    if not filename:
        raise ApiError("missing_filename", "/img 需要 ?filename=xxx.png")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(
                f"{settings.comfy_url}/view",
                params={"filename": filename, "subfolder": subfolder, "type": image_type},
            )
    except Exception as exc:
        raise ApiError("comfyui_unavailable", f"ComfyUI /view 不可达: {exc}", 503)
    if resp.status_code != 200:
        raise ApiError(
            "image_not_found",
            f"ComfyUI /view -> HTTP {resp.status_code}（filename={filename}）",
            404,
        )
    media_type = resp.headers.get("content-type") or "image/png"
    return Response(content=resp.content, media_type=media_type)


@api_route("/audio", ["GET"])
async def route_audio(request: Request) -> Response:
    filename = request.query_params.get("file", "")
    if not filename or os.path.basename(filename) != filename or filename.startswith("."):
        raise ApiError("invalid_file", "/audio 需要合法的 ?file=tts_xxx.wav")
    file_path = os.path.join(settings.tts_dir, filename)
    if not os.path.isfile(file_path):
        raise ApiError("audio_not_found", f"音频不存在: {filename}", 404)
    media_type = "audio/wav" if filename.lower().endswith(".wav") else "audio/mpeg"
    with open(file_path, "rb") as f:
        return Response(content=f.read(), media_type=media_type)


@api_route("/upload/{upload_id}", ["GET"])
async def route_upload_file(request: Request) -> Response:
    upload_id = request.path_params.get("upload_id", "")
    file_path = find_upload_file(settings, upload_id)
    if file_path is None:
        raise ApiError("upload_not_found", f"上传文件不存在: {upload_id}", 404)
    media_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
    with open(file_path, "rb") as f:
        return Response(content=f.read(), media_type=media_type)


# ---------------------------------------------------------------------------
# MCP 工具（保留旧工具名 + §5.2 新增）
# ---------------------------------------------------------------------------


def _tool_error(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        return _json(_error_payload(exc))
    return _json({"ok": False, "error": str(exc)})


def _tool_body(
    prompt: str,
    negative: str,
    workflow: str,
    width: int | None,
    height: int | None,
    seed: int | None,
    character: str,
    loras: list[dict[str, Any]] | None,
    reference_image_url: str,
    checkpoint: str = "",
    source: str = "mcp",
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "prompt": prompt,
        "negative": negative,
        "workflow": workflow or "portrait",
        "character": character or "",
        "reference_image": reference_image_url or "",
        "source": source,
        "async": False,
    }
    if width is not None:
        body["width"] = width
    if height is not None:
        body["height"] = height
    if seed is not None:
        body["seed"] = seed
    if checkpoint:
        body["checkpoint"] = checkpoint
    parsed_loras = parse_loras_param(loras)
    if parsed_loras is not None:
        body["loras"] = parsed_loras
    body.update(extra)
    return body


@server.tool(
    name="comfyui_generate",
    description=(
        "【已弃用 · 不要再用于常规生图】同步阻塞接口，会占用执行线程直到出图完成。"
        "常规生图请改用 comfyui_task_submit + comfyui_task_status 的异步流程。"
        "本工具仅在历史兼容场景保留。"
        "调用本机 ComfyUI 生成图片，等待完成并返回图片 URL。"
        "【必须按画风选模型】用 model 指定模型档案："
        "anime_illustrious=二次元/动漫角色（唯一支持 character 与 LoRA）；"
        "flux3_klein=写实摄影/电影感（16 步，质量最高）；"
        "qwen_image_2_1=写实通用（中文语义强、速度较快）。"
        "不确定时先调用 comfyui_list_models 查看每个模型的 style，再决定；不要默认用动漫模型。"
        "model 会自动带出工作流、尺寸与采样参数，比手写 workflow 更稳。"
        "qwen_image_2_1 / flux3_klein 不支持 character、loras、checkpoint，传了会报错。"
    ),
)
async def comfyui_generate(
    prompt: Annotated[str, Field(description="画面描述。写实类模型请写完整场景（主体+姿态+环境+光线+镜头+质感），不要只写关键词")],
    negative: str = "",
    workflow: str = "",
    model: Annotated[str, Field(description="模型档案，按画风选：anime_illustrious=二次元/动漫角色（唯一支持 character 与 LoRA）｜flux3_klein=写实摄影/电影感｜qwen_image_2_1=写实通用")] = "",
    width: int | None = None,
    height: int | None = None,
    seed: int | None = None,
    character: Annotated[str, Field(description="角色 id/中文名/别名，仅 anime_illustrious 支持；qwen/flux3 传了会报错")] = "",
    loras: Annotated[list[dict[str, Any]] | None, Field(description="LoRA 数组：[{\"name\":\"xxx.safetensors\",\"weight\":0.75}]；仅 anime_illustrious 支持")] = None,
    reference_image_url: str = "",
) -> str:
    """生成图片（同步等待结果）。

    Args:
        prompt: 正向提示词（中文或英文均可，描述画面内容）
        negative: 负向提示词，留空使用角色档案/工作流默认
        workflow: 工作流模板（不传 model 时才需要），portrait=竖版漫画 /
                  landscape=横版漫画 / upscale=放大 / removebg=去背景
        model: **推荐的通用入口**。模型档案 id 或别名，如 anime_illustrious、
               qwen_image_2_1。会自动决定工作流、尺寸、采样参数；未知 id 会返回可用列表
        width: 覆盖画布宽度（可选）
        height: 覆盖画布高度（可选）
        seed: 随机种子（可选，默认随机）。**返回里会带实际使用的 seed**，带上它重发可复刻同一张图
        character: 角色 id / 中文名 / 别名，如 幽兰黛尔、布洛妮娅、bronya、durandal
                   （仅 anime_illustrious 这类支持角色的模型有效）
        loras: LoRA 数组，如 [{"name":"xxx.safetensors","weight":0.75}]；
               也接受 JSON 字符串或 "name:0.75,name2:0.6"（character 留空时才需要自己传）
        reference_image_url: 上传后的 upload_id 或 URL（upscale/removebg 用）
    """
    try:
        result = await submit_generate(
            settings,
            _tool_body(
                prompt, negative, workflow, width, height, seed,
                character, loras, reference_image_url,
                model=model,
            ),
            async_mode=False,
        )
        return _json(
            {
                "ok": True,
                "prompt_id": result.get("prompt_id"),
                "task_id": result.get("task_id"),
                "filename": result.get("filename"),
                "image_url": result.get("url"),
                "path": result.get("path"),
                "local_path": result.get("local_path"),
                "bytes": result.get("bytes"),
                "workflow": result.get("workflow"),
                "model": result.get("model"),
                "checkpoint": result.get("checkpoint"),
                "character": result.get("character"),
                "loras": result.get("loras"),
                "seed": result.get("seed"),
                "prompt": result.get("prompt"),
                "negative": result.get("negative"),
                "seconds": result.get("seconds"),
                "warnings": result.get("warnings") or [],
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(
    name="comfyui_task_submit",
    description=(
        "【生图首选】异步提交 ComfyUI 生图任务，1 秒内返回 task_id，不会阻塞。"
        "提交后用 comfyui_task_status 轮询（间隔 5~10 秒，最多 60 次）。"
        "每次用户主动发起的请求都要自己生成新的 idempotency_key；"
        "同一次逻辑请求的失败重试必须复用同一个 key，避免重复出图。"
    ),
)
async def comfyui_task_submit(
    prompt: str,
    negative: str = "",
    workflow: str = "portrait",
    width: int | None = None,
    height: int | None = None,
    seed: int | None = None,
    character: str = "",
    loras: Annotated[list[dict[str, Any]] | None, Field(description="LoRA 数组：[{\"name\":\"xxx.safetensors\",\"weight\":0.75}]；仅 anime_illustrious 支持")] = None,
    reference_image_url: str = "",
    idempotency_key: str = "",
) -> str:
    """异步提交生图任务。参数同 comfyui_generate。

    idempotency_key：同一个逻辑请求的重试请带上同一个 key，避免重复出图。
    """
    try:
        body = _tool_body(
            prompt, negative, workflow, width, height, seed,
            character, loras, reference_image_url,
        )
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        result = await submit_generate(settings, body, async_mode=True)
        return _json(result)
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(
    name="comfyui_task_status",
    description=(
        "查询异步任务状态（task_id 形如 g_xxx / v_xxx）。"
        "status 取值：pending / running / completed / failed / cancelled / submission_unknown。"
        "pending、running → 继续等待；completed → 读取 result 交付产物；"
        "failed → 报道 error_code 与 error_message；cancelled → 如实说已取消；"
        "submission_unknown → 结果不确定，禁止自动重新提交，请用户确认。"
    ),
)
async def comfyui_task_status(task_id: str) -> str:
    try:
        task = store.get(task_id)
        if task is None:
            return _json({"ok": False, "error": "unknown_task", "task_id": task_id})
        throttled = _poll_gate(task_id, task.status)
        if throttled is not None:
            return _json(throttled)
        return _json({"ok": True, "task": task.public(), "poll": _poll_stats(task_id)})
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(name="comfyui_task_cancel", description="取消网关异步任务（排队中直接删，已提交 ComfyUI 的会被中断）。")
async def comfyui_task_cancel(task_id: str) -> str:
    try:
        return _json(await store.cancel(task_id))
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(name="comfyui_status", description="查询 ComfyUI 任务状态（兼容旧版：输入 prompt_id）。")
async def comfyui_status(prompt_id: str) -> str:
    """查询 ComfyUI prompt 状态；也接受网关 task_id。"""
    try:
        task = store.get(prompt_id)
        if task is not None:
            return _json({"ok": True, "task_id": task.id, "status": task.status, "task": task.public()})
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{settings.comfy_url}/history/{prompt_id}")
        if resp.status_code == 200:
            data = resp.json()
            if prompt_id in data:
                entry = data[prompt_id]
                return _json(
                    {
                        "ok": True,
                        "prompt_id": prompt_id,
                        "status": entry.get("status", {}),
                        "outputs": entry.get("outputs", {}),
                    }
                )
            return _json({"ok": True, "prompt_id": prompt_id, "status": "unknown"})
        return _json({"ok": False, "error": f"HTTP {resp.status_code}"})
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(
    name="comfyui_list_models",
    description=(
        "列出可用的【模型档案】（profile）与底层 checkpoint。生成图片前先调这个，"
        "然后把返回的 models[].id 传给 comfyui_generate 的 model 参数即可。"
        "每个档案带 style/尺寸/是否支持角色与 LoRA，选择成本低。"
    ),
)
async def comfyui_list_models() -> str:
    try:
        checkpoints, source = await comfy_list_models(settings)
        profiles = public_models(settings)
        return _json({
            "ok": True,
            "models": profiles,
            "count": len(profiles),
            "checkpoints": checkpoints,
            "checkpoint_source": source,
            "registry": models_file(settings),
        })
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(name="comfyui_list_loras", description="列出 ComfyUI 可用的 LoRA（可按 query 过滤）。")
async def comfyui_list_loras(query: str = "") -> str:
    try:
        data = await comfy_loras(settings, query=query)
        return _json({"ok": True, **data})
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(name="comfyui_list_characters", description="列出可用的角色档案（id/name/checkpoint/loras/workflow）。")
async def comfyui_list_characters() -> str:
    try:
        characters = load_characters(settings)
        return _json({"ok": True, "characters": characters, "count": len(characters)})
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(name="comfyui_queue_status", description="查询 ComfyUI 队列与 GPU 设备状态。")
async def comfyui_queue_status() -> str:
    try:
        status = await collect_status(settings)
        return _json(
            {
                "ok": True,
                "comfyui": status["comfyui"],
                "gateway_queue": status["queue"],
                "device": status["device"],
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(
    name="tts_speak",
    description=(
        "调用本机 TTS 引擎把文本合成语音，返回语音 URL。"
        "重要：返回的 audio_url 必须以纯文本形式原样放进你回复的最后一行"
        "（保持浏览器/容器可访问的完整 URL 不变），LangBot 会自动下载并把它转成独立的 QQ 语音消息；"
        "如果你不附上这个链接，用户将收不到任何语音。"
    ),
)
async def tts_speak(
    text: str, voice: str = "", engine: str = "kokoro", speed: float = 1.0
) -> str:
    """合成语音。

    Args:
        text: 要朗读的文本（中文）
        voice: 音色名。kokoro 可选: zf_xiaobei/zf_xiaoni/zf_xiaoxiao/zf_xiaoyi、
               af_heart/af_bella/af_nicole/af_sarah、am_adam/am_michael 等；留空用默认。
        engine: kokoro(多音色，快) / mobius(克隆音色，慢)
        speed: 语速倍率，0.5~2.0，默认 1.0
    """
    try:
        result = await do_tts(
            settings,
            {"text": text, "voice": voice, "engine": engine, "speed": speed},
            source="mcp",
        )
        return _json(
            {
                "ok": True,
                "engine": result.get("engine"),
                "voice": result.get("voice"),
                "seconds": result.get("seconds"),
                "filename": result.get("filename"),
                "bytes": result.get("bytes"),
                "audio_url": result.get("audio_url"),
                "path": result.get("path"),
                "task_id": result.get("task_id"),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(name="tts_list_voices", description="列出可用的 TTS 音色（Kokoro + 克隆音色）。")
async def tts_list_voices() -> str:
    try:
        return _json(await list_voices(settings))
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(name="media_status", description="媒体模式与各服务状态（ComfyUI/Kokoro/F5/Core/队列/设备）。")
async def media_status() -> str:
    try:
        return _json({"ok": True, **(await collect_status(settings))})
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(name="media_wake", description="手动唤醒 ComfyUI（转发 Core /v1/media/ensure 并等待就绪）。")
async def media_wake() -> str:
    try:
        return _json(await ensure_comfy(settings))
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


async def _call_core(cfg: Settings, method: str, path: str, payload: dict | None = None) -> dict[str, Any]:
    url = f"{cfg.core_url}{path}"
    headers = {"Authorization": f"Bearer {cfg.core_api_key}"}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            if method.upper() == "GET":
                resp = await client.get(url, headers=headers)
            else:
                resp = await client.request(method.upper(), url, json=payload or {}, headers=headers)
    except Exception as exc:
        raise ApiError("core_unavailable", f"Core 不可达（{url}）: {exc}", 503)
    try:
        data = resp.json()
    except Exception:
        data = {"text": resp.text[:500]}
    if resp.status_code >= 400:
        raise ApiError(
            "core_rejected",
            f"Core {method} {path} -> HTTP {resp.status_code}",
            resp.status_code,
            core=data,
        )
    return data if isinstance(data, dict) else {"ok": True, "data": data}


@server.tool(name="media_sleep", description="手动休眠 ComfyUI（仅转发 Core，Core 是媒体生命周期唯一owner）。")
async def media_sleep() -> str:
    try:
        return _json({"ok": True, "core": await _call_core(settings, "POST", "/api/media/sleep", {})})
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


@server.tool(name="media_set_mode", description="设置媒体模式：off / on_demand / prewarm（仅 Console/管理员）。")
async def media_set_mode(mode: str) -> str:
    try:
        if mode not in ("off", "on_demand", "prewarm"):
            return _json({"ok": False, "error": "invalid_mode", "mode": mode})
        return _json(
            {"ok": True, "core": await _call_core(settings, "PUT", "/api/media/mode", {"mode": mode})}
        )
    except Exception as exc:  # noqa: BLE001
        return _tool_error(exc)


# ---------------------------------------------------------------------------
# ASGI app：MCP 面 + 自定义路由 + 后台 worker
# ---------------------------------------------------------------------------


class _AppWithWorker:
    """在首个 ASGI 请求（lifespan）时拉起单并发任务 worker。"""

    def __init__(self, inner) -> None:
        self.inner = inner
        self._started = False

    async def __call__(self, scope, receive, send) -> None:
        if not self._started:
            self._started = True
            await store.bootstrap()
        await self.inner(scope, receive, send)


_mcp_app = server.streamable_http_app(
    streamable_http_path="/mcp",
    host="0.0.0.0",
    json_response=False,
    stateless_http=False,
    # 默认 30 分钟回收空闲会话。QQ 机器人经常一停就是半小时以上，
    # 会话被回收后 LangBot 侧重连又可能撞上线程池满 → 调用挂死到 180s 超时
    # （2026-10-29 23:57 实测发生过）。这里直接不回收。
    session_idle_timeout=None,
)
app = _AppWithWorker(_mcp_app)


def main() -> None:
    parser = argparse.ArgumentParser(description="Möbius Media Gateway v2 (MCP + REST + proxy)")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认取配置，8899）")
    parser.add_argument("--host", default=None, help="监听地址（默认 0.0.0.0）")
    args = parser.parse_args()
    if args.port:
        settings.apply_port(args.port)
    if args.host:
        settings.host = args.host
    settings.ensure_dirs()
    log.info("Möbius Media Gateway v%s 启动于 http://%s:%s", VERSION, settings.host, settings.port)
    log.info(
        "comfyui=%s kokoro=%s f5=%s core=%s workflows=%s",
        settings.comfy_url,
        settings.kokoro_url,
        settings.f5_url,
        settings.core_url,
        settings.workflows_dir,
    )
    print(
        f"Möbius Media Gateway v{VERSION} 启动于 http://{settings.host}:{settings.port} "
        f"(MCP=/mcp, REST=/api/*)",
        flush=True,
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
