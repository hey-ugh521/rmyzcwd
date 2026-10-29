# Möbius 开机自启入口（由计划任务 "Mobius Boot" 在登录后调用）
#
# 行为：
#   1. 等 Docker 引擎就绪（NapCat / LangBot 在容器里）
#   2. 起 Möbius Core（8900）——它会自动带起轻量的 Media Gateway（8899）
#      ComfyUI 仍按媒体模式 on_demand，不会开机就吃显存
#   3. 起 watchdog.py（管 Core 存活 + NapCat/LangBot 容器 + 流水线卡死）
#   4. 保持自身常驻：计划任务实例活着，子进程才不会被计划任务回收
#
# 想开机就预热 GPU：把 config.json 的 core.prewarm_on_start 设为 true。
param(
    [switch]$Once
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root 'logs\boot.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null

function Log([string]$m) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m
    Add-Content -Path $log -Value $line -Encoding utf8
}

function Test-Port([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

Log "=== boot 开始（pid=$PID, once=$Once） ==="

# 1. 等 Docker（最多 120s，失败也继续——Core 不依赖 Docker 才能活）
$dockerReady = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
Log "docker 就绪=$dockerReady"

# 2. Core（幂等：已在跑就跳过）
try {
    & (Join-Path $PSScriptRoot 'start-mobius.ps1') *>> $log
    Log "Core 启动流程完成"
} catch {
    Log "Core 启动失败: $($_.Exception.Message)"
}

# 3. watchdog（单实例锁在 38999，重复启动会自己退出，所以可以直接拉）
function Start-Watchdog {
    $wd = Join-Path $root '..\qqbot\watchdog.py'
    if (-not (Test-Path $wd)) { Log "找不到 watchdog: $wd"; return }
    $wd = (Resolve-Path $wd).Path
    $py = 'D:\Anaconda3\envs\open-webui\python.exe'
    if (-not (Test-Path $py)) { $py = 'python' }
    Start-Process -FilePath $py -ArgumentList $wd, '--interval', '60' `
        -WorkingDirectory (Split-Path $wd) `
        -RedirectStandardOutput (Join-Path (Split-Path $wd) 'watchdog.run.log') `
        -RedirectStandardError (Join-Path (Split-Path $wd) 'watchdog.run.err.log') `
        -WindowStyle Hidden
    Log "watchdog 已拉起"
}

# 3.5 等 MCP 网关就绪，并修掉「LangBot 比网关先起」的竞态
#     开机时 Docker 容器和宿主机进程是并行起来的：LangBot 常常先于 Media Gateway 就绪，
#     它启动时连 MCP 失败后**不会重试**，于是群里发消息全部没有回复。
#     这里等网关就绪后按启动时间判定，必要时重启 LangBot 重建 MCP 会话。
function Wait-Gateway([int]$timeoutSec = 150) {
    for ($i = 0; $i -lt $timeoutSec; $i++) {
        try {
            $p = Invoke-RestMethod -Uri 'http://127.0.0.1:8899/api/pulse' -TimeoutSec 2
            if ($p.ok) { return $p }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $null
}

$pulse = Wait-Gateway
if ($null -eq $pulse) {
    Log "网关在 150s 内未就绪，跳过 MCP 会话检查"
} else {
    Log ("网关已就绪（uptime={0}s）" -f $pulse.uptime_sec)
    try {
        $gwStart = (Get-Date).AddSeconds(-[double]$pulse.uptime_sec)
        $lbStartedAt = docker inspect langbot --format '{{.State.StartedAt}}' 2>$null
        if ($lbStartedAt) {
            $lbStart = [datetime]::Parse($lbStartedAt).ToUniversalTime()
            $skew = ($gwStart.ToUniversalTime() - $lbStart).TotalSeconds
            if ($skew -gt 20) {
                Log ("检测到竞态：网关比 LangBot 晚 {0:N0}s 启动，重启 LangBot 重建 MCP 会话" -f $skew)
                docker restart langbot | Out-Null
                for ($i = 0; $i -lt 60; $i++) {
                    Start-Sleep -Seconds 2
                    try {
                        if ((Invoke-WebRequest 'http://127.0.0.1:5300/healthz' -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200) {
                            Log "LangBot 已重建并恢复"; break
                        }
                    } catch { }
                }
            } else {
                Log ("MCP 启动顺序正常（网关比 LangBot 早 {0:N0}s）" -f (-$skew))
            }
        }
    } catch {
        Log ("MCP 顺序检查失败: {0}" -f $_.Exception.Message)
    }
}

if (-not (Test-Port 38999)) { Start-Watchdog } else { Log "watchdog 已在运行" }

# 3.6 自检结论：把「开机自启到底成没成」写成一行结论，不用再去翻别处
function Write-BootVerdict {
    $rows = @()
    # Core
    try {
        $st = Invoke-RestMethod -Uri 'http://127.0.0.1:8900/api/status' -TimeoutSec 20
        $rows += "Core=OK($($st.media.mode))"
    } catch { $rows += "Core=FAIL" }
    # 网关
    try {
        $p = Invoke-RestMethod -Uri 'http://127.0.0.1:8899/api/pulse' -TimeoutSec 5
        $rows += "Gateway=OK"
    } catch { $rows += "Gateway=FAIL" }
    # LangBot
    try {
        $h = Invoke-WebRequest 'http://127.0.0.1:5300/healthz' -TimeoutSec 5 -UseBasicParsing
        $rows += "LangBot=OK"
    } catch { $rows += "LangBot=FAIL" }
    # NapCat（宿主端口 13000）
    try {
        $n = Invoke-RestMethod 'http://127.0.0.1:13000/get_status' -TimeoutSec 6
        $rows += "NapCat=$(if ($n.data.online) { '在线' } else { '未登录' })"
    } catch { $rows += "NapCat=FAIL" }
    # MCP 工具集（LangBot 有没有成功连上网关）
    try {
        $lbStart = docker inspect langbot --format '{{.State.StartedAt}}' 2>$null
        $mcpLog = docker logs langbot --since $lbStart 2>&1 | Out-String
        if ($mcpLog -match 'Failed to start MCP server') { $rows += 'MCP=FAIL(未连上)' } else { $rows += 'MCP=OK' }
    } catch { $rows += 'MCP=未知' }
    $verdict = ($rows -join '  ')
    Log ("开机自检：{0}" -f $verdict)
    if ($verdict -notmatch 'FAIL') { Log "开机自检结论：PASS" } else { Log "开机自检结论：FAIL（看上面哪一项）" }
}
Write-BootVerdict

if ($Once) { Log "=== boot 结束（--Once） ==="; return }

# 4. 常驻 + 兜底：计划任务实例活着，子进程才不会被回收
while ($true) {
    Start-Sleep -Seconds 120
    if (-not (Test-Port 8900)) {
        Log "Core 掉线，重新拉起"
        try { & (Join-Path $PSScriptRoot 'start-mobius.ps1') *>> $log } catch { Log "重拉 Core 失败: $($_.Exception.Message)" }
    }
    if (-not (Test-Port 38999)) {
        Log "watchdog 掉线，重新拉起"
        Start-Watchdog
    }
}
