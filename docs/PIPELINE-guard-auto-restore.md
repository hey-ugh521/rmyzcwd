# Pipeline 提示词防覆盖：根源排查与自动恢复

> 时间：2026-11-02 12:03–12:06

## 1. 排查结论

| 项 | 结论 |
|---|---|
| 存储位置 | PG `legacy_pipelines.config`（不是文件），LangBot 启动时 `pipelinemgr.py: Loading pipelines from db...` |
| 历史覆盖事件 | guard 脚本注释记录：2026-11-01 15:54（SQLite 时期）config 被改成 1284 字符默认模板，P0.4 提示词丢失 |
| 云同步 | `config.yaml` 只有 `cloud.directory` 限制项与 `cloud_migration`，**没有自动拉取/覆盖 pipeline 的开关**；LangBot 云端模块（`pkg/cloud/*`）用于 Cloud 部署模式，本机 OSS 模式不参与 |
| 现有状态 | guard `--check` 一致（len=6948，sha256 `0638d25f…`），当前无漂移 |

**判断**：覆盖来源是**外部写入方**（WebUI 编辑/云端同步按钮/版本迁移），LangBot 侧没有可关闭的自动同步开关。因此修复方向不是"关掉某个开关"，而是**让本地快照权威 + 检测到漂移自动回滚**。

## 2. 实现（三层防护）

| 层 | 机制 |
|---|---|
| 快照 | `pipeline_guard.py --snapshot` 同时保存指纹（`pipeline-guard.json`）与**提示词全文**（``pipeline-prompt.txt`，17,486 字节） |
| 检测 | `--check` 比对 role/len/sha256/exception_handling，漂移时 exit 1 |
| 自愈 | `--restore` 将全文写回 PG；watchdog 每 30 分钟检查，漂移时自动 restore + 重启 LangBot 重载流水线 |

## 3. 验收

| # | 标准 | 结果 |
|---|---|---|
| 1 | 明确覆盖来源 | ✅ 外部写入（非 LangBot 自动同步），无开关可关 |
| 2 | 快照含全文 | ✅ 17,486 字节 |
| 3 | 漂移检测 | ✅ 注入 `// DRIFT-TEST` → 检出 len/sha 差异，exit 1 |
| 4 | 手动恢复 | ✅ `--restore` 后 len 回 6948，`--check` 一致 |
| 5 | watchdog 自动恢复 | ✅ 注入漂移 → 日志「检测到漂移，尝试自动恢复 → 恢复成功 → 重启 langbot 重载流水线」 |
| 6 | 恢复后流水线可用 | ✅ LangBot 重启后正常 |

## 4. 使用规则（重要）

```powershell
# 编辑人设提示词后，必须重建基准，否则下次检查会把你的修改回滚
python ops\pipeline_guard.py --snapshot

# 随时查看当前状态
python ops\pipeline_guard.py --show
```

## 5. 遗留

- 自动恢复会重启 LangBot（约 40 秒），只在漂移时触发，频率受 30 分钟检查间隔限制
- 若用户通过 WebUI 编辑提示词，编辑后必须立即 `--snapshot`，否则会被回滚
- 根因的"外部写入方"未能定位到具体按钮/时机（日志无写入审计），后续可在 LangBot 侧加 pipeline 更新审计
