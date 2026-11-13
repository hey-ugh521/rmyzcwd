# Phase 2A 实施报告：Registry 资源管理（草稿导入/校验）

- 日期：2026-11-13
- 状态：Phase 2A 完成，等待审查；Runtime 未接入
- 基础：[Registry 架构契约 v2](../aegis/plans/2026-11-13-capability-registry-contract.md)
- 上一阶段：[Phase 1 报告](PHASE1-REGISTRY-READONLY-REPORT.md)
- 代码提交：`e91bb01`

## 1. 范围

Phase 2A 只做资源管理的最小闭环：

- 旧角色/音色/workflow 文件 → Registry draft
- Character Card V2/V3 JSON 导入
- ComfyUI API Prompt JSON 校验 → Registry draft
- JSON payload 编辑 → 新 revision
- publish / 版本历史 / 回滚到历史版本
- Studio「资源管理」页面

未做：

- 不修改 Agent/Gateway/TTS/ComfyUI 运行时读取
- 不执行 workflow
- 不做 VoiceAsset/参考音频上传
- 不做 ComfyUI 节点依赖在线检查/试跑
- 不做完整角色卡表单（当前用 JSON payload 编辑 + 现有角色编辑页）
- 不进入 Phase 3

## 2. 后端

新增：

- `core/registry_manage.py`
  - `normalize_character_card()`：V2/V3/legacy → canonical draft payload
  - `import_character_card()`
  - `validate_workflow()`：API Prompt 结构、输出节点、KSampler/尺寸参数范围、高权限节点告警
  - `import_workflow()`：仅 valid 的 API Prompt 可落 draft
  - `import_legacy()`：批量导入旧角色/音色/API workflow，幂等跳过
- `core/registry_manage_api.py`
  - `POST /api/registry/manage/import/legacy`
  - `POST /api/registry/manage/characters/import`
  - `POST /api/registry/manage/workflows/validate`
  - `POST /api/registry/manage/workflows/import`
- `core/server.py` 挂载管理路由

所有导入都只创建 draft，不自动发布。

## 3. Studio

新增 `/registry/manage` 资源管理页：

- 导入旧资源
- 导入角色卡 JSON
- 导入/校验 API workflow JSON
- Registry 资源列表
- JSON payload 编辑并保存为新 draft revision
- 发布当前 revision
- 版本历史 + 发布历史版本（回滚）
- 只读提示：发布不会接入 Runtime

「能力库」页增加「资源管理」入口。

## 4. 验证

| 项目 | 结果 |
|---|---|
| `tests/test_registry_manage.py` | 5/5 |
| `tests/test_registry_index.py` | 3/3 |
| `tests/test_registry_lifecycle.py` | 16/16 |
| `tests/test_registry_api.py` | 8/8 |
| `tests/test_registry_core_auth.py` | 6/6 |
| `tests/test_qq_webhook.py` | 5/5 |
| `pnpm typecheck` | OK |
| `pnpm build` | OK |

## 5. 限制 / 后续

- 只支持 API Prompt JSON；UI Workflow JSON 明确拒绝。
- `validate_workflow` 是高权限节点的**静态告警**，不等于 ComfyUI 自定义节点安全证明。
- 没有 ComfyUI `/object_info` 在线依赖检查。
- 没有 workflow 试跑。
- 没有 VoiceAsset（参考音频/模型/权重/预览缓存）管理。
- 角色编辑仍是 JSON payload；完整表单/导入向导属于 Phase 2B。
- 生产 Core 尚未加载 Phase 2A 路由；当前验证为测试和 Studio mock/build。

## 6. 停止点

Phase 2A 到此停止。

不接入 Runtime，不进入 Phase 3。
