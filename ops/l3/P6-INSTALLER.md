# P6 安装器集成与发布（L3）

> 状态：2026-11-08 安装器 0.2.0 集成完成（launcher 向导 + 组件下载器 + 许可声明）；组件托管/发布脚本待补。

## 1. 0.2.0 安装包内容

| 内容 | 说明 |
|---|---|
| `Mobius.exe` / `MobiusCLI.exe` | 新版（含 P4 首启向导、配置兜底） |
| `core/`、`media_gateway/`、`ops/`、`scripts/`、`data/characters/` | 应用代码 |
| `config.example.json` | 示例配置（**不含密钥**；首次运行自动复制为 config.json） |
| `ops/l3/downloader.py` + `ops/l3/packs/*.json` | 组件下载器与 manifest（10 组件，12.45 GB） |
| `licenses/THIRD-PARTY.md` + `licenses.json` + `licenses/texts/` | 305 包声明 + 351 份许可全文 |
| `docs/L3-LICENSES.md`、`L3-COMPONENTS.md`、`L3-PLAN.md` | L3 文档 |
| `preflight.ps1` | 环境检查 |
| 开始菜单 | 启动器 / 控制台 / 环境检查 / **许可与第三方声明** / 卸载 |

安装包：**27.2 MB**，SHA256 `09f24dd9e1c47741312edd2e61588e026f320c034a72a9a1e227c71b5d0226f6`

## 2. 验收证据

| 测试 | 结果 |
|---|---|
| 静默安装（D:\AI\Mobius-P6） | ✅ exit 0，全部文件到位（含 351 份许可文本） |
| 已安装副本状态 | ✅ MobiusCLI 显示 10 个服务（连本机运行中的栈） |
| 开始菜单 | ✅ 5 项（含许可声明） |
| 安装后向导 | ✅ 已安装 `Mobius.exe --wizard` 进程存活 |
| 卸载 | ✅ 目录/快捷方式/注册表清空（残留 0） |
| 升级安装（0.1.0 → 0.2.0） | ✅ 覆盖安装成功，注册表显示 0.2.0 |

## 3. 分发前必须处理

| 项 | 说明 |
|---|---|
| `config.json` | 安装包只带 `config.example.json`；本机功能测试是手动复制配置，**不得**把真实配置（含 API Key/PG 口令）打进分发包 |
| QQ | 不在包内；`napcat-code` 组件已拆分（20.1 MB，排除 QQ 与 node_modules） |
| 受限模型 | 不随包（FLUX.3/F5/LoRA/社区权重），只迁移或链接原始来源 |
| 组件托管 | 10 个组件包（12.45 GB）需上传 GitHub Releases/OSS，并把 `fragments.json` 的 urls 换成真实地址 |
| 签名 | 用 `ops/l3/sign.ps1` + OV/EV 证书签名 `Mobius.exe`、`MobiusCLI.exe`、`MobiusSetup-*.exe` |
| NapCat 许可 | 附自定义许可全文（已含）；仅非商业分发 |

## 4. 发布流程（待脚本化）

```
1. launcher/build.ps1            # 双 exe
2. ops/l3/build_component.ps1 ×N # 组件包 + fragments
3. 上传组件到 Releases/OSS，更新 manifest urls
4. installer/build.ps1           # 安装包
5. ops/l3/sign.ps1               # 签名 + 验签
6. 生成 release.json（版本/哈希/组件清单）并归档
```

## 5. 下一步

- 发布脚本 `ops/l3/build_release.ps1`（打包→哈希→清单→可选上传）
- 组件真实托管与 HTTP 续传实测
- P5b 扩展接口验收（自定义 MCP/角色/工作流）
