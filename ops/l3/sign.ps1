# L3 代码签名脚本（Windows Authenticode）
# 用法：
#   自签（内部测试）：
#     .\ops\l3\sign.ps1 -SelfSigned -Path launcher\dist\Mobius.exe,launcher\dist\MobiusCLI.exe
#   正式证书（OV/EV）：
#     .\ops\l3\sign.ps1 -Thumbprint <CERT_THUMBPRINT> -Path <files...>
param(
    [Parameter(Mandatory = $true)][string[]]$Path,
    [string]$Thumbprint = '',
    [switch]$SelfSigned,
    [string]$TimestampUrl = 'http://timestamp.digicert.com',
    [string]$Subject = 'CN=Mobius Dev'
)

$ErrorActionPreference = 'Stop'

function Find-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
    )
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $hit = Get-ChildItem $root -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

$signtool = Find-SignTool
if (-not $signtool) {
    Write-Warning "未找到 signtool.exe（安装 Windows SDK 后可用）。以下为待执行命令："
    Write-Host "signtool sign /fd SHA256 /tr $TimestampUrl /td SHA256 /sha1 <thumbprint> <files...>"
    exit 2
}

if ($SelfSigned -and -not $Thumbprint) {
    Write-Host "创建自签名代码签名证书：$Subject"
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject $Subject `
        -CertStoreLocation Cert:\CurrentUser\My -NotAfter (Get-Date).AddYears(2)
    $Thumbprint = $cert.Thumbprint
    Write-Host "证书指纹: $Thumbprint（仅本机可信；对外分发需 OV/EV 证书）"
}

if (-not $Thumbprint) {
    throw "需要 -Thumbprint 或 -SelfSigned"
}

$files = @()
foreach ($p in $Path) {
    $resolved = Resolve-Path $p -ErrorAction SilentlyContinue
    if (-not $resolved) { throw "文件不存在: $p" }
    $files += $resolved.Path
}

foreach ($file in $files) {
    Write-Host "签名: $file"
    & $signtool sign /fd SHA256 /tr $TimestampUrl /td SHA256 /sha1 $Thumbprint $file
    if ($LASTEXITCODE -ne 0) { throw "签名失败: $file" }
    & $signtool verify /pa /v $file
    if ($LASTEXITCODE -ne 0) { throw "验签失败: $file" }
}
Write-Host "完成：$($files.Count) 个文件已签名并验证。"
