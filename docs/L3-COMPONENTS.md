# L3 组件清单与打包规范 v1

> 目标：把运行时拆成「可下载、可校验、可单独安装/修复/卸载」的组件；下载器按 manifest 工作。
> 状态：2026-11-08 规范定稿 + 打样（Python 运行时三件）。

---

## 1. manifest 结构

```json
{
  "schema": 1,
  "generated_at": "2026-11-08T03:00:00+08:00",
  "launcher_min_version": "0.1.0",
  "api_version": 1,
  "components": {
    "python-core": {
      "id": "python-core",
      "name": "Core 运行时（Python）",
      "kind": "runtime",
      "version": "3.12-20261108",
      "required": true,
      "depends_on": [],
      "size": 27262976,
      "sha256": "…",
      "urls": [
        "https://github.com/<owner>/mobius-components/releases/download/components-20261108/python-core-3.12-20261108.7z",
        "https://oss.example.com/mobius/components/python-core-3.12-20261108.7z"
      ],
      "install": {"type": "archive", "format": "7z", "target": "runtime/python-core"},
      "license": {"type": "mixed", "note": "包含 FastAPI/uvicorn 等，见 THIRD-PARTY.md"}
    }
  }
}
```

### 字段约定

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✅ | 全局唯一，小写短横线 |
| `kind` | ✅ | `runtime` / `service` / `plugin` / `model` / `data` |
| `version` | ✅ | 组件独立版本；建议 `<base>-<yyyymmdd>` |
| `required` | ✅ | 极简核心必需 |
| `depends_on` | ✅ | 组件 id 数组，安装顺序拓扑排序 |
| `size` / `sha256` | ✅ | 压缩包大小与校验和（**强制校验**） |
| `urls` | ✅ | 多源顺序尝试；支持自定义镜像 |
| `install` | ✅ | `type`: archive（7z/zip）或 model（下载到目录）；`target` 相对 `MOBIUS_ROOT` |
| `license` | ✅ | `type` + 说明；受限模型需 `restricted: true` |
| `min_launcher_version` | 可选 | 低于则拒绝安装 |
| `api_version` | 可选 | 与 Core/扩展接口兼容性 |

### 本地覆盖

- `components.local.json`（用户目录）可覆盖/新增组件；下载器合并顺序：官方 manifest → 本地覆盖
- 第三方组件必须提供 `sha256` + `source`；安装前后需用户确认
- 组件可声明 `byo: true`（Bring Your Own：只校验路径，不下载）

---

## 2. 组件矩阵（v1）

| id | kind | 必需 | 依赖 | 体积（实测） | 说明 |
|---|---|---|---|---|---|
| `launcher` | runtime | ✅ | — | 23 MB | GUI + CLI（随安装器） |
| `python-core` | runtime | ✅ | — | **26 MB** | Core 运行环境（20 包） |
| `python-gateway` | runtime | ✅ | — | **56 MB** | 网关 + 记忆 MCP（35 包） |
| `python-memory` | runtime | ✅* | — | **770 MB** | 嵌入服务（torch CPU） |
| `langbot` | runtime | ✅ | — | 1.14 GB | 源码 + uv venv（P2 后 relocatable） |
| `postgres` | runtime | ✅ | — | 1.09 GB | 便携 PG + 模板数据 |
| `napcat` | runtime | ✅ | — | 1.50 GB | NapCat + QQ 便携包 |
| `powercontext` | service | 可选 | — | ~50 MB | 上下文服务 |
| `kokoro` | runtime | 可选 | python-core | ~1.2 GB | TTS（含 misaki[zh]） |
| `comfyui-runtime` | runtime | 可选 | — | 6.98 GB | 生图运行时 |
| `comfyui-models` | model | 可选 | comfyui-runtime | 物理 79.27 GB（含 E 盘 junction 解引用） | 画笔底模/LoRA |
| `gsv-runtime` | runtime | 可选 | — | ~7 GB | 语音运行时 |
| `gsv-weights` | model | 可选 | gsv-runtime | 20.96 GB | 角色语音权重 |
| `embed-model-bge-m3` | model | ✅* | python-memory | 2.14 GB | 文本记忆必需 |
| `image-base-ai-models` | model | 可选 | — | 25.27 GB | Qwen-Image 2.1 / FLUX3 Klein（默认画笔底座） |
| `plugins-pack` | plugin | 可选 | langbot | 待测 | 内置插件离线包 |

\* 若选择「无记忆」极简模式可不装 python-memory + embed-model。

---

## 3. 打包规范

### 3.1 目录布局（组件包内）

```
python-core-3.12-20261108.7z
└─ python-core/            # 解压到 MOBIUS_ROOT/runtime/python-core
   ├─ Scripts/
   ├─ Lib/
   └─ pyvenv.cfg           # uv --relocatable 生成
```

### 3.2 打包脚本

`ops/l3/build_component.ps1`：

```powershell
.\ops\l3\build_component.ps1 -Name python-core -SourceDir D:\AI\mobius-runtime\python-core `
  -Version 3.12-20261108 -OutDir D:\AI\components
# 产出：
#   D:\AI\components\python-core-3.12-20261108.7z
#   D:\AI\components\python-core-3.12-20261108.manifest.json（id/size/sha256/urls 占位）
```

- 压缩：7z `-mx=5`，单组件 ≤2GB（超出用 `-v2g` 分卷，manifest 用 `urls` 列表）
- 校验：SHA256 写入 manifest；下载器必须校验
- 文件名：`<id>-<version>.7z[*]`

### 3.3 安装/卸载/修复

| 操作 | 行为 |
|---|---|
| 安装 | 下载 → 校验 → 解压到 `target`（已存在则先备份到 `components\backup`）→ 写 `versions.json` |
| 修复 | 校验已装文件（按 manifest 的目录哈希或重下覆盖） |
| 卸载 | 删除 target + 从 `versions.json` 移除；依赖被引用时阻止 |
| 降级/回滚 | `versions.json` 保留上一版本；支持切换 |

### 3.4 缓存

- 下载缓存：`components\cache\<sha256前2位>\<sha256>.part`，支持断点续传
- 缓存导出：整目录可拷贝到另一台机器，下载器识别后跳过下载

---

## 4. 打样结果（2026-11-08）

| 组件 | 安装体积 | 7z 包体积 | SHA256 |
|---|---|---|---|
| `python-core` | 26 MB | 6.22 MB | `39e135d3…cffcbc` |
| `python-gateway` | 56 MB | 14.75 MB | `5b0427ea…d354ece` |
| `python-memory` | 770 MB | 141.14 MB | `4994565…a7a214b73` |

> 打样包位于 `D:\AI\components\`（不入库），fragment 记录在 `ops/l3/packs/fragments.json`。

---

## 5. 待办（P3 下载器）

- [ ] 下载器实现（分卷/续传/多源/修复）
- [ ] `components.local.json` 合并与第三方确认
- [ ] LangBot venv `--relocatable` 重建
- [ ] Kokoro 拆为可选组件（带 `misaki[zh]` 中文验收）
- [ ] ComfyUI/GSV 便携包（排除模型，模型走 model 组件）
- [ ] junction 解引用逻辑（comfyui-models）
- [ ] 组件清单托管（GitHub Releases + OSS 镜像）
