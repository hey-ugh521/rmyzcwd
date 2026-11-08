# 安全事件记录：公开仓库/Release 泄露（2026-11-08）

> 状态：已止血（Release 删除、仓库转 Private）、活密钥全部轮换、可达 Git 历史清理、组件/安装包重建并通过扫描。
> 待决策：是否以「全新仓库」重新公开（GitHub 可能保留 force-push 前的不可达对象）。

## 1. 暴露清单（外部审计提供）

| 级别 | 载体 | 内容 |
|---|---|---|
| P0 | GitHub Release 资产 `langbot-20261108.7z` | `data/config.yaml`（global_api_key、PG/Redis 密码、secret、recovery_key）+ `langbot.db` 及 6 个备份库（2,109 条真实聊天、model_providers 明文 LLM Key、bots.adapter_config、chroma 向量库） |
| P0 | `napcat-code-*.7z` | OneBot HTTP token、WebUI token、autoLoginAccount、QQ 号 |
| P1 | `MobiusSetup-0.2.0.exe` | 内嵌真实 `config.json`（Core api_key、DeepSeek key、PG DSN、LangBot key），下载 2 次 |
| P1 | `docs/archive/reports-history/LANGBOT-CUTOVER.md:54` | 明文 Core api_key |
| P2 | git 历史 | `config.json.bak` blob、watchdog.py 历史版本中的 LangBot key、旧 DeepSeek key、旧 PG 密码 |

## 2. 处置时间线（2026-11-08）

1. **止血**：删除 Release `l3-20261108`（23 个资产 + tag）；仓库 `hey-ugh521/rmyzcwd` 临时转 **Private**。
2. **轮换活密钥**：
   - Core `api_key`
   - LangBot `global_api_key` / `secret` / `recovery_key`
   - NapCat HTTP token / WS token / WebUI token（同步 `.env`、LangBot `bots.adapter_config`）
   - （此前已轮换）DeepSeek Key、PG 口令
3. **重启验证**：Core/Gateway/LangBot 全 ready；NapCat 重新登录（苍玄之书）；OneBot 连接恢复。
4. **Git 清理**：替换历史中 4 个残留敏感值、删除 `config.json.bak`、文档明文改 `<REDACTED>`；force-push（可达引用已干净）。
5. **重建分发包**：
   - `langbot` 包：排除 `.venv/data/logs/tests/skills` → 182MB → **8.9MB**，扫描干净
   - `napcat-code` 包：排除 `config/logs/node_modules/*.db*` → 扫描干净
   - 安装器：排除 `*.db`，确认无 `config.json`（实测安装后不存在），自签 Valid
6. **发布闸门**：新增 `ops/l3/secret_scan.py`（扫描密钥模式 + 敏感文件名，命中即 exit 1）。
7. **二次扫描补充发现并修复**：
   - `comfyui-runtime` 包含 `ComfyUI/user/comfyui.db`（用户设置/插件凭据）→ 排除 `models/cache/input/output/user` 后重建（3.96GB → **3.34GB**，扫描干净）
   - 安装器会把 `ops/probe/state.db` 打进去 → 安装器排除 `*.db/*.db-wal/*.db-shm`
   - 文档中 4 处明文（Core key、WS token、WebUI token）→ `<REDACTED>` 并重写历史
8. **发布流水线硬闸门**：`build_release.ps1` 在收集产物前扫描仓库文件与每个组件包，命中即失败（演练通过）。

## 3. 残留风险

| 风险 | 说明 | 建议 |
|---|---|---|
| GitHub 不可达对象 | force-push 后旧 commits/blobs 可能仍被 GitHub 保留；删除的 Release 资产同理 | 重新公开前**删除并重建仓库**（全新历史），或联系 GitHub Support 清理 |
| 2,109 条聊天记录 | 曾存在于已删除的公开资产（下载数 0） | 按"可能泄露"对待；如需可评估通知相关用户 |
| LangBot Cloud Key | 旧 `langbot.db` 中 `model_providers` 含 `api.langbot.cloud` Key（下载数 0） | 不用该 provider 可删除；用则重置 |
| QQ 号 | 出现在文件名/autoLoginAccount | 无法轮换；接受公开 |
| 旧安装包 2 次下载 | 内含旧 Core/LangBot Key、旧 PG 口令 | Key 均已轮换；PG 口令早于本次已轮换 → 均已失效 |

## 4. 重新公开前检查清单

- [ ] 删除并重建仓库（或确认 GitHub 已清除不可达对象）
- [ ] 运行 `python ops/l3/secret_scan.py` 扫描源码树与每个组件包
- [ ] 确认组件包不再包含 `data/`、`config/`、`*.db`、`.env`
- [ ] 确认安装器无 `config.json`
- [ ] 确认所有历史密钥均已轮换
- [x] 在发布脚本中加入 secret_scan 作为硬闸门（已实现）
