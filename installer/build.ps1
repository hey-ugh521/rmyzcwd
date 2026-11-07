# 构建 Mobius 安装包
# 用法：powershell -ExecutionPolicy Bypass -File installer\build.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$iscc = 'D:\AI\InnoSetup\ISCC.exe'

if (-not (Test-Path $iscc)) {
    Write-Error "找不到 Inno Setup：$iscc"
}
if (-not (Test-Path (Join-Path $root 'launcher\dist\Mobius.exe'))) {
    Write-Error "先构建启动器：launcher\build.ps1"
}

Push-Location $root
try {
    & $iscc 'installer\mobius.iss'
    $setup = Join-Path $root 'installer\dist\MobiusSetup-0.1.0.exe'
    if (Test-Path $setup) {
        $size = [math]::Round((Get-Item $setup).Length / 1MB, 1)
        Write-Output "构建成功：installer\dist\MobiusSetup-0.1.0.exe ($size MB)"
    } else {
        Write-Error "构建失败，检查 Inno Setup 输出"
    }
} finally {
    Pop-Location
}
