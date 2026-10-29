# Möbius Core 启动脚本
#   .\scripts\start-mobius.ps1            # 只起 Core（媒体按需唤醒）
#   .\scripts\start-mobius.ps1 -Prewarm   # 起 Core 并预热 ComfyUI
#   .\scripts\start-mobius.ps1 -Foreground
param(
    [switch]$Prewarm,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = 'D:\Anaconda3\envs\open-webui\python.exe'
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null

function Test-Port([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return [bool]$c
}

if (-not (Test-Path $py)) { throw "找不到 Python: $py" }

if (Test-Port 8900) {
    Write-Host "[mobius] Core 已在运行（8900）" -ForegroundColor Yellow
} else {
    Write-Host "[mobius] 启动 Core ..." -ForegroundColor Cyan
    if ($Foreground) {
        & $py -m core.server
        return
    }
    $env:PYTHONUTF8 = '1'
    $p = Start-Process -FilePath $py -ArgumentList '-m', 'core.server' `
        -WorkingDirectory $root `
        -RedirectStandardOutput (Join-Path $logs 'core.out.log') `
        -RedirectStandardError (Join-Path $logs 'core.err.log') `
        -WindowStyle Hidden -PassThru
    Write-Host "[mobius] PID=$($p.Id)"
}

# 等待健康检查
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8900/health' -TimeoutSec 2
        if ($r.ok) { $ok = $true; break }
    } catch { }
}
if (-not $ok) {
    Write-Host "[mobius] Core 未就绪，查看 logs\core.err.log" -ForegroundColor Red
    Get-Content (Join-Path $logs 'core.err.log') -Tail 30 -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "[mobius] Core 就绪：http://127.0.0.1:8900  控制台 /console" -ForegroundColor Green

if ($Prewarm) {
    Write-Host "[mobius] 预热媒体能力 ..." -ForegroundColor Cyan
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8900/api/media/mode' -Method Put `
            -ContentType 'application/json' -Body '{"mode":"prewarm"}' -TimeoutSec 10 | Out-Null
        Invoke-RestMethod -Uri 'http://127.0.0.1:8900/api/media/wake' -Method Post -TimeoutSec 300 | Out-Null
        Write-Host "[mobius] 媒体能力已就绪" -ForegroundColor Green
    } catch {
        Write-Host "[mobius] 预热失败：$($_.Exception.Message)" -ForegroundColor Yellow
    }
}

$status = Invoke-RestMethod -Uri 'http://127.0.0.1:8900/api/status' -TimeoutSec 30
Write-Host ""
Write-Host "媒体模式 : $($status.media.mode)"
foreach ($k in $status.media.services.PSObject.Properties.Name) {
    $s = $status.media.services.$k
    Write-Host ("  {0,-8} {1,-8} port={2}" -f $k, $s.state, $s.port)
}
Write-Host "LangBot  : $($status.langbot.reachable)"
