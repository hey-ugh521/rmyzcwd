# Möbius 架构差距（GAP）与收敛计划

> 状态：M0 基线（2026-11-10）
> 对照：`ARCHITECTURE-v1.1.md`（目标）、`PRODUCT-BOUNDARY.md`（v1.0 边界）、`ARCHITECTURE-AS-IS.md`（实际）
> 原则：先冻结边界，再改代码；每个 gap 有 owner、退出条件，不允许"顺手重写"。

## 1. 已冻结的产品决策

| ID | 决策 |
|---|---|
| PB-01 | 主入口 = Windows 桌面应用；Console 是同一控制面的管理视图 |
| PB-02 | 先生图稳定，语音第二 |
| PB-03 | NVIDIA 优先，最低 8GB 显存，安装时检测/报错 |
| PB-04 | Windows 10/11 x64、单机单用户 |
| PB-05 | QQ 为可选集成，不是首次成功体验的必要条件 |
| PB-06 | 分发 = Möbius Installer + Integration Manager；版本矩阵分开记录 |
| D1 | 任务状态唯一 owner = Core；Gateway 不自存业务状态（设计先行，见 G-03） |
| D2 | legacy 退役：删 `scripts/boot-mobius.ps1`、`ops/watchdog/`，安装器不再打包 |
| D3 | `config/account_profiles.json` 移出 git 跟踪、本地留存；不改历史（残余风险已接受） |

## 2. Gap 总表

> Owner 约定：产品决策 = PM；代码实现 = Qoder（实现会话）；文档/验收 = Lead。
> 每个 gap 未满足退出条件前，不得标记关闭。

| ID | Gap | 严重度 | 证据 | 退出条件（Definition of Done） |
|---|---|---|---|---|
| G-01 | **开发副本 ≠ 安装副本**：当前运行来自开发目录，安装副本代码落后 | P0 | AS-IS §1；`D:\AI\Mobius` 无 `map_gateway_status` | 安装器产物与仓库 main 同源；干净机器安装=当前代码；发布流程强制 |
| G-02 | **双大脑 / 双记忆**：QQ 走 LangBot + memory MCP，Web/Console 走 Core；跨入口人格/记忆/审计不统一 | P0 | AS-IS §3/§4；STATUS §2/§5 | 身份、会话、记忆的权威归属明确；只有一个写入者；跨入口验证通过 |
| G-03 | **任务状态双写**：Gateway `task_snapshot` 与 Core `tasks` 并存；D1 已定 Core owner，代码未收敛 | P0 | AS-IS §4；ADR-002 / T1 映射只是兼容；**设计草案 `docs/adr/ADR-007-task-ownership.md`（Proposed）** | ADR-007 评审通过并实现（Gateway 只留执行队列/outbox，Core 唯一状态源）；契约测试全绿 |
| G-04 | **桌面应用入口缺失**：当前是 launcher GUI + 浏览器 Console；PB-01 要求桌面应用为主 | P0 | AS-IS §3.1；launcher 打开 `/console` | 桌面应用内嵌控制台/任务/诊断；用户不需要手动开浏览器 |
| G-05 | **Open WebUI 入口断开**：Pipe 在仓库，进程/端口不存在 | P1 | AS-IS §3.3；STATUS §20 | 要么纳入 Integration Manager 可诊断启动，要么从 v1.0 范围移除并文档标注 |
| G-06 | **安装器与开发者环境耦合**：README/文档出现 `D:\AI`、Anaconda、本机 ComfyUI、计划任务；安装器未验证干净机器 | P0 | README L41/L59/L89；AS-IS §1/§5 | Möbius Installer 独立；干净 Windows 安装/卸载/升级演练通过；路径/端口全部来自配置/向导 |
| G-07 | **legacy Docker/boot/watchdog 仍在仓库与安装包**：与原生 Supervisor 双保活；Docker 口径冲突 | P0 | AS-IS §6；D2 | legacy 文件删除、iss 不再打包、计划任务清理；Core Supervisor 唯一监管 |
| G-08 | **热补丁/上游变更不可复现**：LangBot 原生补丁、容器内补丁、升级后丢失 | P1 | `ops/patches/`、STATUS §8；ADR-006 引用 watchdog | 补丁可版本化/自动检测/重放/失败回滚；或改用正式接口 |
| G-09 | **文档口径冲突**：README 写 Docker/on_demand/SQLite；STATUS/ACCEPTANCE 记录 prewarm/PG/QQ 断开；用户无法判断现状 | P0 | README L59/L69；STATUS L100/L164/L170；AS-IS §8 | README 只写"用户当前可用"；STATUS 只写现状/已知问题；ARCHITECTURE 只写冻结契约；三者交叉引用并 CI 检查关键默认值 |
| G-10 | **测试分层不足**：E2E 依赖真实环境；缺契约/故障注入/安装测试；`public_url` 依赖本机 config | P1 | 2026-11-09 审查；tests/ 现状 | 单元/契约/集成/E2E/故障注入/安装测试分层；CI 可跑单元+契约；干净环境跑 E2E |
| G-11 | **安全残余**：回环免鉴权 + body 自报 user_id；`/mcp` 开放（ADR-006）；密钥仍有明文副本；卸载不清敏感文件 | P1 | AS-IS §7；ADR-006；SECURITY-INCIDENT | 管理/任务接口分级或本机 token；明文副本收口；卸载策略明确；安全验收清单实测 |
| G-12 | **版本/兼容矩阵缺失**：Möbius、第三方组件、工作流、模型版本未分开记录 | P1 | PRODUCT-BOUNDARY §7 | Release 附版本矩阵；安装器/Integration Manager 记录并校验 |
| G-13 | **任务恢复语义不完整**：取消即终态、迟到产物/unknown 恢复、bootstrap/outbox 重试边界未闭环 | P1 | 2026-11-09 审查；STATUS §8/§15 | 任务状态机 + 恢复/取消/重复回调测试通过 |
| G-14 | **配置遗留**：`f5` 与 `gsv` 端口冲突（7871），f5 disabled；prewarm 代码兜底/示例值与运行值不一致 | P2 | AS-IS §2.1/§8；STATUS §15 | 配置清理；默认值单一来源；CI 校验 example 与 ADR-005 一致 |

## 3. 与目标架构的差距（分层看）

| 层 | 目标 | 实际 | 差距 |
|---|---|---|---|
| Presentation | 桌面应用为主 | launcher + 浏览器 Console | G-04 |
| Control Plane | Core 唯一权威（人格/会话/记忆/任务/审计） | Core 只覆盖 Web/Console；QQ 旁路 | G-02/G-03 |
| Task Runtime | 持久队列/幂等/恢复/租约 | Gateway 有幂等/outbox；Core 仅投影；租约/恢复不完整 | G-03/G-13 |
| Execution Plane | Provider Adapter 隔离 ComfyUI/TTS | Gateway 内嵌协议适配，Provider 抽象未落地 | M2 工作（G-13 相关） |
| Resource | 统一资源状态机与调度 | Core Supervisor 有状态机，但 legacy watchdog 并存 | G-07/G-14 |
| Persistence | 唯一写入者、迁移/备份 | PG 已有，但双写/双库；无迁移 runner/安装备份验收 | G-02/G-03/G-11 |
| Delivery | Installer + Integration Manager + 版本矩阵 | 单层安装器、环境耦合、无矩阵 | G-01/G-06/G-12 |

## 4. 收敛顺序（不跳步）

### M0（本文件交付，2–3 天）
- 边界冻结：`PRODUCT-BOUNDARY.md`
- 现状冻结：`ARCHITECTURE-AS-IS.md`
- 差异冻结：本文
- 文档口径修复：README 顶部声明 + STATUS 指向本三文件（不做大改）
- 验收：三文件评审通过；所有 gap 有 owner/退出条件；无代码改动（本阶段）

### M1（任务生命周期与数据一致性）
- G-03 设计 → 实现（Core owner；Gateway 只留执行队列/outbox）
- 起点：`docs/adr/ADR-007-task-ownership.md`（Proposed，待评审后进入实现）
- G-13 任务恢复/取消/重复回调测试
- 退出：单写者验证 + 契约测试 + 故障注入（杀进程/断网/重复提交）

### M2（媒体解耦与资源调度）
- Provider Adapter 契约；ComfyUI/TTS 适配器
- 资源状态机与 GPU 调度；legacy 清理（G-07/G-08）
- 退出：更换 Provider 不改 Core 状态机；资源冲突/休眠误杀测试通过

### M3（安全与生命周期）
- G-11 安全边界；安装/升级/备份/恢复/卸载（G-06）
- 退出：安全清单实测；干净 Windows 安装/卸载演练

### M4（桌面入口与首次体验）
- G-04 桌面应用；向导/诊断/任务中心
- 退出：新用户不读开发者文档完成"安装→生图→关闭→再启动找回历史"

### M5（发布质量）
- G-10/G-12：CI、契约/故障注入、版本矩阵、CHANGELOG/迁移说明
- 退出：可持续发布，不再人工搭环境

## 5. M0 验收清单

- [ ] `PRODUCT-BOUNDARY.md` 评审通过（目标用户/平台/入口/硬件/范围/指标）
- [ ] `ARCHITECTURE-AS-IS.md` 与实际运行一致（进程/端口/写入者/依赖/缺口均有证据）
- [ ] 本文 gap 总表每项有 owner、严重度、退出条件
- [ ] README/STATUS 明确"以本三文件为准"，并修正最危险的口径冲突（Docker/prewarm/SQLite）
- [ ] 不改核心代码；不重启生产；变更仅文档
- [ ] 下一步 M1 仅从 G-03/G-13 开始（D1 设计先行）

## 6. 风险与备注

- **不要**为了"工业级"一次性重构；每个 M 阶段只允许一个主目标进入实现。
- **不要**把 Qoder/Lead 的测试通过当作产品验收；M0 的验收是"文档与边界可被陌生人理解并执行"。
- 当前运行环境是开发者机器；M0 采集不能替代干净 Windows 安装测试。
- ADR-006 的 `/mcp` 开放决策已接受；若未来要加鉴权，先解决 LangBot MCP 自定义 header 能力。
- T8 删除 watchdog 时同步修正 ADR-006 中的 `ops/watchdog/watchdog.py` 引用。
