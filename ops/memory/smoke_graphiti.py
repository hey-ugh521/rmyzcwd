"""Graphiti 中文冒烟：FalkorDB + DeepSeek(JSON) + 本地 bge-m3。

运行：
  D:\\AI\\mobius-memory\\Scripts\\python.exe ops\\memory\\smoke_graphiti.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone

from graphiti_core import Graphiti
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.nodes import EpisodeType


class NoopReranker(CrossEncoderClient):
    """本地无重排：返回原始顺序（避免依赖 OpenAI rerank）。"""

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(p, 1.0) for p in passages]

DS_KEY = os.environ.get("MOBIUS_DEEPSEEK_KEY", "sk-ROTATED-REMOVED")
FALKOR = dict(host="127.0.0.1", port=63790, database="mobius_memory")
EMBED = "http://127.0.0.1:8903/v1"


async def main() -> int:
    t0 = time.time()
    llm = OpenAIGenericClient(
        config=LLMConfig(
            api_key=DS_KEY, model="deepseek-chat", small_model="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
        ),
        structured_output_mode="json_object",
    )
    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key="local", embedding_model="bge-m3", embedding_dim=1024, base_url=EMBED,
        )
    )
    driver = FalkorDriver(**FALKOR)
    g = Graphiti(graph_driver=driver, llm_client=llm, embedder=embedder, cross_encoder=NoopReranker())

    print("1) 建索引 ...")
    await g.build_indices_and_constraints()
    print("   完成")

    print("2) 写入 episode（中文）...")
    await g.add_episode(
        name="零玖人设第一版",
        episode_body=(
            "零玖是 Möbius 的文学叙事引擎，身份是 world_state_manager。"
            "零玖喜欢文学、拓扑心理学，说话克制。"
            "用户是高级用户，喜欢让零玖用叙事方式回答。"
        ),
        source=EpisodeType.text,
        source_description="smoke-test",
        reference_time=datetime.now(timezone.utc),
        group_id="test-user",
    )
    print(f"   完成（{time.time()-t0:.1f}s）")

    print("3) 检索 '零玖是谁' ...")
    results = await g.search("零玖是谁", group_ids=["test-user"], num_results=5)
    for r in results:
        print("   -", type(r).__name__, str(getattr(r, "fact", getattr(r, "name", "")))[:100])
    print(f"   检索命中 {len(results)} 条（{time.time()-t0:.1f}s）")
    print("RESULT:", "PASS" if results else "FAIL")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
