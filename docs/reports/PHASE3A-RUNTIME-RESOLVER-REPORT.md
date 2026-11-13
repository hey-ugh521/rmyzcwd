# Phase 3A 实施报告：Registry Runtime Resolver + Workflow Graph 运行时

- 日期：2026-11-13
- 状态：已受控切换到 `enforce`，仅对已发布资源生效；未发布资源回退 legacy
- 基础：[Registry 架构契约 v2](../aegis/plans/2026-11-13-capability-registry-contract.md)
- 上一阶段：[Phase 2C 报告](PHASE2C-WORKFLOW-VOICE-FORM-REPORT.md)
- 代码提交：`969fb97`

## 1. 运行时状态机

新增 `core/registry_runtime.py`：

```text
off      -> 完全保持旧行为（默认）
shadow   -> 读取 published、记录差异、仍返回 legacy
enforce  -> 有 published 则使用 Registry，无 published 回退 legacy
```

配置：

```json
"registry": { "runtime_mode": "enforce" }
```

也支持 `MOBIUS_REGISTRY_RUNTIME` 环境变量覆盖。

每次读取只解析 **published revision**，未发布资源不进入运行时。
回退会写 `audit: registry.runtime.fallback`；shadow 写
`registry.runtime.shadow`。

## 2. Agent 接入

`core/agent.py` 的三处角色读取改为：

```text
registry_runtime.character(...)
```

覆盖：

- `_system_prompt` 的角色 id
- `_do_image` 的 media_policy / workflow / checkpoint / LoRA / style prompt
- `_do_voice` 的 voice profile

发布资源存在时：

- 角色名称、style prompt、negative、workflow、checkpoint、size、LoRA、media_policy 从 published revision 读取
- voice 从 published `voice_profile_id` 或角色 payload/source 读取
- 未发布角色继续走旧文件

## 3. Gateway 使用已发布 Workflow

新增 Gateway 请求字段：

```json
{"workflow_graph": {"1": {"class_type": "...", "inputs": {}}}}
```

- Core 在 enforce 模式下解析 published workflow
- Agent 将 graph 传给 Gateway
- Gateway 跳过文件加载，直接执行 graph
- 仍保留 TaskStore 任务、GPU lease、输出、取消、超时
- 结果 workflow 标签为 `registry:<id>`

只接受 Core 已解析的 graph；Gateway 仍只做本机 `/api` 鉴权。

## 4. Live 验证

生产配置：

```text
registry.runtime_mode = enforce
```

已发布：

- character `bronya` revision 1
- workflow `comic-portrait` revision 1

验证：

| 项目 | 结果 |
|---|---|
| `registry_runtime.status()` | `enforce` |
| resolver char | 名称/style/workflow/voice/`_registry` 正确 |
| resolver workflow alias | `portrait` → `comic-portrait` revision 1 |
| Gateway graph runtime | `mobius_registry_00001_.png`，37.3s |
| Agent image enforce | `task t_28db5e2f`，`mobius_registry_00002_.png` |
| Agent TTS enforce | `task t_c65931bb`，audio URL 返回 |
| 未发布资源 | 自动回退 legacy |
| Runtime status API | `GET /api/registry/manage/runtime/status` |

## 5. 验证

| 项目 | 结果 |
|---|---|
| `tests/test_registry_runtime.py` | 5/5 |
| `tests/test_registry_manage.py` | 7/7 |
| `tests/test_voice_assets.py` | 4/4 |
| `tests/test_registry_lifecycle.py` | 16/16 |
| `pnpm typecheck` | OK |

## 6. 限制

- 只发布了两个资源用于验收；其他角色/workflow 仍回退 legacy。
- 还没有完整的迁移状态机自动化（`LEGACY_DISCOVERED` → `RUNTIME_SWITCHED`）。
- 没有关闭旧文件读路径，没有 `LEGACY_READ_DISABLED`。
- VoiceAsset 尚未真正参与 TTS 合成；当前 voice 来自 VoiceProfile/legacy。
- published 资源的删除/禁用回退策略还需对照测试。
- Core 当前为直接启动进程，不是受控服务重启路径；生产 launcher 下次启动会恢复正常管理。
- 生产日志 ACL 已临时放开当前用户写权限，便于非提权重启服务。

## 7. 回滚

- Runtime 模式：把 `config.json` 的 `registry.runtime_mode` 改为 `off` 并重启 Core。
- 配置备份：`D:\AI\backups\config-runtime-20261113-123715\config.json`
- 代码备份：`D:\AI\backups\phase3a-20261113-122404`
- Gateway graph 修复备份：`D:\AI\backups\gateway-graph-fix-20261113-123013`

## 8. 停止点

Phase 3A 到此停止。

不删除 legacy 读路径，不进入 Phase 3B 的完整迁移状态机/干净安装收口。
