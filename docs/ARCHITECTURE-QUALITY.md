# 架构质量：SSOT / 幂等性 / 状态机

> 2026-11-08。本文记录三项基础属性的现状、已修复项与后续计划。
> 关联：`docs/L3-PLAN.md`、`docs/SECURITY-INCIDENT-2026-11-08.md`。

---

## 1. SSOT（单一事实源）

### 1.1 已成立的 SSOT

| 域 | Owner | 说明 |
|---|---|---|
| 媒体任务 | PG `task_snapshot` | 提交/状态/结果唯一持久源 |
| 会话消息 | LangBot PG | 对话与消息 |
| 审计/上下文 | Core（`mobius_core`） | 审计、turns |
| 角色卡 | `data/characters/*.json` | Gateway/Core 共读 |
| 服务定义 | `config.json` | `${VAR}` 展开；Core 消费 |

### 1.2 已识别的 SSOT 违反

| # | 问题 | 风险 | 计划 |
|---|---|---|---|
| 1 | 密钥 4 个副本（`.env`、`config.json`、LangBot `config.yaml`、NapCat 配置） | 轮换需改多处，本次安全事件已暴露 | 引入单一 `secrets.json`（或 Credential Manager），其余位置由生成脚本产出 |
| 2 | LLM 配置双所有者（Core `config.llm` vs LangBot `model_providers`） | 换 Key 需改两处 | 选 Core 为 owner，LangBot 同步/只读引用 |
| 3 | 路径展开两份实现（`core/paths.py`、launcher 内联） | 规则漂移 | 抽公共模块，launcher 打包时引用 |
| 4 | manifest 源/聚合两份（`packs/*.json` vs `fragments.json`） | 旧分卷清单复活（已发生，已加 sha 校验） | `fragments.json` 定为生成物，构建时重算 + drift 校验 |
| 5 | 凭据 fallback 链（env → `.env` → `config.json`） | 排障不清 | 明确定义优先级并在健康检查中输出"生效来源" |

### 1.3 本次已修

- `fragments.json` 聚合保留逻辑加 sha 校验（防止旧 parts 复活）
- 安装器排除 `config.json`/`*.db`；组件包排除 `data/`、`config/`、用户数据

---

## 2. 幂等性

### 2.1 矩阵

| 操作 | 幂等机制 | 状态 |
|---|---|---|
| 生图任务提交 | 显式 `idempotency_key` 优先；缺失时按**事件身份**派生：`sha256(source/message_id/event_id/user_id/session_id)[:32]`；同 key 同 payload → 原任务，同 key 异 payload → 409 | ✅ 本次完成 |
| MCP 异步工具 | 新增可选 `message_id`/`event_id` 参数并写入 body；提示词明确"优先复用同一消息" | ✅ |
| Core 服务启动 | 探测 ready → 直接返回；进程在跑 → 不重复 spawn；冷却/退避 | ✅ |
| NapCat 启动 | 命名互斥 + 进程/端口探测 no-op + UAC | ✅ |
| 外部实例停止 | `?force=true` 按 `kill_pattern` 清理 | ✅ |
| 组件安装/卸载 | 已装跳过；依赖检查 | ✅ |

### 2.2 关键设计变更（v2）

派生串**只含事件身份，不含 payload**：

- 同一消息重试 → 同 key，payload 差异由存储层 `payload_hash` 判 409，防止重复出图
- 不同消息（新用户请求）→ 不同 key，正常新建任务
- 无 `message_id/event_id` 且无 `session_id+user_id` → 不派生（保持原随机行为）

### 2.3 验收证据

- 单测：`tests/test_idempotency_derive.py` → 42/42（两个解释器）
- Live：
  - 首次提交 → `derived=true`、key 为 32 位 hash
  - 同 message_id 同 payload → `idempotent_hit=true`、同 task_id
  - 同 message_id 异 payload → **HTTP 409**
  - 不同 message_id → 新 task_id
  - 测试任务已取消

---

## 3. 状态机

### 3.1 服务状态 + 就绪判据

状态集合：`unknown → down / starting / ready / busy / error / disabled`

新增 `ready_when`：

| 模式 | 语义 |
|---|---|
| `health` | 只测 `health_url`（缺失回退 port） |
| `ports_all` | 所有声明端口监听才算 ready |
| `ports_any` | 任一端口（旧语义） |
| `port` | 只测主端口 |
| 未配置 | 旧默认：health > extra_ports any > port |

NapCat 使用 `ports_all`（3000+3001 都在监听 = 已登录的代理判据，无需读 token），修掉了"3000 开、3001 未开也 ready"的误报。`start()` 等待循环复用同一判据。

单测：`tests/test_readiness_modes.py` → 34/34。

### 3.2 任务状态机

`pending → running → completed / failed / cancelled`（持久化 + outbox 事件 + 幂等提交），本次未改动。

### 3.3 仍缺（后续）

| 项 | 建议 |
|---|---|
| NapCat/QQ 登录态 | 引入 `login_probe`（OneBot `get_login_info`）作为显式状态 `starting→awaiting_login→logged_in→ready` |
| 状态转换守卫 | 显式转移表（合法边 + 事件），非法转换写审计 |
| 组件安装状态 | `versions.json` 增加 `absent/downloading/installing/installed/failed` |
| Console 展示 | 暴露 `ready_when`（已加入 `/api/services` 返回）与状态历史 |

---

## 4. 后续优先级

| 级别 | 项 |
|---|---|
| P1 | 密钥 SSOT + 一键轮换脚本（读一处写多处） |
| P1 | 路径展开抽公共模块；manifest 生成物 drift 校验 |
| P1 | OneBot 事件级 `message_id` 去重（防重放） |
| P2 | NapCat `login_probe` 状态机 + 服务状态转移表 |
| P2 | 组件安装状态机 + Console 展示 |
