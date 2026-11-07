# L3 代码签名脚本（Windows Authenticode，无需 signtool）
# 自签并信任（测试）：
#   .\ops\l3\sign.ps1 -SelfSigned -Trust -Path launcher\dist\Mobius.exe,launcher\dist\MobiusCLI.exe
# 正式证书（OV/EV，证书装到 CurrentUser\My）：
#   .\ops\l3\sign.ps1 -Thumbprint <CERT_THUMBPRINT> -Path <files...>
param(
    [Parameter(Mandatory = $true)][string[]]$Path,
    [string]$Thumbprint = '',
    [switch]$SelfSigned,
    [switch]$Trust,
    [string]$TimestampUrl = 'http://timestamp.digicert.com',
    [string]$Subject = 'CN=Mobius Dev'
)

$ErrorActionPreference = 'Stop'
# 兼容 -File 传参：支持逗号/分号分隔的单字符串
$Path = @($Path | ForEach-Object { $_ -split '[,;]' } | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
if ($Thumbprint -and ($Thumbprint -match '[\\/]')) {
    # -File 模式下多余参数被绑到 Thumbprint，这里纠正
    $Path += $Thumbprint
    $Thumbprint = ''
}

function Find-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

# 1) 准备证书
$cert = $null
if ($SelfSigned -and -not $Thumbprint) {
    $existing = Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.Subject -eq $Subject -and $_.NotAfter -gt (Get-Date).AddDays(30) -and $_.HasPrivateKey } |
        Sort-Object NotAfter -Descending | Select-Object -First 1
    if ($existing) {
        Write-Host "复用已有自签名证书：$Subject"
        $cert = $existing
    } else {
        Write-Host "创建自签名代码签名证书：$Subject"
        $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $Subject `
            -KeyUsage DigitalSignature -CertStoreLocation Cert:\CurrentUser\My `
            -NotAfter (Get-Date).AddYears(2)
    }
    $Thumbprint = $cert.Thumbprint
    Write-Host "证书指纹: $Thumbprint"

    if ($Trust) {
        foreach ($storeName in @('Root', 'TrustedPublisher')) {
            try {
                $store = New-Object System.Security.Cryptography.X509Certificates.X509Store($storeName, 'CurrentUser')
                $store.Open('ReadWrite')
                $store.Add($cert)
                $store.Close()
                Write-Host "已加入 CurrentUser\$storeName 信任"
            } catch {
                Write-Warning "加入 $storeName 失败（可稍后手动信任）：$_"
            }
        }
    }
}
elseif ($Thumbprint) {
    $cert = Get-ChildItem Cert:\CurrentUser\My | Where-Object { $_.Thumbprint -eq $Thumbprint } | Select-Object -First 1
    if (-not $cert) {
        $cert = Get-ChildItem Cert:\LocalMachine\My | Where-Object { $_.Thumbprint -eq $Thumbprint } | Select-Object -First 1
    }
    if (-not $cert) { throw "找不到证书: $Thumbprint" }
}
else {
    throw "需要 -Thumbprint 或 -SelfSigned"
}

# 2) 签名
$signtool = Find-SignTool
$files = @()
foreach ($p in $Path) {
    $resolved = Resolve-Path $p -ErrorAction SilentlyContinue
    if (-not $resolved) { throw "文件不存在: $p" }
    $files += $resolved.Path
}

foreach ($file in $files) {
    Write-Host "签名: $file"
    if ($signtool) {
        & $signtool sign /fd SHA256 /tr $TimestampUrl /td SHA256 /sha1 $Thumbprint $file
        if ($LASTEXITCODE -ne 0) { throw "签名失败: $file" }
    } else {
        try {
            $sig = Set-AuthenticodeSignature -FilePath $file -Certificate $cert `
                -HashAlgorithm SHA256 -TimestampServer $TimestampUrl
        } catch {
            Write-Warning "时间戳失败，改为不带时间戳签名: $_"
            $sig = Set-AuthenticodeSignature -FilePath $file -Certificate $cert -HashAlgorithm SHA256
        }
        if ($sig.Status -eq 'UnknownError' -and $sig.StatusMessage -match 'root') {
            Write-Warning "证书链不受信任（自签未信任时正常）：$file"
        }
        if (-not $sig.SignerCertificate) { throw "签名失败: $file" }
    }
}

# 3) 验签
Write-Host "`n验证签名："
$bad = 0
foreach ($file in $files) {
    $sig = Get-AuthenticodeSignature $file
    $ok = $sig.SignerCertificate -and ($sig.Status -eq 'Valid' -or $sig.Status -eq 'UnknownError')
    if (-not $ok) { $bad++ }
    $status = if ($sig.SignerCertificate) { "$($sig.Status)" } else { "无签名" }
    Write-Host ("  {0}  {1}  {2}" -f ($(if ($ok) { 'OK ' } else { 'BAD' }), (Split-Path $file -Leaf), $status))
    if ($sig.SignerCertificate) {
        Write-Host ("       指纹: {0}  时间戳: {1}" -f $sig.SignerCertificate.Thumbprint, $(if ($sig.TimeStamperCertificate) { '有' } else { '无' }))
    }
}
if ($bad -gt 0) { throw "$bad 个文件验签失败" }
Write-Host "`n完成：$($files.Count) 个文件已签名并验证。"
