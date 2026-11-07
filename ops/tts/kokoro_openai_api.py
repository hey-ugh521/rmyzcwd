"""
Kokoro TTS - OpenAI 兼容 API 服务器
用于接入 Open WebUI 的 TTS 设置
"""

import os
import sys

# ==================== 网络配置 ====================
# 使用 HuggingFace 镜像（国内加速）
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["CURL_CA_BUNDLE"] = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""
os.environ["SSL_CERT_FILE"] = ""

# 设置本地模型缓存路径（可用 MOBIUS_KOKORO_DIR 覆盖；默认 <MOBIUS_ROOT>/models/kokoro）
_app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODEL_DIR = os.environ.get("MOBIUS_KOKORO_DIR") or os.path.join(_app_root, "models", "kokoro")
HF_HOME = os.path.join(MODEL_DIR, "hf_home")
os.environ["HF_HOME"] = HF_HOME
os.environ["HUGGINGFACE_HUB_CACHE"] = os.path.join(HF_HOME, "hub")

# ==================== 导入 ====================
import argparse
import io
import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from huggingface_hub import hf_hub_download
from kokoro import KPipeline
from kokoro.model import KModel
import uvicorn
import torch
import ssl

ssl._create_default_https_context = ssl._create_unverified_context

# 预下载 voice 文件列表
VOICE_FILES = {
    "zf_xiaobei": "voices/zf_xiaobei.pt",
    "zf_xiaoni": "voices/zf_xiaoni.pt",
    "zf_xiaoxiao": "voices/zf_xiaoxiao.pt",
    "zf_xiaoyi": "voices/zf_xiaoyi.pt",
    "af_heart": "voices/af_heart.pt",
    "af_bella": "voices/af_bella.pt",
    "af_nicole": "voices/af_nicole.pt",
    "af_sarah": "voices/af_sarah.pt",
    "af_alloy": "voices/af_alloy.pt",
    "am_adam": "voices/am_adam.pt",
    "am_michael": "voices/am_michael.pt",
    "am_liam": "voices/am_liam.pt",
    "am_echo": "voices/am_echo.pt",
    "bf_emma": "voices/bf_emma.pt",
    "bf_isabella": "voices/bf_isabella.pt",
    "bm_george": "voices/bm_george.pt",
    "bm_fable": "voices/bm_fable.pt",
    "ef_dora": "voices/ef_dora.pt",
    "em_alex": "voices/em_alex.pt",
    "em_carmen": "voices/em_carmen.pt",
    "ff_siwis": "voices/ff_siwis.pt",
    "hf_alpha": "voices/hf_alpha.pt",
    "hm_omega": "voices/hm_omega.pt",
    "if_sara": "voices/if_sara.pt",
    "im_nicola": "voices/im_nicola.pt",
    "pf_dora": "voices/pf_dora.pt",
    "pm_alex": "voices/pm_alex.pt",
    "pm_santos": "voices/pm_santos.pt",
}

AVAILABLE_VOICES = sorted(VOICE_FILES.keys())

# 语言映射
LANG_MAP = {
    "af": "a", "am": "a",
    "bf": "b", "bm": "b",
    "ef": "e", "em": "e",
    "ff": "f",
    "hf": "h", "hm": "h",
    "if": "i", "im": "i",
    "pf": "p", "pm": "p",
    "zf": "z", "zm": "z",
}

# ==================== API 服务 ====================
app = FastAPI(title="Kokoro TTS API", version="1.0.0")

# 共享模型实例（所有管道共用）
_shared_model = None
_shared_model_lock = None

def get_shared_model():
    global _shared_model
    if _shared_model is None:
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
        print(f"正在加载 Kokoro 模型到 {device}...（首次加载较慢）")
        _shared_model = KModel(repo_id='hexgrad/Kokoro-82M').to(device).eval()
        print("模型加载完成！")
    return _shared_model

def get_pipeline(voice: str) -> KPipeline:
    prefix = voice.split("_")[0] if "_" in voice else voice[:2]
    lang_code = LANG_MAP.get(prefix, "a")
    model = get_shared_model()
    pipeline = KPipeline(lang_code=lang_code, model=model)
    return pipeline

class TTSRequest(BaseModel):
    model: str = "tts-1"
    input: str = ""
    voice: str = "zf_xiaobei"
    response_format: str = "wav"
    speed: float = 1.0

class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    owned_by: str = "kokoro"
    permission: list = []

class ModelListResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]

@app.get("/v1/models")
async def list_models():
    models = [ModelInfo(id=v) for v in AVAILABLE_VOICES]
    return ModelListResponse(data=models)

@app.get("/v1/models/{voice}")
async def get_model(voice: str):
    if voice not in AVAILABLE_VOICES:
        raise HTTPException(status_code=404, detail=f"Voice '{voice}' not found")
    return ModelInfo(id=voice)

@app.post("/v1/audio/speech")
async def text_to_speech(req: TTSRequest):
    if not req.input.strip():
        raise HTTPException(status_code=400, detail="input is required")

    voice = req.voice
    if voice not in AVAILABLE_VOICES:
        raise HTTPException(status_code=400, detail=f"Unknown voice: {voice}")

    try:
        pipeline = get_pipeline(voice)
        generator = pipeline(
            req.input, voice=voice,
            speed=req.speed,
            split_pattern=r"\n+"
        )

        all_audio = []
        for _, _, audio in generator:
            if audio is not None:
                all_audio.append(audio.numpy())

        if not all_audio:
            raise HTTPException(status_code=500, detail="Synthesis failed")

        combined = np.concatenate(all_audio)

        buf = io.BytesIO()
        sf.write(buf, combined, 24000, format="WAV")
        wav_data = buf.getvalue()

        return Response(
            content=wav_data,
            media_type="audio/wav",
            headers={"Content-Disposition": 'inline; filename="speech.wav"'}
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kokoro TTS OpenAI API Server")
    parser.add_argument("--port", type=int, default=7870, help="Server port")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Server host")
    args = parser.parse_args()

    print(f"Kokoro TTS API 启动在 http://{args.host}:{args.port}")
    print(f"模型缓存目录: {HF_HOME}")
    print(f"可用音色: {len(AVAILABLE_VOICES)} 个")

    # 预下载模型文件
    print("检查模型文件...")
    try:
        config_path = hf_hub_download(
            repo_id='hexgrad/Kokoro-82M',
            filename='config.json',
            local_dir=os.path.join(HF_HOME, "models"),
            local_dir_use_symlinks=False
        )
        print(f"  config.json: 已就绪")
    except Exception as e:
        print(f"  config.json 下载失败: {e}")
        print("  尝试使用本地文件...")
        local_config = os.path.join(MODEL_DIR, "config.json")
        if os.path.exists(local_config):
            os.makedirs(os.path.join(HF_HOME, "models", "hexgrad", "Kokoro-82M"), exist_ok=True)
            import shutil
            shutil.copy(local_config, os.path.join(HF_HOME, "models", "hexgrad", "Kokoro-82M", "config.json"))
            print(f"  已复制本地 config.json")

    try:
        model_path = hf_hub_download(
            repo_id='hexgrad/Kokoro-82M',
            filename='kokoro-v1_0.pth',
            local_dir=os.path.join(HF_HOME, "models"),
            local_dir_use_symlinks=False
        )
        print(f"  kokoro-v1_0.pth: 已就绪")
    except Exception as e:
        print(f"  模型下载失败: {e}")
        print("  尝试使用本地文件...")
        local_model = os.path.join(MODEL_DIR, "kokoro-v1_0.pth")
        if os.path.exists(local_model):
            os.makedirs(os.path.join(HF_HOME, "models", "hexgrad", "Kokoro-82M"), exist_ok=True)
            import shutil
            shutil.copy(local_model, os.path.join(HF_HOME, "models", "hexgrad", "Kokoro-82M", "kokoro-v1_0.pth"))
            print(f"  已复制本地模型文件")

    print()
    print(f"在 Open WebUI 设置中配置:")
    print(f"  TTS 引擎: OpenAI 兼容")
    print(f"  API 地址: http://localhost:{args.port}")
    print(f"  API Key: (留空)")
    print()

    uvicorn.run(app, host=args.host, port=args.port, workers=1)