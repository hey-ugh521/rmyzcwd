# Mobius NapCat 幂等启动包装（L3）
# 作用：无论从启动器、Core 服务、快捷方式还是脚本调用，都保证只有一个 NapCat 实例。
# 行为：
#   1. 命名互斥，串行化并发启动
#   2. 已存在 NapCatWinBootMain 进程或 3000/3001 已监听 → 直接退出（幂等 no-op）
#   3. 未提权 → 通过 UAC 自提权后启动
#   4. 否则启动 launcher.bat 一次
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File start-napcat.ps1 [-WhatIf]
# 退出码：0 = 已在运行或已成功发起；非 0 = 失败
param([switch]$WhatIf)

$ErrorActionPreference = 'Stop'
$napcatDir = 'D:\AI\napcat-native\NapCat.52230.Shell\versions\9.9.33-52230\resources\app\napcat'
$logFile = 'D:\AI\napcat-start.log'

function Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    try { Add-Content -Path $logFile -Value $line -Encoding UTF8 } catch { }
}

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-Port([int]$port) {
    try {
        $c = New-Object Net.Sockets.TcpClient
        $c.Connect('127.0.0.1', $port)
        $c.Close()
        return $true
    } catch { return $false }
}

$mutex = New-Object System.Threading.Mutex($false, 'Global\MobiusNapCatStart')
$hasLock = $false
try {
    $hasLock = $mutex.WaitOne(5000)
} catch [System.Threading.AbandonedMutexException] {
    $hasLock = $true
}

if (-not $hasLock) {
    Log '另一个启动流程正在进行，跳过（幂等）'
    exit 0
}

try {
    $running = @(Get-Process NapCatWinBootMain -ErrorAction SilentlyContinue)
    $portsUp = (Test-Port 3000) -or (Test-Port 3001)
    if ($running.Count -gt 0 -or $portsUp) {
        Log ("NapCat 已在运行（进程 {0} 个，端口 3000/3001 状态 {1}），跳过启动" -f $running.Count, $portsUp)
        exit 0
    }

    if ($WhatIf) {
        Log 'WhatIf：未运行，将会启动 NapCat'
        exit 0
    }

    if (-not (Test-Admin)) {
        Log '当前未提权，尝试通过 UAC 自提权启动'
        $args = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath
        Start-Process -FilePath 'powershell.exe' -ArgumentList $args -Verb RunAs -WindowStyle Hidden
        exit 0
    }

    if (-not (Test-Path (Join-Path $napcatDir 'launcher.bat'))) {
        Log "找不到 launcher.bat: $napcatDir"
        exit 1
    }

    Log '启动 NapCat（管理员）...'
    Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', '"launcher.bat"' `
        -WorkingDirectory $napcatDir -WindowStyle Minimized
    Log '已发起启动'
    exit 0
}
finally {
    if ($hasLock) { try { $mutex.ReleaseMutex() } catch { } }
    $mutex.Dispose()
}
