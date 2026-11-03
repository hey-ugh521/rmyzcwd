"""GPT-SoVITS 微调模型的 OpenAI 兼容 TTS 适配层（供 Möbius Gateway 调用）。

- 上游：GPT-SoVITS 原生 API  http://127.0.0.1:7873/tts
- 下游：http://127.0.0.1:7871/v1/audio/speech、/v1/models
- 参考音使用训练集里的干净片段；权重为八重樱微调模型。

运行：runtime\\python.exe D:\\AI\\gsv_openai_adapter.py
"""

from __future__ import annotations

import typing

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

UPSTREAM = "http://127.0.0.1:7873/tts"
REF_AUDIO = r"D:/AI/yae_dataset/segments/vocal_p3_44k.wav_10_002.wav"
REF_TEXT = "舰长，喜欢樱花吗？明年的春天，一起去赏樱吧。"
SPEED = 1.0
PORT = 7871

app = FastAPI(title="Mobius GSV TTS")


class SpeechRequest(BaseModel):
    model: str = "tts"
    input: str
    voice: typing.Optional[str] = None
    response_format: typing.Optional[str] = "wav"
    speed: typing.Optional[float] = 1.0


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": "mobius", "object": "model", "owned_by": "mobius"}]}


@app.get("/health")
async def health():
    return {"ok": True}


@app.post("/v1/audio/speech")
async def speech(req: SpeechRequest):
    speed = (req.speed if req.speed and req.speed > 0 else SPEED) * 0.9  # 八重樱语速略慢
    payload = {
        "text": req.input,
        "text_lang": "zh",
        "ref_audio_path": REF_AUDIO,
        "prompt_text": REF_TEXT,
        "prompt_lang": "zh",
        "text_split_method": "cut0",
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
        "speed_factor": speed,
    }
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(UPSTREAM, json=payload)
            if r.status_code != 200:
                return JSONResponse(
                    {"error": f"upstream {r.status_code}", "detail": r.text[:500]},
                    status_code=502,
                )
            return Response(content=r.content, media_type="audio/wav")
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"tts failed: {exc}"}, status_code=503)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
