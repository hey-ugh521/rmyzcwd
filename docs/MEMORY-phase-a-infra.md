# 记忆系统 Phase A：基础设施验收报告

> 时间：2026-11-02 18:28–19:05
> 结论：**重型方案可行性验证通过**（Graphiti + FalkorDB + DeepSeek + 本地 bge-m3）

## 1. 架构与选型

| 组件 | 选型 | 理由 |
|---|---|---|
| 图数据库 | FalkorDB 4.x（Docker，宿主 127.0.0.1:63790） | Graphiti 官方支持；内存占用小（百 MB 级），适合笔记本；避开 scoop redis 占用的 6379 |
| 记忆引擎 | graphiti-core（独立 venv `D:\AI\mobius-memory`） | 时序知识图谱：事实有效期、失效不删除、episode 溯源 |
| 抽取 LLM | DeepSeek API（`deepseek-chat`，json_object 模式） | 已实测结构化输出可用（1.27s，合法 JSON）；比本地小模型可靠 |
| 嵌入 | **本地 bge-m3**（sentence-transformers，CPU，1024 维，OpenAI 兼容服务 8903） | 隐私（记忆不出发）；Ollama 官方库不可达、云端嵌入 403/404 的替代方案 |

## 2. 验收

| # | 标准 | 结果 |
|---|---|---|
| A1 | FalkorDB 容器可运行、可持久化 | ✅ `PONG`；卷 `mobius-falkordb-data` |
| A2 | 本地嵌入可用 | ✅ 1024 维；单批 0.31s（加载后） |
| A3 | Graphiti 可导入、可建索引 | ✅ `build_indices_and_constraints` 完成 |
| A4 | **中文 episode 写入** | ✅ 4.9s（含 DeepSeek 实体/事实抽取） |
| A5 | **中文检索命中** | ✅ 查询"零玖是谁"返回 3 条：身份/文学叙事引擎/拓扑心理学 |
| A6 | 结构化输出可靠性 | ✅ DeepSeek json_object 模式解析通过 |

**实测输出**：
```
1) 建索引 ... 完成
2) 写入 episode（中文）... 完成（4.9s）
3) 检索 '零玖是谁' ...
   - 零玖的身份是 world_state_manager。
   - 零玖是 Möbius 的文学叙事引擎。
   - 零玖喜欢拓扑心理学。
   检索命中 3 条（5.0s） RESULT: PASS
```

## 3. 过程中排除的障碍

| 问题 | 处理 |
|---|---|
| Ollama 拉取 bge-m3 失败 | `ollama.com` 在本机不可达（超时），改用 sentence-transformers + HF 镜像（hf-mirror.com 可达） |
| 云端嵌入单点 | LangBot Models 403、DeepSeek 无 embeddings 接口 → 必须本地嵌入 |
| 端口冲突 | scoop 的 redis-server 占用 6379 → FalkorDB 映射到 63790 |
| Graphiti 默认 reranker 需要 OpenAI key | 自定义 `NoopReranker`（本地无重排），避免外网依赖 |
| transformers 离线加载 | 用本地快照路径加载 bge-m3，`HF_HUB_OFFLINE=1` |

## 4. 运行中的服务

| 服务 | 地址 | 启动方式 |
|---|---|---|
| FalkorDB | 127.0.0.1:63790 | Docker（restart unless-stopped） |
| 嵌入服务 | 127.0.0.1:8903 | `ops/memory/embed_server.py`（Python 进程） |
| Graphiti | 库（venv） | 由记忆 MCP 服务调用 |

## 5. 待办（Phase B/C/D）

- **B**：`ops/memory/memory_mcp.py`（:8902）——add/search/entity/forget/stats，group_id 隔离
- **C**：LangBot 挂 MCP + 提示词记忆规则 + L1 历史摘要 + 对话自动写入
- **D**：夜间巩固（冲突失效/衰减）+ 图备份 + ops 观测 + boot/watchdog 保活

## 6. 配置与凭据

- DeepSeek key 复用 LangBot 已配置的 provider（`model_providers` 表）
- 图数据库/嵌入服务仅绑定 127.0.0.1
- Graphiti 遥测使用 PostHog（匿名），Phase B 将设 `GRAPHITI_TELEMETRY_ENABLED=false`
