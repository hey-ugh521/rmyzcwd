# Mobius 环境检查（M3c：配置驱动，不依赖开发路径）
# 只读检查，不修改任何东西。
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File preflight.ps1
# 退出码：0 = 关键依赖齐备；1 = 有关键缺失

$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$cfgPath = Join-Path $root 'config.json'
if (-not (Test-Path $cfgPath)) { $cfgPath = Join-Path $root 'config.example.json' }

function Expand-MobiusPath([string]$value, [string]$rootDir) {
    if (-not $value) { return $value }
    $v = $value
    $v = $v.Replace('${ROOT}', $rootDir)
    $v = $v.Replace('${RUNTIME}', (Join-Path $rootDir 'runtime'))
    $v = $v.Replace('${DATA}', (Join-Path $rootDir 'data'))
    $v = $v.Replace('${AI}', (Join-Path $rootDir 'runtime'))
    $v = $v.Replace('${USERPROFILE}', $env:USERPROFILE)
    return $v
}

$cfg = $null
try { $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json } catch {}
if (-not $cfg) {
    Write-Host "无法读取配置: $cfgPath" -ForegroundColor Red
    exit 1
}

function Check($name, $path, $required) {
    $expanded = Expand-MobiusPath $path $root
    if ($expanded -and (Test-Path $expanded)) {
        Write-Host ("  [OK]   {0}: {1}" -f $name, $expanded) -ForegroundColor Green
        return $true
    }
    if ($required) {
        Write-Host ("  [缺失] {0}: {1}" -f $name, $expanded) -ForegroundColor Red
        return $false
    }
    Write-Host ("  [可选] {0}: {1}" -f $name, $expanded) -ForegroundColor Yellow
    return $true
}

Write-Host ""
Write-Host "===== Möbius 环境检查（配置驱动） =====" -ForegroundColor Cyan
Write-Host "  配置: $cfgPath"
Write-Host ""

$coreOk = $false
$gwOk = $false
$pgOk = $false
$launcher = $cfg.launcher
$services = $cfg.services

$corePy = if ($launcher) { $launcher.core_python } else { $null }
if (-not $corePy) { $corePy = ($services.gateway).python }
$coreOk = Check "Core Python" $corePy $true
$gwOk = Check "Gateway Python" ($services.gateway).python $true
$pgOk = Check "PostgreSQL pg_ctl" (($services.postgres).exe) $true
Check "Core 代码" (Join-Path $root 'core\server.py') $true | Out-Null
Check "Gateway 代码" (Join-Path $root 'media_gateway\server.py') $true | Out-Null
Check "secrets.json" (Join-Path $root 'data\secrets.json') $true | Out-Null
Check "config.json" (Join-Path $root 'config.json') $false | Out-Null

Check "LangBot" (($services.langbot).exe) $false | Out-Null
Check "NapCat" (($services.napcat).exe) $false | Out-Null
Check "ComfyUI" (($services.comfyui).python) $false | Out-Null
Check "Kokoro" (($services.kokoro).python) $false | Out-Null
Check "GSV" (($services.gsv).python) $false | Out-Null

$ports = @(55432, 8900, 8899, 8902, 8000, 5300, 3000, 3001)
foreach ($p in $ports) {
    $busy = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($busy) {
        Write-Host ("  [占用] 端口 {0} 已被监听（若 Möbius 已在运行属正常）" -f $p) -ForegroundColor Yellow
    }
}

Write-Host ""
if ($coreOk -and $gwOk -and $pgOk) {
    Write-Host "关键依赖齐备：双击 Mobius.exe 即可启动。" -ForegroundColor Green
    Write-Host "可选组件缺失只影响生图/语音/QQ；文本聊天可先用。"
    Write-Host ""
    exit 0
}
Write-Host "关键依赖缺失：请先运行 MobiusCLI.exe --headless bootstrap 安装必需组件。" -ForegroundColor Red
Write-Host ""
exit 1
