# Möbius L3 规划：可分发在线安装器（修订版）

> 目标：让一台全新的 Windows 机器通过安装器完成部署；运行时组件首次联网下载，模型按需下载/迁移。
> 决策（2026-11-08）：
> 1. **模型**：下载器（不随包）
> 2. **用途**：可对外分发
> 3. **网络**：允许首次联网
>
> 现状基线：L2 已完成（去 Docker、Core 统管、便携 PG、启动器、离线安装包 v1）。

---

## 0. 体积盘点（实测）

| 组件 | 体积（实测/审计） | 交付方式 |
|---|---|---|
| Core Python 环境（当前 open-webui 全量） | 3.44 GB → **0.09 GB 基础 / 1.23 GB 含 Kokoro** | 组件下载（必需） |
| Gateway Python 环境 | 0.09 GB → **0.13 GB** | 组件下载（必需） |
| 记忆 Python 环境 | 1.07 GB → **1.08 GB**（torch 瓶颈） | 组件下载（记忆可选） |
| LangBot 源码 + venv | 1.14 GB | 组件下载（必需） |
| 便携 PostgreSQL | 1.09 GB | 组件下载（必需） |
| 原生 NapCat（含 QQ） | 1.50 GB | 组件下载（必需） |
| ComfyUI 运行时（不含模型） | 6.98 GB | 组件下载（可选） |
| GPT-SoVITS 运行时（不含权重，估） | ~7 GB | 组件下载（可选） |
| **模型合计（实测审计）** | **≈127.6 GiB** | 模型下载（可选，见下） |
| ├ 嵌入 bge-m3（文本记忆必需） | 2.14 GiB | 模型下载 |
| ├ 生图 | 104.54 GiB（ComfyUI 物理 79.27 = D 盘 19.31 + **E 盘 junction 59.97**；另 `D:\AI\ai-models` 25.27） | 模型下载/迁移 |
| ├ 语音 | 20.96 GiB（GSV 13.19 + 社区 0.53 + TTS/ASR/评测 7.25） | 模型下载/迁移 |
| **安装器本体** | **< 100 MB** | 随包 |
| **最小可跑通集** | **≈15.6 GiB**（组件 + bge-m3） | 首次下载 |
| **默认能力档** | **≈25.7 GiB**（+Illustrious/qwen/角色 LoRA/Rosmontis/kokoro） | 首次下载 |

> 修正（来自子智能体审计）：
> 1. **ComfyUI 模型不是 19.31GB**：15 个子目录是指向 `E:\comfy-models` 的 junction，物理合计 **79.27GB**；迁移/下载器必须先解引用（`robocopy /XJ` 或按目标拷贝），不能复制 junction 本身。
> 2. **`D:\AI\ai-models`（25.27GB，Qwen-Image 2.1 / FLUX3 Klein）是默认画笔底座**，不在 ComfyUI 目录内；L3 需纳入模型清单（建议作为「生图底座包」可选组件，仅从原始来源下载/迁移，不镜像分发）。
> 3. **不可再分发**（只能迁移）：13 个 Hi3 角色 LoRA、社区 Rosmontis/Theresa 权重、自训 GSV 73 个 epoch、FLUX3（FLUX Kommunity 非商用）、F5-TTS（CC-BY-NC）、MiniMax H3（许可待确认）。
> 4. Python 环境合计 **4.59GB → 2.43GB（-47%）**；极简核心（不含 Kokoro）Python 环境仅 **0.22GB**。
> 5. **三套 venv 基底不可分发**（Gateway 指向 DSH 自带 Python、Memory 指向 open-webui conda）→ P2 必须 `uv venv --relocatable` 重建；4 份 requirements 与重建命令见 `ops/l3/dep-audit.md` §4.3/§4.4。
> 6. Kokoro 中文 G2P 需显式 `misaki[zh]`（pypinyin-dict 107.6MB），建议 Kokoro 作为可选组件。

结论：安装器本体很小；体积转移到「组件 + 模型下载器」。核心难点是**组件托管、校验、断点续传、模型许可与 junction 处理**。

---

## 1. 目标形态

### L3 交付物

1. `MobiusSetup.exe`（<100MB）：安装器本体（launcher + 组件管理器 + 首启向导）
2. **组件清单**：`manifest.json`（版本、URL、SHA256、大小、依赖关系）
3. **组件包**：runtime 分卷（每卷 ≤2GB），托管在发布渠道
4. **模型清单**：来源以官方/原始发布页为准（Civitai/HuggingFace），下载器负责校验与落盘
5. **文档**：安装、迁移、许可以及第三方声明

### 安装流程

```
运行安装器（<100MB）
  → 选择安装目录（默认 %LOCALAPPDATA%\Mobius 或用户指定）
  → 选择要装的组件（必需：Core/Gateway/记忆/LangBot/PG/NapCat；
                     可选：ComfyUI/GSV）
  → 下载 + SHA256 校验 + 解压（断点续传、失败重试、镜像切换）
  → 首启向导：模型来源（下载/迁移/跳过）、QQ 扫码登录、自检
  → 启动
```

---

## 2. 关键设计

### 2.1 根目录契约

```
MobiusRoot\
├─ app\            # Core / Gateway / ops / config.json / data
├─ runtime\        # Python 环境、uv、LangBot、PostgreSQL、NapCat
├─ components\     # 组件清单与下载缓存
├─ data\           # PG 数据、日志、上传
├─ models\         # 模型根（可指向外部磁盘）
├─ launcher\       # Mobius.exe / MobiusCLI.exe / setup.exe
└─ versions.json   # 已装组件与版本
```

所有配置仅允许 `%MOBIUS_ROOT%` 相对路径；启动器注入该环境变量。

### 2.2 组件与下载器

组件清单（示例）：

```json
{
  "version": 1,
  "components": {
    "python-core": {
      "version": "3.12-20261108",
      "urls": ["https://cdn.example.com/mobius/python-core.7z.001", "..."],
      "sha256": "...",
      "size": 966367641,
      "required": true,
      "install": { "type": "archive", "target": "runtime/python-core" }
    },
    "comfyui": { "...": "可选组件" },
    "model-bge-m3": { "type": "model", "source": "huggingface", "size": 2791728742 }
  }
}
```

下载器要求：
- 分卷+断点续传、SHA256 校验、失败自动重试/换源
- 安装/修复/卸载单组件
- 离线缓存：已下载卷可复用（拷贝 `components\cache` 即可离线装）
- 速度/来源可配置（国内镜像）

托管建议：
- GitHub Releases（组件包分卷，≤2GB/卷）
- 国内加速：对象存储（阿里云 OSS / 腾讯 COS）或 ghproxy 镜像
- 模型：优先原始发布页（HuggingFace/Civitai），提供镜像与许可说明

### 2.3 运行时离线化（仍然是前提）

| 环境 | 方案 |
|---|---|
| Core | 依赖审计 → 精简环境（目标 <1GB），`uv venv --relocatable` |
| Gateway/记忆 | 同样 relocatable venv |
| LangBot | 保留 uv 项目 + venv，或首启时 `uv sync`（需联网） |
| ComfyUI/GSV | 便携 Python 目录直接打包（排除模型） |

### 2.4 模型策略

- 不随包；下载器按清单下载并校验
- 支持三类来源：**迁移**（从旧目录复制）、**下载**（清单 URL）、**指定路径**（外接盘）
- 按用途分组：文本（必需 bge-m3）、生图（ComfyUI 模型）、语音（GSV 权重）
- **许可检查**：每个模型条目带 license 字段；下载前展示，禁止再分发受限模型

### 2.5 首次向导

1. 组件安装状态检测 → 缺失项一键安装
2. 模型根选择（默认 `MobiusRoot\models`，支持外接盘）
3. 模型下载/迁移（可跳过生图/语音）
4. QQ 登录（启动 NapCat，引导扫码）
5. 自检（服务 ready + 测试消息）

---

## 3. 阶段与验收

| 阶段 | 内容 | 工期 | 验收 |
|---|---|---|---|
| **P1 路径抽象（完成）** | `MOBIUS_ROOT` 契约；`core/paths.py`；配置变量化；示例配置 | 3–5 天 | ✅ 复制/改名目录后不改文件即可启动（见 `ops/l3/P1-PATHS.md`） |
| **P2 运行时组件化（完成）** | ✅ 三套 Python relocatable（26/56/770 MB）+ LangBot venv（637MB）+ Kokoro venv（904MB）；8 个组件包（合计 12.22GB，含 ComfyUI 3.96GB / GSV 7.93GB，已排除模型与角色权重） | 7–10 天 | ✅ 复制 runtime 可全栈启动；LangBot/Kokoro 已切换验证；见 `ops/l3/P2-RUNTIME.md`、`P2B-RUNTIME.md` |
| **P3 组件清单与下载器（完成）** | ✅ manifest v1 + `ops/l3/downloader.py`（多源/续传/校验/解压/安装/修复/卸载/更新/本地覆盖/依赖） | 5–7 天 | ✅ 本地 file:// 源全链路验收（5 组件安装、SHA 失败、超长路径、卸载）；见 `ops/l3/P3-DOWNLOADER.md` |
| **P4 首启向导（完成）** | ✅ 4 步向导（组件/LLM/模型目录/QQ）+ CLI（components/install-components/test-llm/configure-llm/migrate-models） | 5–7 天 | ✅ LLM 连通、模型迁移、GUI 启动验证；见 `ops/l3/P4-WIZARD.md` |
| P5 扩展接口与自定义组件 | MCP/插件/角色/画笔/工作流扩展契约；本地 manifest 覆盖；第三方组件信任提示 | 5–7 天 | 自定义扩展放入目录即生效，无需重装 |
| P6 可分发合规 | 第三方声明（AGPL/GPL/模型许可）、版本策略、签名方案、隐私说明 | 3–5 天 | 分发包通过许可自查清单 |
| P7 安装器与发布 | Inno 集成下载器/能力面板、卸载、发布脚本、校验 | 3–5 天 | 新机 30 分钟内可聊天（含下载） |

**合计约 6–8 周**（比自用版多「能力面板 / 扩展契约 / 合规签名」三块）。

---

## 4. 验收清单

- [ ] 全新 Windows（无 Python/Docker），运行安装器
- [ ] 组件下载/校验/解压成功（断网中断后可续传）
- [ ] 首启向导完成模型选择与 QQ 登录
- [ ] 文本聊天 + 记忆可用；生图/语音按选择的组件可用
- [ ] 单组件修复/卸载可用
- [ ] 安装目录移动后仍可启动
- [ ] 分发包包含第三方许可声明；受限模型不随包
- [ ] 卸载干净（组件、数据可选保留）
- [ ] **极简安装（不含本地模型）可完成一次真实文本对话**
- [ ] 任一可选能力可单独安装/卸载，装后无需重装主程序
- [ ] 自定义 MCP / 插件 / 角色卡 / 画笔 / 工作流放入目录即生效
- [ ] 自定义组件源（components.local.json）可覆盖或新增组件
- [ ] 下载缓存可导出，用于另一台机器离线复用

---

## 5. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| ComfyUI 模型 junction（E 盘 60GB） | 迁移漏拷 | 迁移脚本用 `robocopy /XJ` 解引用或按 manifest 逐文件拷贝 |
| 模型许可（Civitai/HF 不可再分发） | 侵权/违规 | 仅迁移或链接原始来源；下载前展示许可 |
| 22GB 组件下载体验 | 首装慢 | 分卷、断点续传、国内镜像、可选只装必需组件 |
| 分卷托管成本/带宽 | 分发成本高 | 对象存储 + CDN；或仅发布必需组件 |
| 模型许可（Civitai/HF） | 不能分发 | 只链接原始来源；下载前展示许可；不镜像受限模型 |
| LangBot AGPL / ComfyUI GPL | 分发义务 | 附源码/许可声明、提供获取方式 |
| Windows 签名 | SmartScreen 警告 | 自签或购买证书；文档说明 |
| QQ/NapCat 风控/版本绑定 | 首登失败 | 固定版本 + 向导说明 + 备用登录方式 |
| CUDA/驱动版本 | GPU 组件失败 | 组件清单声明驱动要求；首启检测 |

---

## 6. 立即可做（不依赖决策）

1. **依赖审计**：Core/Gateway/记忆实际 import 包 → 三份精简清单
2. **路径改造**：`config.json`、`core/services.py`、launcher、PG 脚本
3. **组件清单 schema** 定稿（v1）
4. **模型清单**：整理 bge-m3 / ComfyUI / GSV / 语音包来源、体积、SHA256、许可
5. **迁移脚本原型**：旧机 → 新机（PG dump + data + models）

---

## 7. 发布与托管建议

| 项 | 建议 |
|---|---|
| 组件托管 | GitHub Releases（分卷）+ 阿里云 OSS 镜像（已有学生权益） |
| 模型托管 | 原始来源优先；镜像仅限许可允许的模型 |
| 版本策略 | 组件版本独立演进；`manifest.json` 带最低兼容版本 |
| 签名 | 自用阶段可跳过；对外分发建议购买 OV 证书 |
| 更新 | 安装器/启动器检查 manifest 版本，提示增量更新 |

---

## 8. 下载器选项设计（细化）

### 8.1 安装模式

| 模式 | 包含 | 约体积 | 说明 |
|---|---|---|---|
| **极简核心** | launcher + Core + Gateway + LangBot + PostgreSQL + NapCat | ~4.8 GB | 文本聊天可用（LLM 在云端，无需本地模型） |
| 标准 | 极简 + 记忆组件 + bge-m3 嵌入模型 | ~8.5 GB | 开启长期记忆 |
| 全家桶 | 标准 + ComfyUI + GSV 运行时 | ~22 GB | 生图/语音能力，模型另下 |
| 自定义 | 逐组件勾选 | 按选择 | 高级用户 |

### 8.2 组件矩阵（清单驱动）

| 组件 | 必需 | 依赖 | 说明 |
|---|---|---|---|
| `launcher` | ✅ | — | 启动器与安装向导 |
| `core-runtime` | ✅ | — | Core 精简 Python 环境 |
| `gateway-runtime` | ✅ | — | 网关 Python 环境 |
| `langbot` | ✅ | — | LangBot 源码 + venv |
| `postgres` | ✅ | — | 便携 PG 二进制 + 模板数据 |
| `napcat` | ✅ | — | NapCat + QQ 便携包 |
| `memory` | 可选 | postgres | 记忆 MCP + 嵌入服务 |
| `embed-bge-m3` | 可选 | memory | 嵌入模型（约 2.6GB） |
| `comfyui-runtime` | 可选 | — | 生图运行时（约 7GB） |
| `comfyui-models` | 可选 | comfyui-runtime | 画笔底模/LoRA（19GB+） |
| `gsv-runtime` | 可选 | — | 语音运行时（约 7GB） |
| `gsv-weights` | 可选 | gsv-runtime | 角色语音权重（14GB+） |
| `powercontext` | 可选 | — | 上下文/记忆插件服务 |
| `plugins-pack` | 可选 | langbot | 内置插件离线包 |

### 8.3 下载选项（每一项都要可配）

| 选项 | 设计 |
|---|---|
| 来源 | 官方 GitHub / 国内镜像 / **自定义 URL**（单个组件可覆盖） |
| 并发 | 1–4 可调（默认 2） |
| 限速 | 可设上限，避免占满带宽 |
| 断点续传 | 分卷级续传；失败自动重试 N 次并换源 |
| 校验 | **SHA256 强制**；失败标记损坏并可重下 |
| 缓存 | 自定义缓存目录；支持把旧机器的缓存目录拷来复用（离线安装） |
| 模型 | 下载 / 迁移（robocopy 旧目录）/ 指定已有路径 / 跳过；**模型根可指向外接盘** |
| 许可 | 每个模型显示 license 与来源；受限模型需确认后才下载 |
| 更新 | 检查更新、增量、锁定版本、回滚 |
| 组件操作 | 安装 / 修复 / 卸载 / 查看日志 |

### 8.4 离线与低带宽

- 「仅下载必需组件」模式（~4.8GB）
- 缓存导出：`components\cache` 打包含校验，可拷给另一台机器离线复用
- 可暂停/继续；下载进度与错误原因可见

---

## 9. 基础核心：保证「安装即可跑通」

### 9.1 极简核心的验收路径

```
安装极简核心 → 首启向导配置 LLM（DeepSeek/OpenAI 兼容：base_url + api_key + model）
→ 连通性测试（一次真实对话）→ QQ 扫码登录 → 自检 → 可用
```

关键：**文本聊天不依赖本地模型**；LLM 走云端 API，向导负责配置与验证。

### 9.2 能力矩阵与降级

| 能力 | 依赖 | 未安装时的表现 |
|---|---|---|
| 文本聊天 | 极简核心 + LLM API | 安装向导必配 |
| 长期记忆 | memory + bge-m3 | 控制台「能力」页显示"未安装"，给出一键安装；bot 记忆工具返回明确提示 |
| 生图 | comfyui-runtime + models | 画笔列表为空 + 引导安装 |
| 语音 | gsv-runtime + weights | 语音工具不可用 + 引导安装 |
| 联网搜索 | plugins-pack | 工具缺失提示 |
| 扩展（MCP/插件） | 任意 | 扩展页可添加，无需重装 |

**原则：功能缺失要"可见、可解释、可一键补齐"，不能静默失败。**

---

## 10. 扩展接口契约（功能可达 + 自定义）

| 扩展点 | 位置/方式 | 用户可自定义 |
|---|---|---|
| MCP 服务器 | `config.json → mcp_servers` / 控制台新增 | HTTP/stdio、URL/命令、超时；本地脚本 MCP |
| LangBot 插件 | `runtime/langbot/data/plugins` + 离线 zip | 拖入目录 / 安装包，热启用 |
| 角色卡 | `app/data/characters/*.json` | 自定义角色、LoRA、语音、人设 |
| 人设提示词 | `app/config/prompts/` | 直接编辑，回滚基线 |
| 画笔/底模 | `models.json` + `workflows/*.json` | 增加新底模/LoRA/工作流，注册即用 |
| 内置能力服务 | `components.local.json` 覆盖 | 自定义组件源、私有组件包 |
| API | Core `/v1`（OpenAI 兼容）+ `/api` | 第三方应用接入 |
| 工具/Skill | 目录扩展（规划） | 自定义技能包 |

### 自定义组件的信任模型

- `components.local.json` 可覆盖/新增组件条目，但必须声明来源与 SHA256
- 非官方组件在向导中标记「第三方」，需用户确认
- 版本兼容：组件声明 `minLauncherVersion` / `apiVersion`

### 扩展点验收

- 放入一个自定义 MCP 服务（stdio 本地脚本）→ 控制台可见、工具可调用
- 放入自定义角色卡 → bot 可选中并生图/语音
- 放入自定义工作流 JSON → 生图验收脚本可发现
- 新增组件条目 → 下载器可安装，无需改主程序

