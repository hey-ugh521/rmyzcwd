# P6 发布流水线与发布产物（L3）

> 状态：2026-11-08 完成 `ops/l3/build_release.ps1`，并用真实产物跑通全流程演练。
> 发布目录 `release/<version>/` 不入库（`.gitignore`）。

## 1. 流水线

```
ops/l3/build_release.ps1 -Version 0.2.0
  1) launcher/build.ps1             → Mobius.exe + MobiusCLI.exe
  2) 组件打包（10 个，已存在则跳过）  → D:\AI\components\*.7z + manifest 片段
  3) 汇总 ops/l3/packs/fragments.json（required 标记；-BaseUrl 重写 urls）
  4) installer/build.ps1            → MobiusSetup-<ver>.exe
  5) 收集产物 → release/<ver>/：
       MobiusSetup-<ver>.exe、fragments.json、release.json、SHA256SUMS.txt
       THIRD-PARTY.md、licenses.json、LICENSE
  6) 可选签名（-SignThumbprint）
  7) 生成 upload-commands.txt（gh release / ossutil）；-ExecuteUpload 才真正上传
```

常用参数：

| 参数 | 说明 |
|---|---|
| `-Version` | 版本号（写入安装器与 manifest） |
| `-SkipLauncher` / `-SkipComponents` / `-SkipInstaller` | 复用已有产物（快速演练/只更新一层） |
| `-BaseUrl` | 组件下载前缀，写回 manifest 的 `urls` |
| `-SignThumbprint` | 用指定证书签名（调 `ops/l3/sign.ps1`） |
| `-ExecuteUpload` | 调 `gh release` 真实上传（默认只生成命令） |
| `-Tag` | Release 标签（默认 `v<Version>`） |

## 2. 演练结果（2026-11-08）

| 步骤 | 结果 |
|---|---|
| 跳过构建，用已有产物 | ✅ `release/0.2.0/` 生成；release.json 含 10 组件哈希；SHA256SUMS 6 个文件 |
| 重建 launcher + installer（`-SkipComponents`） | ✅ 全流程通过；新安装包 SHA256 `beea43ee…6661de` |
| 组件哈希 | ✅ 10 个组件（12.45GB）逐一记录 |
| 上传命令 | ✅ `upload-commands.txt`（gh release / ossutil 模板） |
| 签名 | ✅ 自签跑通（见 `P6-SIGNING.md`）：launcher 先签→安装器后签→签名后哈希；内嵌 exe Valid |

## 3. 产物布局

```
release/0.2.0/
├─ MobiusSetup-0.2.0.exe
├─ fragments.json          # 下载器 manifest（含 urls）
├─ release.json            # 版本/安装器/组件 sha256
├─ SHA256SUMS.txt
├─ THIRD-PARTY.md
├─ licenses.json
├─ LICENSE
└─ upload-commands.txt
```

## 4. 待办（正式发布）

- [ ] 组件包（12.45GB）上传 GitHub Releases / OSS，并把真实 URL 写入 manifest（`-BaseUrl`）
- [ ] 购买 OV/EV 证书并用 `-SignThumbprint` 签名（当前自签可跑通流程）
- [ ] `release/<ver>/` 与组件包不提交仓库（已 gitignore）
- [ ] 发布说明：组件版本、SHA256、许可提示（非商业、QQ/受限模型不随包）
