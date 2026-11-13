# ops 目录

运维、构建、组件、迁移和实验脚本。这里不是运行时包；很多脚本只在本机开发/验收时使用。

## 目录

| 路径 | 说明 |
|---|---|
| `l3/` | 组件化、发布构建、下载器、许可证、签名、安装包辅助 |
| `l2/` | L2 阶段脚本 |
| `patches/` | 旧 LangBot / 网关热补丁 |
| `probe/` | 探针与状态数据库（`state.db` 本地忽略） |
| `secrets/` | 密钥轮换/样例（真实 secrets 不入库） |
| `napcat/` | NapCat 启动包装 |
| `tts/`, `voice_eval/` | TTS / 音色评估 |
| `image/`, `memory/`, `tracing/`, `dsh/`, `dev/` | 专题实验 |
| `tests/` | 运维级验证脚本 |

## 根目录脚本

根目录保留的是被文档、启动器或历史流程直接引用的脚本，例如：

- `l3/build_release.ps1`、`l3/build_component.ps1`
- `l3/downloader.py`、`l3/component_state.py`
- `l3/secret_scan.py`
- `napcat` 包装
- Phase 0/1 迁移、探针、备份相关脚本

不要为了“目录好看”移动这些根脚本；移动前先检查 `docs/`、`launcher/`、`installer/` 和 `tests/` 的引用。

## 约定

- 运行时数据、备份、探针数据库不提交。
- 新脚本优先按用途进子目录；只有被现有路径引用时才放根目录。
- 历史补丁不要当当前行为契约；当前行为以 `docs/current`、ADR 和代码为准。
