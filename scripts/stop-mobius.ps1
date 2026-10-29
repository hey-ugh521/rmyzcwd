# Möbius Core 停止脚本
#   .\scripts\stop-mobius.ps1           # 停 Core（可选休眠媒体）
#   .\scripts\stop-mobius.ps1 -All      # 同时休眠 ComfyUI / 停 Gateway
param(
    [switch]$All
)

$ErrorActionPreference = 'Continue'

function Get-PortPid([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.OwningProcess }
    return $null
}

if ($All) {
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8900/api/media/mode' -Method Put `
            -ContentType 'application/json' -Body '{"mode":"off"}' -TimeoutSec 10 | Out-Null
        Write-Host "[mobius] 媒体模式已置为 off" -ForegroundColor Yellow
    } catch { }
}

# 先停 Core
$corePid = Get-PortPid 8900
if ($corePid) {
    Write-Host "[mobius] 停止 Core (PID $corePid)" -ForegroundColor Cyan
    Stop-Process -Id $corePid -Force -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 800
} else {
    Write-Host "[mobius] Core 未在运行"
}

if ($All) {
    foreach ($port in @(8899, 8188)) {
        $p = Get-PortPid $port
        if ($p) {
            Write-Host "[mobius] 停止端口 $port (PID $p)" -ForegroundColor Cyan
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        }
    }
}
Write-Host "[mobius] 完成"
