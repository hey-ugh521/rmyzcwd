"""OpenAI 兼容的本地嵌入服务（bge-m3，CPU）。

用途：给 Graphiti / 记忆服务提供 /v1/embeddings。
启动：
  D:\\AI\\mobius-memory\\Scripts\\python.exe ops\\memory\\embed_server.py --port 8903
端点：
  POST /v1/embeddings  {"model":"bge-m3","input":["文本1","文本2"]}
  GET  /health
"""

from __future__ import annotations

import argparse
import os
import time
from typing import Any

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

MODEL_NAME = os.environ.get("MOBIUS_EMBED_MODEL", "bge-m3")
MODEL_PATH = os.environ.get("MOBIUS_EMBED_PATH", r"D:\AI\models\bge-m3-st")

app = FastAPI(title="mobius-embed", version="1.0")
_model: SentenceTransformer | None = None


def _load() -> SentenceTransformer:
    global _model
    if _model is None:
        t0 = time.time()
        _model = SentenceTransformer(MODEL_NAME, cache_folder=MODEL_PATH, device="cpu")
        print(f"loaded {MODEL_NAME} in {time.time()-t0:.1f}s", flush=True)
    return _model


class EmbeddingsRequest(BaseModel):
    model: str = "bge-m3"
    input: list[str] | str


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "model": MODEL_NAME, "loaded": _model is not None}


@app.post("/v1/embeddings")
def embeddings(req: EmbeddingsRequest) -> dict[str, Any]:
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    if not texts:
        return {"object": "list", "data": [], "model": req.model}
    t0 = time.time()
    vecs = _load().encode(texts, normalize_embeddings=True, batch_size=8)
    return {
        "object": "list",
        "model": req.model,
        "data": [
            {"object": "embedding", "index": i, "embedding": [float(x) for x in vec]}
            for i, vec in enumerate(vecs)
        ],
        "usage": {"prompt_tokens": sum(len(t) for t in texts), "total_tokens": sum(len(t) for t in texts)},
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8903)
    args = ap.parse_args()
    _load()  # 启动即加载，避免首请求超时
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
