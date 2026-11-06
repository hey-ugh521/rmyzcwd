# ADR-004：媒体网关的 MCP 面改为无状态（stateless HTTP）

- 状态：已采纳并完成（2026-11-06 13:44，提交 `3691561`）
- 决策人：产品经理（要求「群友功能不能因为网关重启就哑掉」）
- 相关：`docs/archive/reports-history/POSTMORTEM-20261028.md` §A、`docs/STATUS.md` §8

## 背景

1. 网关以 Streamable HTTP 提供 `/mcp`，**默认是有状态模式**：客户端先 `initialize`
   拿到 session id，后续请求靠一个长连接 SSE 流复用该会话。
2. QQ 侧的调用方是 LangBot。LangBot **只在启动时建立一次 MCP 会话，之后不重连**。
3. 于是形成一个组合缺陷：只要网关进程重启（改代码、崩溃、watchdog 拉起、开机竞态），
   LangBot 手里那条 SSE 流立刻变成死流，后续所有工具调用挂在死流上直到 300s 超时。
4. 线上反复出现同一个表象：「用 LoRA 画 XX」报错，但网关日志里**一条请求都没有**。
   10-28 与 11-06 各踩一次，两次都误以为是 LoRA/ComfyUI/权限问题。
5. 10-28 的第一版解法是绕过去而不是消除它：`watchdog.check_mcp_session()` 比较
   网关与 LangBot 的启动时间，网关较新就重启 LangBot 重建会话。
   这把一次性故障换成了**周期性重启风险**：11-06 12:10–13:33 共重启 LangBot 13 次。

## 备选方案

| 方案 | 说明 | 否决/采纳理由 |
|---|---|---|
| 1. 保持有状态 + watchdog 定点重启 LangBot | 现状兜底 | 否决：判据是推断（uptime 先后 + 日志字符串），误判代价是重启风暴，且恢复期用户侧不可用 |
| 2. 客户端侧加自动重连 | 改 LangBot | 否决：第三方代码，热补丁已在三处，再加一处更难维护 |
| 3. **网关 MCP 面无状态化** | `stateless_http=True` | **采纳**：消除会话概念本身，网关重启不再影响工具可用性；无需任何自愈逻辑 |
| 4. 退回 stdio MCP | 每客户端一进程 | 否决：多入口（LangBot/Core/调试）无法共享队列与锁，且 stdio 会撞上 LangBot 沙箱限制 |

## 决策

1. `media_gateway/server.py` 以 `stateless_http=True` + `session_idle_timeout=None` 构建
   Streamable HTTP 应用；每次请求独立处理，不再签发/校验会话。
2. JSON 响应仍为 `json_response=False`（保留 SSE 分块能力，工具结果大时不被截断）。
3. **保留** watchdog 的 `check_mcp_session()`，但其语义降级为兜底：
   判据要从「会话是否失效」改为「工具集是否真能列出来」，
   即直接用 `tests/probe_mcp.py` 里的 `tools/list` 探测（STATUS §6.2 第 9 条 / 选项 A）。
4. 文档层面把「MCP 面必须无状态」写进设计铁律（`docs/STATUS.md` §3 第 6 条）。

## 影响

- 正面：网关重启/升级不再是事故；僵尸 SSE 会话不再累积；LangBot 无需为此重启。
- 代价：失去服务端按会话维持的上下文（原本 MCP 会话内可记住客户端的
  progress token / 采样请求方向）。本项目工具全是**请求-响应式**，不依赖会话态，
  因此实际影响为零；若未来要做 `sampling`/`elicitation`（服务端反向调用客户端），
  需要另开一条有状态通道，不能把这条改回去。
- 验证：网关重启后立刻从 LangBot 侧调 `comfyui_list_loras`，应在秒级返回而非 300s 超时；
  同一操作在有状态模式下的旧行为已在 `logs/incidents/incident-20261106-130044-*.md` 留档。

## 回滚

改回 `stateless_http=False` 即恢复旧行为，但必须同时恢复「网关重启后重建 LangBot 会话」
的操作纪律 —— 不建议。
