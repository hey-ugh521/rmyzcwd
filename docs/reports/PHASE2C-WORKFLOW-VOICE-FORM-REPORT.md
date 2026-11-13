# Phase 2C 实施报告：Workflow 试跑、VoiceAsset、完整角色表单

- 日期：2026-11-13
- 状态：Phase 2C 完成并已热更新到 `D:\AI\Mobius`；Runtime 仍未读取 Registry
- 基础：[Registry 架构契约 v2](../aegis/plans/2026-11-13-capability-registry-contract.md)
- 上一阶段：[Phase 2A/2B 报告](PHASE2A-REGISTRY-MANAGEMENT-REPORT.md)
- 热更新报告：[Registry live hot update](REGISTRY-LIVE-HOTUPDATE-REPORT.md)
- 代码提交：`7addf45`

## 1. Workflow 试跑

链路：

```text
Studio 资源管理 → Core POST /api/registry/manage/workflows/{id}/test
→ Media Gateway POST /api/workflows/test
→ ComfyUI /prompt → /history → /img
```

特性：

- 只执行 Registry 当前 revision 的 graph，不改 published
- 不创建 TaskStore 任务，不占任务生命周期
- 复用 Gateway 的 ComfyUI 唤醒、GPU lease、超时和输出路径
- 只接受已验证的 API Prompt / nodes-dict workflow
- Gateway `/api` 原有鉴权保留
- Core 返回 `prompt_id`、耗时、图片 URL

Live 验证：

- `comic-portrait` 试跑成功：`prompt_id=e1dfe2a2-...`，44.3s
- 输出：`comic_portrait_00002_.png`
- `/img` 返回 200，image/png，1,352,665 bytes
- Studio「试跑 workflow」按钮可显示结果图

## 2. VoiceAsset

新增：

- `core/voice_assets.py`
- 表 `registry_voice_assets`
- 存储目录：`data/voice_assets`，可用 `registry.voice_assets_dir` 覆盖
- 校验：kind、扩展名、25MB 上限、reuse_policy
- 元数据：filename、checksum、size、mime、source、consent、reuse_policy
- 软删除：`deleted=1`，不删除文件

API：

```text
GET    /api/registry/manage/voice-assets
POST   /api/registry/manage/voice-assets
GET    /api/registry/manage/voice-assets/{id}
DELETE /api/registry/manage/voice-assets/{id}
```

Studio「音色」页增加 VoiceAsset 素材库与上传入口。

Live 验证：

- 上传 `probe.wav` → 200
- 返回 id `va_4adb3eb09344`、sha256、size 72
- 删除 → 200（软删除）

## 3. 完整角色表单

`/registry/manage` 角色详情新增「角色表单」：

- 名称 / 别名
- 默认 workflow / 默认 checkpoint
- 系统提示词（system block）
- 角色描述 / 性格 / 场景
- 说话风格 / 关系立场 / 视觉锚点 / 备注
- allow_image / allow_voice
- 保存 → 新 draft revision
- 保留 JSON payload 高级编辑

Live 验证：

- `vita` 表单加载
- 点击「保存表单为 draft」→ revision 从 r1 变为 r2
- published 仍为空

## 4. 验证

| 项目 | 结果 |
|---|---|
| `tests/test_registry_manage.py` | 7/7 |
| `tests/test_voice_assets.py` | 4/4 |
| `tests/test_registry_lifecycle.py` | 16/16 |
| `tests/test_registry_api.py` | 8/8 |
| `pnpm typecheck` | OK |
| `pnpm build` | OK |
| Live workflow 试跑 | OK |
| Live VoiceAsset 上传/删除 | OK |
| Live 角色表单保存 | OK |

## 5. 限制

- 试跑不落 TaskStore，不可查询/取消/复用；只是验证执行，不是正式生成任务。
- 试跑不经过 Registry published 指针；执行的是当前 revision。
- VoiceAsset 还没有被角色/VoiceProfile 引用，也没有 consent UI 强制。
- 软删除保留文件，磁盘回收属于后续维护。
- 角色表单覆盖常用字段，不覆盖全部 CharaCard 字段；复杂字段仍用 JSON。
- Agent/Gateway 仍未读取 Registry；Runtime 接入仍属下一阶段。

## 6. 停止点

Phase 2C 到此停止。

不进入 Runtime 接入。
