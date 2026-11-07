# Möbius L3 规划：可迁移的离线绿色包

> 目标：从"本机可运行"升级为"换一台 Windows 机器也能双击运行"。
> 现状：L2 已完成（去 Docker、Core 统管、便携 PG、启动器 exe、安装包），但运行时与模型仍绑定本机绝对路径。
> 日期：2026-11-08

---

## 0. 体积盘点（实测）

| 组件 | 体积 | 是否必须随包 | 备注 |
|---|---|---|---|
| Core 的 Python 环境（open-webui） | 3.44 GB | 是（可瘦身） | 346 个包，实际 import 远少于此 |
| Gateway Python 环境（qqbot-mcp） | 0.09 GB | 是 | 很轻 |
| 记忆 Python 环境（mobius-memory） | 1.07 GB | 是 | sentence-transformers/torch |
| LangBot 源码 + venv | 1.14 GB | 是 | venv 0.70 GB |
| 便携 PostgreSQL | 1.09 GB | 是 | 二进制 + 数据 |
| 原生 NapCat（含 QQ 9.9.33） | 1.50 GB | 是 | 便携包 |
| ComfyUI 运行时（不含模型） | 6.98 GB | 是（生图） | 便携 Python + custom_nodes |
| ComfyUI 模型 | 19.31 GB | 否 | 迁移/下载 |
| GPT-SoVITS 运行时（不含权重） | 约 7 GB（估） | 是（语音） | 含 torch/CUDA 运行库 |
| GPT-SoVITS 权重 | 约 14 GB（估） | 否 | 迁移/下载 |
| 其他模型（bge-m3/emotion2vec 等） | 9.38 GB | 部分 | 嵌入模型必须，其余可选 |
| 社区语音模型（Rosmontis 等） | 0.53 GB | 否 | 迁移 |
| **运行时合计（不含模型）** | **约 22 GB** | | |
| **全量（含模型）** | **约 60–86 GB** | | |

结论：**全量打包不现实（体积/分卷/校验成本高）**，推荐「运行时绿色包 + 模型迁移向导」。

---

## 1. 目标定义（分级）

| 级别 | 定义 | 交付形态 | 体积 |
|---|---|---|---|
| **L3-A 绿色目录包** | 拷到新机器任意目录可启动；模型用向导迁移 | 7z/分卷目录包 | ~22 GB |
| L3-B 离线安装器 | 一个 exe 安装运行时，模型迁移/下载 | Inno + 分卷 | ~22 GB |
| L3-C 全量离线 | 连模型一起 | 分卷 60GB+ | 不推荐 |

**建议先做 L3-A**，验收"整目录复制到新盘可跑"；再做 L3-B 安装器。

---

## 2. 关键设计

### 2.1 根目录契约

```
MobiusRoot\
├─ app\            # core / media_gateway / ops / config.json / data
├─ runtime\        # python 环境、uv、langbot、postgres、napcat
├─ data\           # pg 数据、日志、上传、tts_output
├─ models\         # 模型根（可指向外部磁盘）
└─ launcher\       # Mobius.exe / MobiusCLI.exe
```

所有配置只允许出现 `%MOBIUS_ROOT%` 相对路径；启动时由 launcher 注入 `MOBIUS_ROOT` 环境变量。

### 2.2 路径改造（最大工作量）

- 现状：45 个文件 / 160 处硬编码（`ops/l2/hardcoded-paths.csv`）
- 策略：
  1. 运行时相关（core/services.py、config.json、launcher、start/postgres 脚本）→ 改为 `MOBIUS_ROOT` 派生
  2. 运维/评测一次性脚本（ops/voice_eval 等）→ 保持绝对路径但移出包，不阻塞
  3. ComfyUI `extra_model_paths.yaml`、GPT-SoVITS 模型配置 → 用 `models\` 根自动生成
- 验收：把 `MobiusRoot` 整体改名/换盘后仍能启动（不改任何文件）

### 2.3 Python 运行时离线化

| 环境 | 方案 |
|---|---|
| Core | 依赖审计后新建 `mobius-core` 精简环境（目标 < 1GB），`uv venv --relocatable` |
| Gateway | 新建精简环境（依赖少） |
| 记忆 | 保留 torch CPU/sentence-transformers，relocatable |
| LangBot | 保留 uv 项目，打包 venv + 离线 wheelhouse（`uv pip install --offline --find-links`） |
| ComfyUI / GPT-SoVITS | 使用其便携 Python（本身 relocatable），只打包代码与运行库 |

关键点：
- venv 必须 relocatable，并在打包后用新路径做冒烟测试
- 打包 wheelhouse（约 1–2 GB），断网可重建环境
- 固定 torch/CUDA 版本（cu128/cu130），与驱动要求写入文档

### 2.4 数据与模型

| 项 | 方案 |
|---|---|
| PostgreSQL | 首次运行 `initdb` 空库 + 导入 `dump\*.sql`；或打包模板 data 目录（体积小） |
| LangBot 数据 | 打包现有 data（plugin/config/日志），PG 数据随 dump |
| NapCat 登录 | 不打包登录态（换机需重新扫码），首次向导引导 |
| 模型 | 向导：①从旧目录迁移（robocopy）②指定已有路径 ③下载（按清单 URL） |
| ComfyUI 模型路径 | `extra_model_paths.yaml` 指向 `models\comfyui` |
| GPT-SoVITS 权重 | 启动前校验路径，缺失则提示 |

### 2.5 首次运行向导（launcher 扩展）

1. 环境检查（复用 preflight）
2. 选择模型根（默认 `MobiusRoot\models`，支持外接盘）
3. 迁移模式：扫描旧安装（注册表/常见路径）→ 复制 PG 数据、LangBot data、模型
4. QQ 登录：启动 NapCat，显示 WebUI/二维码链接
5. 自检：全部服务 ready + 一条测试消息

---

## 3. 阶段与验收

| 阶段 | 内容 | 工期 | 验收标准 |
|---|---|---|---|
| P1 路径抽象 | MOBIUS_ROOT 契约、config 化、45 文件替换 | 3–5 天 | 目录复制/改名后不改文件即可启动 |
| P2 运行时离线化 | 四套 Python 环境 relocatable + wheelhouse；ComfyUI/GSV 便携化 | 7–10 天 | 断网、无 conda、全新目录启动成功 |
| P3 数据与模型策略 | PG init/导入、模型根、extra_model_paths 生成、迁移脚本 | 3–5 天 | 新机器导入旧数据后记忆/消息历史正常 |
| P4 首次向导 | launcher 向导（检测/迁移/模型/QQ 登录/自检） | 5–7 天 | 向导全程无手工命令行 |
| P5 绿色包 | 7z 分卷（2GB/卷）+ 校验清单 + 安装说明 | 3–5 天 | 断网新机解压即可运行 |
| P6 安装器 v2 | Inno 集成绿色包/分卷、卸载、启动目录自启 | 2–3 天 | 安装后 10 分钟内可聊天 |

**合计约 4–5 周**（单人）。

---

## 4. 验收清单（L3-A 定义完成）

- [ ] 全新 Windows（无 Python/Docker/conda），解压绿色包
- [ ] 断网状态双击 Mobius.exe → UAC → 服务全部 ready
- [ ] 文本聊天可用（LangBot + NapCat 扫码后）
- [ ] 记忆读写正常（便携 PG + bge-m3）
- [ ] 生图/语音：模型目录指定后可用（不要求随包）
- [ ] 旧机器迁移：PG 三库 + LangBot 数据 + 模型目录 → 历史与角色卡保留
- [ ] 整目录移动（D:→E:）后仍可启动
- [ ] 卸载/删除干净，无注册表残留（如用安装器）

---

## 5. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| venv 不可重定位 | 环境全废 | 统一 `uv venv --relocatable`；打包后换路径冒烟 |
| 插件依赖在线安装（LangBot marketplace） | 新机插件缺失 | 打包插件目录 + 离线依赖缓存 |
| NapCat/QQ 版本绑定、登录风控 | 换机首登失败 | 向导引导扫码；固定 QQ/NapCat 版本 |
| CUDA/驱动版本 | GPU 服务失败 | 固定 torch 版本；文档要求驱动 ≥ 版本 |
| 模型体积/迁移耗时 | 体验差 | 迁移向导 + 外接盘支持 + 可选下载 |
| 许可合规（AGPL/GPL/Civitai） | 不能公开分发 | 仅自用；分发包需评估 |
| 代码签名/SmartScreen | 安全提示 | 自用可忽略；分发需签名 |

---

## 6. 立即可做的前置事项

1. **依赖审计**：统计 Core/Gateway/记忆实际 import 的包，产出三份精简清单（体积可从 4.6GB 降到 ~2GB）
2. **路径改造**：先改 `config.json`、`core/services.py`、launcher、PG 启停脚本（收益最大）
3. **模型根约定**：定 `models\` 子目录结构（comfyui/gsv/embed），写生成脚本
4. **迁移脚本原型**：robocopy 旧数据 + pg_dump/restore 的 PowerShell 脚本

---

## 7. 待决策

| 问题 | 选项 |
|---|---|
| 模型如何处理 | A 迁移向导（推荐）/ B 随包（+40GB）/ C 下载器 |
| 目标用户 | 仅自己换机（L3-A 足够）/ 给他人用（需 L3-B + 许可评估） |
| 网络前提 | 允许首次联网下载（简化）/ 完全离线 |
| 是否保留 Docker 分支 | 保留（已实现，增加维护面） / L3 后移除 |
