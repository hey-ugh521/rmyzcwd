# ADR-006：网关鉴权边界（/api/* 走 token，/mcp 与媒体回传口保持开放）

- 状态：已采纳（2026-11-10，产品经理工单要求「决定 /mcp 是否也要 token；不要则在文档明确接受」）
- 决策人：产品经理
- 相关：`docs/adr/ADR-004-mcp-stateless.md`（LangBot 只建一次会话、否决改 LangBot）、
  `docs/SECURITY-INCIDENT-2026-11-08.md`（密钥泄露事故，硬化动机）、`docs/SECRETS-SSOT.md`（token 来源）

## 背景

1. 第二批 T5 给媒体网关加了 Bearer token：配置 `gateway.token`（密钥 SSOT `secrets.json`）
   或环境变量 `MOBIUS_GATEWAY_TOKEN` 后，`/api/*` 强制鉴权
   （`media_gateway/server.py:2388` 的 `api_route` 守卫），`/api/pulse` 豁免
   （`media_gateway/server.py:2391`，watchdog 唯一探针，见 `ops/watchdog/watchdog.py:78`）。
   token 解析：`media_gateway/server.py:211`（env 优先，其次 `_secret_gateway_token()` 读 SSOT）。
2. 网关默认只监听回环 `127.0.0.1`（`media_gateway/server.py:189`）；`streamable_http_app(host=settings.host)`
   （`media_gateway/server.py:3175`）在回环 host 下由 mcp 库自动启用 DNS-rebinding 防护（校验 Host/Origin）。
3. `/mcp` 的唯一生产消费方是 LangBot。ADR-004 已确立两条硬约束：
   - LangBot **只在启动时建立一次 MCP 会话、之后不重连**；
   - 「改 LangBot」被明确否决——第三方代码、热补丁已在三处，再加一处更难维护。
4. LangBot 的 streamable-http MCP 客户端配置格式为 `{serverName, transport, url, ...}`
   （`ops/dsh/mobius-ops.yml:13`、`docs/archive/reports-history/D1-dsh-ops-readonly-report.md:47`），
   **未见 headers 字段**；在 LangBot 自有源码中亦未检索到 MCP 自定义 header 支持。
   即：无法让 LangBot 在 MCP 请求上注入 `Authorization` 头。
5. 媒体回传口 `/img`、`/audio`、`/upload/{id}`、`/health` 同样被 LangBot/原生回传直接 GET，
   无法注入头（与 `/mcp` 同理）。

## 备选方案

| 方案 | 说明 | 否决/采纳理由 |
|---|---|---|
| 1. `/mcp` 也强制 token + 同步 LangBot 配置 | 给 MCP 面加鉴权中间件，并让 LangBot 带 token | **否决**：LangBot MCP 配置无 headers 字段、ADR-004 已否决改 LangBot；强行加会击穿唯一消费方，QQ 侧生图/语音全哑 |
| 2. `/mcp` 仅在非回环时强制 token | 回环开放、暴露时鉴权 | **否决**：LangBot 无法注入头，「非回环 + LangBot」组合本就不可用；给 MCP 关键路径（ADR-004 列为不可击穿）加中间件，风险高收益低 |
| 3. **`/mcp` 与媒体口保持开放 + 回环默认 + 非回环 fail-fast 要求 token** | 用监听边界与启动校验补偿 MCP 面不鉴权 | **采纳**：零破坏 LangBot；`/api/*` 可锁；误暴露在启动即被拦 |

## 决策

1. **`/api/*` 走 token**：配置 token 后强制 Bearer 鉴权，`/api/pulse` 豁免。未配置 token 则不鉴权（向后兼容）。
2. **`/mcp` 不鉴权，保持开放——本文即为「明确接受」记录**。理由见背景 3/4：唯一消费方 LangBot
   无法注入 header，且 ADR-004 否决改 LangBot；`/mcp` 已有「回环默认 + DNS-rebinding 防护」两道边界。
3. **媒体回传口 `/img`、`/audio`、`/upload/{id}`、`/health` 同样保持开放**（LangBot/原生回传需直接可达）。
4. **补偿控制（fail-fast）**：监听地址非回环且未配置 token 时，**启动直接失败**
   （`media_gateway/server.py:3231` `_enforce_bind_security`，回环判定 `:3217` `_is_loopback_host`）。
   语义＝「要把网关暴露到非回环，就必须先用 token 锁住 `/api/*`」，杜绝裸奔。
5. **运维铁律**：生产拓扑下 LangBot / Core / watchdog 与网关同机，网关**必须保持回环监听**。
   若确需非回环（跨机调试等），`/mcp` 与媒体口**必须由前置反向代理鉴权**，不得裸暴露公网。

## 影响

- 正面：`/api/*` 不再裸奔；非回环误配置在启动即被拦下；`/mcp` 与媒体回传兼容性零破坏（LangBot 不受影响）。
- **残留风险（明确接受）**：非回环模式下 `/mcp` 与媒体口仍开放（token 不覆盖它们）。
  仅当操作者「配了 token 但绑非回环、且未加反代」时存在——此时 `/api/*` 已锁，
  但 `/mcp` 工具（生图/TTS）可被网络直接调用 → 资源滥用。缓解：回环默认 + 第 4 条 fail-fast + 第 5 条铁律 + 反代要求。
- 与 `SECURITY-INCIDENT-2026-11-08` 的关系：该事故是**密钥泄露**（发布资产含明文 secrets），
  本 ADR 是**传输面鉴权边界**，二者正交；token 本身仍须存于 `secrets.json`（SSOT），不得硬编码或入库。

## 回滚

若要给 `/mcp` 加鉴权，前提是**先让 LangBot MCP 客户端支持自定义 header**（上游能力，目前不具备），
再在 `streamable_http_app` 外层加鉴权中间件并同步 LangBot 配置。在前提满足前，不应单独给 `/mcp` 加 token，
否则会击穿 QQ 侧全部媒体能力。移除 `_enforce_bind_security` 调用即可回退第 4 条 fail-fast（不建议）。
