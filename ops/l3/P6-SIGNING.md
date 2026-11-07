# P6 自签名流水线（L3）

> 状态：2026-11-08 自签跑通并接入发布流水线。
> 证书：`CN=Mobius Dev`（CurrentUser\My，自签，2 年）
> 指纹：`FFC15D1B21B21831EE656A310D1C59F1C6748F06`

## 1. 结论

| 项 | 结果 |
|---|---|
| 签名方式 | PowerShell 原生 `Set-AuthenticodeSignature`（无需 Windows SDK/signtool；装了 signtool 会优先用） |
| 算法/时间戳 | SHA256 + `http://timestamp.digicert.com` |
| 信任 | 证书加入 `CurrentUser\Root` 与 `TrustedPublisher`（仅本机当前用户） |
| 验签 | 3 个文件 + 安装后内嵌 exe 全部 **Valid**，时间戳存在 |
| 发布一致性 | `release.json` 哈希在签名后计算，与实际文件一致 |
| 安装 | 已签名安装包静默安装/卸载通过 |

## 2. 流水线顺序（重要）

```
构建 launcher exe
  → 签 launcher exe（否则安装器内嵌的是未签名 exe）
  → 构建安装器
  → 签安装器
  → 计算哈希/生成 release.json
```

早期错误顺序导致"安装器 Valid 但安装后 Mobius.exe NotSigned"，已修复。

## 3. 使用

```powershell
# 单独签名（自签 + 信任 + 验签）
.\ops\l3\sign.ps1 -SelfSigned -Trust -Path `
  launcher\dist\Mobius.exe,launcher\dist\MobiusCLI.exe,installer\dist\MobiusSetup-0.2.0.exe

# 发布时自签（launcher 先签、安装器后签、哈希按签名后计算）
.\ops\l3\build_release.ps1 -Version 0.2.0 -SkipComponents -SelfSign

# 正式 OV/EV 证书（证书装到 CurrentUser\My 后）
.\ops\l3\build_release.ps1 -Version 0.2.0 -SkipComponents -SignThumbprint <THUMB>
```

## 4. 验收证据（2026-11-08）

| 文件 | 状态 | 指纹 | 时间戳 |
|---|---|---|---|
| `launcher/dist/Mobius.exe` | Valid | FFC15D1B… | 有 |
| `launcher/dist/MobiusCLI.exe` | Valid | FFC15D1B… | 有 |
| `installer/dist/MobiusSetup-0.2.0.exe` | Valid | FFC15D1B… | 有 |
| 安装后 `Mobius.exe` | Valid | FFC15D1B… | 有 |
| 安装后 `MobiusCLI.exe` | Valid | FFC15D1B… | 有 |

发布包 SHA256：`9017fcfb1a984151dac9ec499ce64dcb2d2b47823648771142a30b9238e175fb`（`release/0.2.0/`，签名后哈希）

## 5. 局限与对外分发

- **自签仅在当前用户/本机受信**：其他机器 SmartScreen 仍会警告，无法消除
- 对外分发需购买 **OV/EV 代码签名证书**（2023-06 起需硬件令牌/HSM）：
  1. 证书装到 `CurrentUser\My`
  2. `build_release.ps1 -SignThumbprint <指纹>`（流水线顺序不变）
  3. 附 `signtool`/`Get-AuthenticodeSignature` 验签证据到发布说明
- 证书到期前需续签；自签证书 2 年有效，仅测试用

## 6. 待办

- [ ] 购买 OV/EV 证书并替换自签
- [ ] 发布说明记录签名指纹与 `SHA256SUMS.txt`
- [ ] 组件包（.7z）不强制签名（无直接执行入口）
