# L3 许可合规与签名方案（P5）

> 目标：让 L3 分发包**可合法分发**，并给出 Windows 代码签名落地路径。
> 状态：2026-11-08 完成合规清单、第三方声明、签名脚本骨架；正式分发前需按清单复核。

---

## 1. 结论（先看这个）

| 约束 | 结论 |
|---|---|
| **NapCat** | 自定义许可：**仅限非商业**；允许再分发（须附全文+来源+版权）；修改版不得公开。 |
| **QQ 客户端** | 腾讯专有软件，**不得随包分发**；安装器只能引导用户从官方渠道安装。当前 `napcat` 组件包内含 QQ，**分发前必须拆分**。 |
| **LangBot** | AGPL-3.0：分发须提供 Corresponding Source（组件包含源码 ✓）与许可文本；作为网络服务提供也须提供源码。 |
| **ComfyUI** | GPL-3.0：随包分发须附源码与许可文本（组件包含源码 ✓）；修改版（custom_nodes）同样开源。 |
| **GPT-SoVITS** | MIT：保留版权与许可声明即可。 |
| **模型** | 混合许可：bge-m3(MIT)、Qwen-Image(Apache-2.0) 可分发；FLUX.3 Klein（非商用）、F5-TTS（CC-BY-NC）、Civitai LoRA、社区语音权重 **不可再分发**，只能迁移或链接原始来源。 |
| **Python 包** | 305 个包：MIT/BSD/Apache 为主；含 LGPL（psycopg、chardet、num2words、python-telegram-bot）与 GPL（html2text、phonemizer-fork）——附声明+许可文本，分发时提供对应源码获取方式。 |

**若分发给他人：整个组合按"非商业"使用（NapCat 限制）；不得打包 QQ、不得打包受限模型、不得商业售卖。**

---

## 2. 组件许可矩阵

| 组件 | 许可 | 可再分发 | 分发义务 |
|---|---|---|---|
| `python-core` / `python-gateway` / `python-memory` | MIT/BSD/Apache + LGPL(psycopg) | ✅ | 附 `THIRD-PARTY.md` + 许可文本；LGPL 动态链接声明 |
| `python-langbot` | 同 Python 依赖（含 GPL 的 html2text/phonemizer-fork？） | ✅ | 附声明与文本；GPL 包提供源码获取方式 |
| `python-kokoro` | Apache-2.0 + GPL 的 phonemizer-fork | ✅ | 附声明与文本；GPL 源码获取方式 |
| `langbot`（源码） | **AGPL-3.0** | ✅ | 附 `upstream-langbot-AGPL-3.0.txt`；提供完整源码（已随包）；网络服务亦须提供源码 |
| `comfyui-runtime` | **GPL-3.0** | ✅ | 附 `upstream-comfyui-GPL-3.0.txt`；提供完整源码（已随包）；修改版须开源 |
| `gsv-runtime` | MIT | ✅ | 附 `upstream-gpt-sovits-MIT.txt` |
| `napcat` | 自定义（非商业） | ⚠️ 仅非商业 | 附 `upstream-napcat-LICENSE.txt` + 来源 + 版权；不得商业使用；**不得包含 QQ** |
| QQ（NapCat 依赖） | 腾讯专有 | ❌ | 安装器引导官方安装；不随包 |
| PostgreSQL | PostgreSQL License | ✅ | 附许可文本（`texts/postgresql-*`） |
| pgvector | PostgreSQL License | ✅ | 附许可文本 |
| Kokoro 模型权重 | Apache-2.0 | ✅ | 署名 |
| bge-m3 | MIT | ✅ | 署名 |
| Qwen-Image 2.1 | Apache-2.0 | ✅ | 附许可 |
| FLUX.3 Klein | 非商用（FLUX 条款） | ❌ | 只迁移/链接原始来源 |
| F5-TTS | CC-BY-NC | ❌ | 只迁移/链接原始来源 |
| Illustrious / NoobAI 底模 | 待确认（各自发布页） | ⚠️ | 逐模型复核 |
| Civitai LoRA / 角色卡 | 逐模型条款 | ⚠️ | 逐模型复核；多数禁止再分发 |
| Rosmontis / Theresa 社区权重 | 待确认 | ⚠️ | 仅迁移，不镜像 |
| DeepSeek API | 服务条款 | n/a | 不打包、不转售 Key |
| Inno Setup | 免费使用（含商业） | ✅ | 附版权声明 |
| PyInstaller | GPL-2.0 + bootloader exception | ✅ | 打包应用不受 GPL 传染（官方例外）；附声明 |

---

## 3. 自动生成的第三方声明

| 产物 | 位置 | 说明 |
|---|---|---|
| 包清单 | `ops/l3/licenses/THIRD-PARTY.md` | 305 个包：名称/版本/许可/出现环境/主页 |
| 机器可读 | `ops/l3/licenses/licenses.json` | 同上（JSON） |
| 许可全文 | `ops/l3/licenses/texts/` | 350 份（Python 包导出 + 4 个上游 LICENSE） |

重新生成：

```powershell
$rt = "D:\AI\mobius-runtime"
foreach ($env in "python-core","python-gateway","python-memory","python-langbot","python-kokoro") {
  & "$rt\$env\Scripts\python.exe" ops\l3\licenses\collect_licenses.py --env $env --out "ops\l3\licenses\$env.jsonl"
  & "$rt\$env\Scripts\python.exe" ops\l3\licenses\collect_licenses.py --texts $env
}
python ops\l3\licenses\collect_licenses.py --merge
```

> 采集器内置 7 项人工确认覆盖（addict/dotenv/langbot-plugin/matrix-nio/socksio/tiktoken，以及 espeakng-loader 的"待确认"）。
> 分发前需复核：`html2text`（GPL-3.0）、`phonemizer-fork`（GPLv3+）、`espeakng-loader`（可能含 espeak-ng GPL 数据）。

---

## 4. 分发前检查清单

- [ ] 安装器/绿色包内包含 `THIRD-PARTY.md`、`licenses.json`、`licenses/texts/`
- [ ] LangBot 附 AGPL-3.0 全文 + 完整源码（含修改）
- [ ] ComfyUI 附 GPL-3.0 全文 + 完整源码（含 custom_nodes）
- [ ] GPT-SoVITS 附 MIT 声明
- [ ] NapCat 附自定义许可全文 + 来源 + 版权；**移除 QQ 客户端**
- [ ] 不打包 QQ；安装器只给官方下载链接/引导
- [ ] 不打包 FLUX.3 / F5-TTS / Civitai LoRA / 社区语音权重；仅迁移或链接原始来源
- [ ] 每个随包模型在 manifest 中带 `license` 与 `source`
- [ ] 提供 GPL/LGPL 包的源码获取方式（链接或随附）
- [ ] 声明"非商业使用（NapCat 条款）"
- [ ] 隐私说明：消息/记忆数据仅本地存储；LLM 调用会发送对话到所选服务商
- [ ] 版本与组件清单对应（manifest/versions.json 可追溯）

---

## 5. Windows 代码签名

### 5.1 方案对比

| 方案 | 成本 | 适用 | 说明 |
|---|---|---|---|
| 自签名证书 | 0 | 内部/测试 | 需手动信任；SmartScreen 仍警告 |
| OV 代码签名证书 | ~$200–400/年 | 对外分发 | 2023-06 起需硬件令牌/HSM 私钥 |
| EV 证书 | ~$300–600/年 | 对外分发、即时信誉 | 仍需硬件令牌 |
| Azure Trusted Signing | ~$10/月 | 企业（需 3 年经营历史） | 个人通常不可用 |

### 5.2 签名命令（签名脚本 `ops/l3/sign.ps1`）

```powershell
# 自签（测试）
.\ops\l3\sign.ps1 -SelfSigned -Path launcher\dist\Mobius.exe

# 用证书指纹签名（OV/EV）
.\ops\l3\sign.ps1 -Thumbprint <CERT_THUMBPRINT> -Path launcher\dist\Mobius.exe,installer\dist\MobiusSetup-0.1.0.exe
```

- 时间戳：`http://timestamp.digicert.com`
- 验证：`signtool verify /pa /v <file>`
- 签名后仍需对 **安装器 + 所有 exe/dll** 签名（含 launcher、MobiusCLI）；组件包内 DLL 不强制签（无 SmartScreen 直接执行入口），但安装器释放后首次运行可能触发警告。

### 5.3 发布建议

1. P6 阶段先用自签跑通打包/签名流水线
2. 对外分发前购买 OV/EV 证书，签名并提交 `signtool verify` 证据
3. 记录签名指纹与时间戳到发布说明

---

## 6. 待办

- [ ] 拆分 `napcat` 组件包：NapCat 代码（可分发）+ QQ（不随包）
- [ ] 移除受限模型的打包路径，改 manifest `byo/migration`
- [ ] 安装器内加「许可与第三方声明」页面/目录
- [ ] 正式分发前复核 espeakng-loader / Illustrious / 社区权重的许可
