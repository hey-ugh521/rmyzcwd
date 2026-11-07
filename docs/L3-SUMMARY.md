# Möbius L3 完成总结

> 日期：2026-11-08
> 目标：把 Möbius 从"本机可运行"推进到"可分发、可迁移、可扩展"。

---

## 1. 完成了什么

| 阶段 | 交付 | 证据 |
|---|---|---|
| **P1 路径抽象** | `core/paths.py`（`${VAR}` 契约）、配置变量化、示例配置 | 应用目录复制/改名后全栈启动 |
| **P2 运行时组件化** | 3 套 relocatable venv：core 26MB / gateway 56MB / memory 770MB；组件包规范 | 938MB runtime 复制到新路径全栈启动 |
| **P2b 全运行时组件** | LangBot（637MB venv）、Kokoro（904MB，中文 TTS 验证）、ComfyUI（3.96GB，排除模型）、GSV（7.93GB，排除角色权重） | 8+2 组件包，10 组件 12.45GB |
| **P3 下载器** | `ops/l3/downloader.py`：多源/续传/SHA256/7z/超长路径安全/安装修复卸载更新/本地覆盖 | file:// 全链路 7 类测试通过 |
| **P4 首启向导** | 4 步 GUI（组件/LLM/模型目录/QQ）+ CLI；首次运行自动进入 | LLM 真实连通、模型迁移、GUI 启动 |
| **P5 许可合规** | 305 包清单、351 份许可文本、组件矩阵、分发检查清单、签名脚本 | `docs/L3-LICENSES.md`、`ops/l3/licenses/` |
| **P5b 扩展接口** | 模型/角色卡/工作流/本地组件覆盖验收 + `GET /api/workflows` | 9/9 通过 |
| **P6 安装器+发布** | Inno 0.2.0（向导/下载器/许可）；`build_release.ps1`（哈希/清单/签名/上传命令） | 安装/升级/卸载/发布演练全过 |

---

## 2. 目录与产物

```
mobius/
├─ launcher/dist/Mobius.exe / MobiusCLI.exe      # 启动器 + 向导
├─ installer/dist/MobiusSetup-0.2.0.exe          # 27.2MB 安装包
├─ ops/l3/
│  ├─ downloader.py                              # 组件下载器
│  ├─ build_component.ps1 / build_release.ps1    # 打包与发布
│  ├─ packs/fragments.json                       # 10 组件 manifest
│  ├─ licenses/                                  # 声明 + 351 份文本
│  ├─ acceptance_extensions.py                   # 扩展验收
│  └─ P1..P6 阶段文档
├─ release/0.2.0/                                # 发布演练产物
└─ D:\AI\components\                             # 10 个组件包（12.45GB，不入库）
```

安装：`MobiusSetup-0.2.0.exe` → 开始菜单 → 首次向导（组件/LLM/模型/QQ）。
组件：`MobiusCLI --headless install-components` 或向导第 1 步。

---

## 3. 已验证的 L3 目标

- ✅ 应用目录可复制/改名/换盘启动（P1）
- ✅ 运行时整体可复制（venv relocatable，P2）
- ✅ 组件化下载/校验/安装/修复/卸载（P3）
- ✅ 首次运行向导：LLM 配置 + 模型迁移 + QQ 登录（P4）
- ✅ 许可与第三方声明齐备（P5）
- ✅ 扩展放入即生效：模型/角色/工作流/组件（P5b）
- ✅ 安装器安装/升级/卸载 + 发布流水线（P6）

---

## 4. 尚未完成（需要外部条件）

| 项 | 阻塞条件 |
|---|---|
| ~~组件真实托管~~ | ✅ 已上传 GitHub Release `l3-20261108`（私有仓库，需 token） |
| 正式代码签名 | 自签已跑通；对外分发需 OV/EV 证书 |
| 真实 HTTP 断点续传实测 | 需组件公网 URL |
| QQ 官方安装引导 | 分发时需做成安装器步骤（当前文档说明） |
| 商业分发 | NapCat 许可限制为**非商业** |

---

## 5. 下一步建议

1. **自用换机演练**：找一台干净 Windows，用 `release/0.2.0` + 组件包（U 盘/局域网）跑一遍向导
2. **托管**：优先阿里云 OSS（学生权益），把 `fragments.json` 的 urls 指向真实地址
3. **签名**：先自签跑通，再购 OV 证书
4. **可分发版本**：拆分 QQ、移除受限模型、补许可页（P6 已有清单）
