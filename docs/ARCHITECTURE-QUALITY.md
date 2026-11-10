# 架构质量：SSOT / 幂等性 / 状态机

> 2026-11-08。本文记录三项基础属性的现状、已修复项与后续计划。
> 关联：`docs/L3-PLAN.md`、`docs/SECURITY-INCIDENT-2026-11-08.md`。

---

## 1. SSOT（单一事实源）

### 1.1 已成立的 SSOT

| 域 | Owner | 说明 |
|---|---|---|
| 媒体任务 | PG `task_snapshot`（迁移期） | 当前执行态持久源；**目标态见 `docs/adr/ADR-007-task-ownership.md`：Core 为业务状态唯一 owner** |
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
- 路径展开收敛到 `shared/paths.py`（`core/paths.py` 为薄兼容层，launcher 动态加载同一份）
- manifest 聚合/校验收敛到 `ops/l3/manifest_check.py`（build_release 不再内联聚合；write 保留 sha 一致组件的 parts/urls 与 release 元数据）
- 密钥统一到 `secrets.json`：Core 启动时覆盖 `core.api_key`/`llm.api_key`/`integrations key`/`db_dsn`；`credentials.py` 优先读 SSOT；各消费方视图由 `rotate.py sync` 生成，`check` 检出 drift

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
| OneBot 事件入口 | `AiocqhttpAdapter` 按 `message_id` 去重（TTL+LRU，`MOBIUS_ONEBOT_DEDUPE_TTL/MAX` 可调/关闭） | ✅ 本地 patch |

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
| `login_probe` | 调 OneBot `get_login_info`（token 取自 secrets SSOT），返回 `retcode=0` 且 `user_id` 匹配才算就绪；NapCat 使用 |
| 未配置 | 旧默认：health > extra_ports any > port |

NapCat 现使用 `login_probe`：直接查 OneBot `get_login_info`（token 从 secrets SSOT 读取），并暴露 `login_state`（unknown/awaiting_login/logged_in/error）、`login_state_since`、`login_detail`；`start()` 等待循环复用同一判据。

同时引入**显式服务状态转移表**（`STATE_TRANSITIONS`/`LOGIN_TRANSITIONS`）：合法边集中定义，非法转移写审计 `state.invalid_transition` 并告警（不阻塞运行）；`public()` 增加 `state_since`/`login_state*`。

单测：`tests/test_readiness_modes.py` → 34/34；`tests/test_service_state_machine.py` → 26/26（状态表/登录态/probe）；Console 状态历史：`tests/test_console_state_history.py` → 13/13（静态 + 在线 API）。

### 3.2 任务状态机

`pending → running → completed / failed / cancelled`（持久化 + outbox 事件 + 幂等提交），本次未改动。

### 3.3 组件安装状态机

状态：`absent → downloading → installing → installed | failed`（`installed → downloading` 支持重装/升级，`failed → downloading` 支持重试，`absent → installed` 用于既有安装回填）。

实现：`ops/l3/component_state.py`（原子写 `component_states.json`、`ComponentStateStore`、非法转移抛 `StateTransitionError`、启动时把超时未更新的 `downloading/installing` 恢复为 `failed`）；`downloader.install/repair/uninstall` 全程包裹状态，`versions.json` 保留安装记录。

验收：单测 `tests/test_component_state.py` → 24/24；真实 `python-core` 安装（6.2MB）→ `installed`（attempts=1/started/finished 完整）、重复安装幂等、卸载 → `absent`；启动器 `components` 列表与 CLI `list` 展示状态文案（下载中/安装中/已安装/失败）。

### 3.4 仍缺（后续）

| 项 | 建议 |
|---|---|
| NapCat/QQ 登录态 | 引入 `login_probe`（OneBot `get_login_info`）作为显式状态 `starting→awaiting_login→logged_in→ready` |
| ✅ 状态转换守卫 | 显式转移表 + 非法转移审计/告警（不阻塞） |
| ✅ 组件安装状态 | 已实现 `absent→downloading→installing→installed/failed`，持久化 `component_states.json`，崩溃/超时恢复为 failed |
| ✅ Console 展示 | 服务卡片显示登录态/状态持续时长；新增「最近状态变化」面板（`GET /api/services/history?limit&service`） |

---

## 4. 后续优先级

| 级别 | 项 |
|---|---|
| P1 | ✅ 密钥 SSOT 已完成：`secrets.json` + `ops/secrets/rotate.py`（init/show/check/sync/rotate），见 `docs/SECRETS-SSOT.md` |
| P1 | ✅ 已完成：`shared/paths.py` 唯一实现（Core+launcher 共用，35/35 测试）；`ops/l3/manifest_check.py`（check/write，77/77）接入 build_release，真实仓库无 drift、15 个组件包 sha 全部一致 |
| P1 | OneBot 事件级 `message_id` 去重（防重放） |
| P2 | NapCat `login_probe` 状态机 + 服务状态转移表 |
| P2 | ✅ 组件安装状态机已完成（installer 的 `components` 列表与 CLI `list` 展示状态） |
