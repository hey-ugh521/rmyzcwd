# 注册 / 卸载「登录后自动拉起 Möbius」
#
#   .\scripts\install-autostart.ps1              # 安装（登录后 30s 自动启动）
#   .\scripts\install-autostart.ps1 -Uninstall   # 卸载
#   .\scripts\install-autostart.ps1 -Status      # 查看状态
#
# 说明：不写注册表启动项、不需要管理员权限，用当前用户的计划任务实现，
#       可在「任务计划程序」里看到 "Mobius Boot"，随时停用。
param(
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
$taskName = 'Mobius Boot'
$root = Split-Path -Parent $PSScriptRoot

if ($Status) {
    $t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host "未安装计划任务 '$taskName'" -ForegroundColor Yellow; return }
    $i = Get-ScheduledTaskInfo -TaskName $taskName
    $t | Select-Object TaskName, State | Format-Table -AutoSize | Out-String | Write-Host
    Write-Host ("上次运行: {0}  结果: 0x{1:X}" -f $i.LastRunTime, $i.LastTaskResult)
    return
}

if ($Uninstall) {
    $t = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($t) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "[autostart] 已卸载 '$taskName'" -ForegroundColor Yellow
    } else {
        Write-Host "[autostart] 本来就没装"
    }
    return
}

$launcher = Join-Path $root 'Mobius.exe'
if (-not (Test-Path $launcher)) { $launcher = Join-Path $root 'launcher\dist\Mobius.exe' }
if (-not (Test-Path $launcher)) { throw "找不到 Mobius 启动器: $launcher" }
Write-Host "[autostart] 使用启动器: $launcher"

$action = New-ScheduledTaskAction -Execute $launcher

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = 'PT30S'

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Limited -Force `
    -Description '登录后自动启动 Möbius（launcher + Core Supervisor 管理全部服务）。' | Out-Null

Write-Host "[autostart] 已注册计划任务 '$taskName'（登录后 30s 触发）" -ForegroundColor Green
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State | Format-Table -AutoSize | Out-String | Write-Host
Write-Host "验证：先跑 .\scripts\stop-mobius.ps1 -All 停掉，再执行 Start-ScheduledTask -TaskName '$taskName'"
