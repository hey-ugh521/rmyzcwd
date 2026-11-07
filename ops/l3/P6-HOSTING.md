# P6 组件托管：GitHub Releases（L3）

> 状态：2026-11-08 完成。组件已上传到 GitHub Release，下载器可真实 HTTP 下载并续传。
> 仓库：`hey-ugh521/mobius`（**Private**）
> Release：`l3-20261108`（23 个资产，约 12.45 GB）

## 1. 资产清单

| 类型 | 资产 |
|---|---|
| 单包（8） | `python-core/gateway/memory/langbot/kokoro-*.7z`、`langbot-20261108.7z`、`postgres-18.6-20261108.7z`、`napcat-code-4.18.42-20261108.7z` |
| 分卷（8） | `comfyui-runtime-20261108.7z.001~003`（3.87GB）、`gsv-runtime-20261108.7z.001~005`（7.74GB） |
| 发布物（7） | `MobiusSetup-0.2.0.exe`、`fragments.json`、`release.json`、`SHA256SUMS.txt`、`THIRD-PARTY.md`、`licenses.json`、`LICENSE` |

## 2. 关键设计

### 2.1 分卷（GitHub 单文件 ≤2GB）

- `ops/l3/split_component.py`：把大包切成 1900MB 分卷，逐卷计算 SHA256，片段写入 `parts: [{index,name,url,size,sha256}]`
- 下载器多卷：逐卷下载/校验（每卷可续传）→ 拼接 → 整体 SHA256 校验 → 解压
- 实测：本地分卷安装 PASS；缺卷重下 PASS

### 2.2 私有仓库与鉴权

仓库是 Private，Release 资产需 token。manifest 使用 **GitHub API 资产 URL**：

```
https://api.github.com/repos/hey-ugh521/mobius/releases/assets/<id>
```

下载器行为：

| 条件 | 行为 |
|---|---|
| URL 含 `api.github.com` | 加 `Accept: application/octet-stream` |
| 环境变量 `MOBIUS_GH_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN` | 加 `Authorization: Bearer <token>` |

```powershell
$env:MOBIUS_GH_TOKEN = gh auth token
python ops/l3/downloader.py --manifest ops/l3/packs/fragments.json --root D:\Mobius install --required
```

### 2.3 流转顺序（避免 URL 被覆盖）

```
上传组件资产
→ finalize_release.py（资产 id → API URL 写回 fragments.json）
→ build_release.ps1（保留已 finalize 的 parts/urls，重建并自签安装器）
→ 上传发布资产（安装器/fragments/release.json/SHA256SUMS/许可）
```

## 3. 验收证据

| 测试 | 结果 |
|---|---|
| 上传 | ✅ 16 组件资产 + 7 发布资产 = 23 |
| 真实 HTTP 下载（python-core） | ✅ 带 token 从 API URL 下载、SHA 校验、安装成功 |
| 断点续传 | ✅ 预置 2MB `.part` → 从 2MB 续传，SHA 一致 |
| 多卷安装（本地分卷） | ✅ 拼接+SHA+解压 |
| 发布清单 | ✅ `finalize_release.py` 无缺失资产；manifest 指向 API URL |

## 4. 常用命令

```powershell
# 上传组件（示例）
gh release upload l3-20261108 --repo hey-ugh521/mobius D:\AI\components\*.7z D:\AI\components\*.7z.00? --clobber

# 资产 id 写回 manifest
python ops\l3\finalize_release.py --repo hey-ugh521/mobius --tag l3-20261108 --version 0.2.0

# 重建签名安装器并上传发布资产
.\ops\l3\build_release.ps1 -Version 0.2.0 -SkipComponents -SelfSign
gh release upload l3-20261108 --repo hey-ugh521/mobius release\0.2.0\* --clobber
```

## 5. 对外分享的两个选择

| 方式 | 做法 | 注意 |
|---|---|---|
| 保持 Private | 给使用者 `MOBIUS_GH_TOKEN`（或做成安装器/向导输入） | 每台机器需 token；适合熟人/自用 |
| 改为 Public | `gh repo edit --visibility public` 后，可把 manifest 的 API URL 换成 `browser_download_url`（下载器无需 token） | 公开前核对许可（NapCat 非商业、无 QQ/受限模型）、历史无敏感信息 |

## 6. 待办

- [ ] 向导/安装器集成 token 输入（Private 分发时）
- [ ] 公开分发时切换 browser URL 并复核许可
- [ ] Release 保留策略（旧组件清理，避免无限增长）
