# 启动 Möbius 新版 Media Gateway（从 config.json services.gateway 读取 env/args）
$ErrorActionPreference = 'SilentlyContinue'
$root = 'C:\Users\lenovo\Desktop\_说明文档\mobius'
Set-Location $root

function Test-Port($port) {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $port); $c.Close(); return $true } catch { return $false }
}

if (Test-Port 8899) { Write-Output "Gateway 已在监听 8899"; exit 0 }

$cfg = Get-Content (Join-Path $root 'config.json') -Raw | ConvertFrom-Json
$gw = $cfg.services.gateway
foreach ($prop in $gw.env.PSObject.Properties) { [Environment]::SetEnvironmentVariable($prop.Name, [string]$prop.Value) }

Start-Process -FilePath $gw.python -ArgumentList $gw.args -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $root 'logs\gateway.run.log') `
    -RedirectStandardError (Join-Path $root 'logs\gateway.run.err.log')

for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 2
    try {
        $p = Invoke-RestMethod 'http://127.0.0.1:8899/api/pulse' -TimeoutSec 3
        if ($p.ok) { Write-Output "Gateway 启动成功"; exit 0 }
    } catch { }
}
Write-Output "Gateway 启动超时"
exit 1
