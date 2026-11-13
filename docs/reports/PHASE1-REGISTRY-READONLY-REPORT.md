# Phase 1 实施报告：Read-only Registry + Studio 能力库

- 日期：2026-11-13
- 状态：Phase 1 只读索引完成，等待审查；未进入 Phase 2
- 基础：[Registry 架构契约 v2](../aegis/plans/2026-11-13-capability-registry-contract.md)
- 上一阶段：[Phase 0.1 报告](PHASE0.1-REGISTRY-HARDENING-REPORT.md)

## 1. 范围

只做只读索引：

- 读取现有角色 JSON
- 读取 ComfyUI workflow JSON 与 `models.json`
- 从角色卡聚合音色引用
- 通过 `/api/registry/index/*` 暴露只读投影
- Studio 新增「能力库」页面

不做：

- 不写 Registry DB
- 不导入/迁移旧资源
- 不修改 Agent/Gateway/TTS/ComfyUI
- 不做编辑、发布、试跑
- 不进入 Phase 2

## 2. 后端

新增：

- `core/registry_index.py`
- `core/registry_index_api.py`
- `core/server.py`：挂载 `/api/registry/index/*`

索引来源：

- 角色：`characters_dir` 下 `*.json`
- 工作流：`configs/workflows` / `MOBIUS_WORKFLOWS_DIR` / `${paths.AI}/comic/workflows`
- 模型：工作流目录下 `models.json`
- 音色：角色卡 `voice` 字段聚合，带 `used_by`

只读特性：

- 不执行 workflow
- 不执行 JSON 导入
- 不修改源文件
- 不创建 DB 资源

## 3. Studio

新增 `/registry`「能力库」页：

- Summary：角色 / 音色 / 工作流 / 图像模型数量
- Tabs：角色 / 音色 / 工作流
- 搜索
- 详情面板：
  - 角色：来源、workflow、checkpoint、尺寸、音色、LoRA、checksum
  - 音色：engine、voice、引用角色
  - 工作流：格式（API prompt / UI workflow）、节点数、节点类型、引用角色、checksum
- 显示只读标记；角色详情可跳转现有角色编辑器

## 4. 验证

| 项目 | 结果 |
|---|---|
| `tests/test_registry_index.py` | 3/3 |
| `pnpm typecheck` | OK |
| `pnpm build` | OK |
| 实际 Core app `GET /api/registry/index` | 200，13 角色 / 4 音色 / 9 工作流 / 6 模型 |
| Studio mock 页面 | 正常渲染、详情可打开 |

## 5. 限制

- 当前是**文件索引**，不是 Registry DB 中的已发布资源。
- 音色是角色卡聚合结果，不是完整 VoiceProfile。
- Workflow 只展示格式/节点摘要，不做 node 依赖/安全验证。
- 没有编辑/导入/发布；没有运行时切换。
- 生产 Core 尚未重启加载新索引路由；本次验证使用 Core TestClient 与 Studio mock。

## 6. 停止点

Phase 1 到此停止。

不进入 Phase 2，不接入 Agent/Gateway。
