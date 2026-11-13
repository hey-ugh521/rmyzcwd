# Möbius 文档索引

> 先看这里，再按用途进目录。历史材料不要直接当当前契约使用。

## 当前（current）

| 文档 | 用途 |
|---|---|
| [`../README.md`](../README.md) | 项目入口、运行方式、目录结构 |
| [`PRODUCT-BOUNDARY.md`](PRODUCT-BOUNDARY.md) | 产品边界与 v1 交付范围 |
| [`ARCHITECTURE-AS-IS.md`](ARCHITECTURE-AS-IS.md) | 当前实际运行架构 |
| [`ARCHITECTURE-GAP.md`](ARCHITECTURE-GAP.md) | 目标 vs 实际 / 收敛计划 |
| [`ACCEPTANCE.md`](ACCEPTANCE.md) | 验收标准与端到端流程 |
| [`STATUS.md`](STATUS.md) | 当前状态与已验收能力 |
| [`current/REGISTRY-LIFECYCLE.md`](current/REGISTRY-LIFECYCLE.md) | Registry 生命周期契约 |
| [`current/REGISTRY-API.md`](current/REGISTRY-API.md) | Registry API 契约 |

## ADR

[`adr/`](adr/) 记录已接受的架构决策，原则上一个决定一份文件，不改写历史结论。

- `ADR-002` 状态词表
- `ADR-003` PostgreSQL 迁移
- `ADR-004` MCP stateless
- `ADR-005` 媒体模式默认值
- `ADR-017` PG Shadow Validation 维护窗口隔离
- `ADR-006` Gateway 认证边界
- `ADR-007` 任务归属
- `ADR-008` 媒体 Provider 资源
- `ADR-009` 生命周期安全
- `ADR-010` Registry 生命周期
- `ADR-011` Control Plane / Execution Plane 边界
- `ADR-012` Registry Runtime 迁移状态与旧读关闭
- `ADR-013` 统一能力调用边界
- `ADR-014` 能力权限、Provider 策略与记录保留
- `ADR-015` Provider 动态健康与降级
- `ADR-016` 权限 Scope 语义

## 计划与设计

[`aegis/plans/`](aegis/plans/) 只放当前有效的实施计划/设计契约。

- `2026-11-12-core-qq-adapter.md`：QQ adapter B1
- `2026-11-13-capability-registry-contract.md`：Registry 架构契约 v2
- 当前：[Identity Data Model Contract](current/IDENTITY-DATA-MODEL.md)
- 设计审查：[Phase 4F-2B Identity Shadow](aegis/plans/2026-11-13-phase4f2b-shadow-design.md)
- 下一阶段计划：[Phase 4F 下一阶段计划](aegis/plans/2026-11-13-phase4f2b-implementation-plan.md)
- 2B-1 报告：[Shadow Event 数据模型](reports/PHASE4F2B1-SHADOW-EVENT-REPORT.md)
- 2B-2 报告：[Shadow 观察器与 RoutePolicyEvaluator](reports/PHASE4F2B2-SHADOW-EVALUATOR-REPORT.md)
- 2B-3 报告：[Shadow Middleware 接线](reports/PHASE4F2B3-SHADOW-MIDDLEWARE-REPORT.md)
- 2B-4 报告：[运行证据与冻结](reports/PHASE4F2B4-SHADOW-EVIDENCE-REPORT.md)
- 2B 生产部署：[Shadow 关闭部署](reports/PHASE4F2B-PRODUCTION-DEPLOY-REPORT.md)
- PG Shadow 验证计划：[维护窗口执行计划](aegis/plans/2026-11-13-pg-shadow-validation-plan.md)
- PG Shadow 执行前证据：[Preflight Evidence](reports/PG-SHADOW-PREFLIGHT-EVIDENCE.md)
- PG Shadow 验证结果：[Validation Report](reports/PHASE4F2B-PG-SHADOW-VALIDATION-REPORT.md)
- 4F-2C 前置检查：[4F-2C Preflight](aegis/plans/2026-11-14-phase4f2c-preflight.md)
- 2B-3 前置检查：[Phase 4F-2B-3 前置检查清单](aegis/plans/2026-11-13-phase4f2b3-preflight.md)
- 测试可复现性：[3.13 复跑报告](reports/TEST-REPRODUCIBILITY-2026-11-13.md)- 阶段报告：[Phase 0](reports/PHASE0-REGISTRY-REPORT.md)、[Phase 0.1](reports/PHASE0.1-REGISTRY-HARDENING-REPORT.md)、[Phase 1](reports/PHASE1-REGISTRY-READONLY-REPORT.md)、[Phase 2A](reports/PHASE2A-REGISTRY-MANAGEMENT-REPORT.md)、[Phase 2C](reports/PHASE2C-WORKFLOW-VOICE-FORM-REPORT.md)、[Phase 3A](reports/PHASE3A-RUNTIME-RESOLVER-REPORT.md)、[Phase 3B](reports/PHASE3B-MIGRATION-LEGACY-CLOSE-REPORT.md)、[Phase 4A](reports/PHASE4A-CAPABILITY-INVOCATION-REPORT.md)、[Phase 4B](reports/PHASE4B-CAPABILITY-POLICY-REPORT.md)、[Phase 4C](reports/PHASE4C-PROVIDER-HEALTH-REPORT.md)、[Phase 4D](reports/PHASE4D-POLICY-STUDIO-SCOPE-REPORT.md)、[Phase 4E](reports/PHASE4E-POLICY-VALIDATION-REPORT.md)、[Phase 4F-1](reports/PHASE4F1-IDENTITY-DATA-REPORT.md)、[Phase 4F-2A](reports/PHASE4F2A-LEGACY-MAPPING-REPORT.md)、[Live hot update](reports/REGISTRY-LIVE-HOTUPDATE-REPORT.md)
- 已取代的 v1 设计在 [`archive/aegis/`](archive/aegis/)

## 报告

[`reports/`](reports/) 放当前阶段的验收/实施报告。命名建议：

```text
YYYY-MM-DD-<topic>-report.md
```

历史阶段报告已归档到 [`archive/reports-history/`](archive/reports-history/)。

## 归档

[`archive/`](archive/) 只读历史材料：

- 旧架构版本
- 旧实施报告
- 旧 prompt 记录
- 已取代的 Aegis 设计稿

归档内容不作为当前契约；需要恢复时从 Git 历史或归档中取。

## 运维与测试

- `ops/`：组件、发布、探针、补丁、许可证、语音评估脚本
- `tests/`：自动化验收与回归脚本
- `configs/workflows/`：ComfyUI workflow 模板与模型注册表
- `data/characters/`：当前角色档案 JSON

## 本地运行产物

以下目录/文件是**本机运行产物或隐私数据**，不应提交：

- `config.json`、`secrets.json`
- `data/*.db`、`logs/`
- `backups/`
- `release/`、`launcher/dist/`、`launcher/build/`、`installer/dist/`
- `web/studio/node_modules/`、`web/studio/dist/`
- `media_gateway/{data,logs,tts_output,uploads}/`
- `ops/probe/state.db`

统一规则见根目录 [`.gitignore`](../.gitignore)。
