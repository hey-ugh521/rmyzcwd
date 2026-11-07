# Mobius 环境检查（安装后 / 首次运行）
# 只读检查，不修改任何东西。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File preflight.ps1
# 退出码：0 = 关键依赖齐备；1 = 有关键缺失

$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$coreOk = $false
$gwOk = $false

function Check($name, $path, $required) {
    if (Test-Path $path) {
        Write-Host ("  [OK]   {0}: {1}" -f $name, $path) -ForegroundColor Green
        return $true
    }
    if ($required) {
        Write-Host ("  [缺失] {0}: {1}" -f $name, $path) -ForegroundColor Red
        return $false
    }
    Write-Host ("  [可选] {0}: {1}" -f $name, $path) -ForegroundColor Yellow
    return $true
}

Write-Host ""
Write-Host "===== Mobius 环境检查 =====" -ForegroundColor Cyan
Write-Host ""

Check "安装目录" $root $true | Out-Null
Check "配置文件" (Join-Path $root 'config.json') $true | Out-Null

$pyCore = 'D:\Anaconda3\envs\open-webui\python.exe'
$pyGw = 'D:\AI\qqbot-mcp\Scripts\python.exe'
$pyMem = 'D:\AI\mobius-memory\Scripts\python.exe'
$uv = 'C:\Users\lenovo\.local\bin\uv.exe'

$coreOk = Check "Core Python 环境" $pyCore $true
$gwOk = Check "网关 Python 环境" $pyGw $true
Check "记忆 Python 环境" $pyMem $false | Out-Null
Check "uv（LangBot 原生运行时）" $uv $true | Out-Null
Check "原生 LangBot 源码" 'D:\AI\langbot-native\main.py' $true | Out-Null

Check "便携 PostgreSQL" 'D:\AI\pgsql-portable\pgsql\bin\pg_ctl.exe' $true | Out-Null
Check "原生 NapCat" 'D:\AI\napcat-native\NapCat.52230.Shell\NapCatWinBootMain.exe' $true | Out-Null
Check "ComfyUI（生图，可选）" 'D:\AI\comic\ComfyUI_windows_portable\ComfyUI\main.py' $false | Out-Null
Check "GPT-SoVITS（语音，可选）" 'D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50\api_v2.py' $false | Out-Null
Check "ComfyUI 模型目录" 'D:\AI\comic\ComfyUI_windows_portable\ComfyUI\models' $false | Out-Null
Check "语音模型目录" 'D:\AI\community_models' $false | Out-Null

$ports = @(55432, 8900, 8899, 8902, 8903, 8000, 5300, 3000, 3001)
foreach ($p in $ports) {
    $busy = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        Write-Host ("  [占用] 端口 {0} 已被监听（若 Mobius 已在运行属正常）" -f $p) -ForegroundColor Yellow
    }
}

Write-Host ""
if ($coreOk -and $gwOk) {
    Write-Host "关键依赖齐备：双击 Mobius.exe 即可启动（需要管理员权限用于 NapCat）。" -ForegroundColor Green
    Write-Host "模型目录缺失不影响文本聊天；生图/语音需要对应模型路径。"
    Write-Host ""
    exit 0
} else {
    Write-Host "关键依赖缺失：请按上面 [缺失] 项安装/放置后再运行。" -ForegroundColor Red
    Write-Host ""
    exit 1
}
