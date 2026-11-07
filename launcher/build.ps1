# 构建 Möbius 启动器 exe
# 用法：powershell -ExecutionPolicy Bypass -File launcher\build.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = 'D:\AI\mobius-launcher\Scripts\python.exe'

if (-not (Test-Path $py)) {
    Write-Error "找不到启动器打包环境：$py（先运行 uv venv D:\AI\mobius-launcher）"
}

Set-Location $root
foreach ($mode in @(@("--windowed", "Mobius"), @("--console", "MobiusCLI"))) {
    & $py -m PyInstaller --noconfirm --onefile $mode[0] `
        --name $mode[1] `
        --icon "$root\launcher\mobius.ico" `
        --distpath "$root\launcher\dist" `
        --workpath "$root\launcher\build" `
        --specpath "$root\launcher" `
        "$root\launcher\mobius_launcher.py"
}

if (Test-Path "launcher\dist\Mobius.exe") {
    $size = [math]::Round((Get-Item "launcher\dist\Mobius.exe").Length / 1MB, 1)
    Write-Output "构建成功：launcher\dist\Mobius.exe ($size MB)"
} else {
    Write-Error "构建失败，见 launcher\build\Mobius\warn-Mobius.txt"
}
