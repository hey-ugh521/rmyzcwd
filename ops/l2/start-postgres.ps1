# 启动便携 PostgreSQL（Core 之前调用；P5 启动器也会用）
# 用法：powershell -ExecutionPolicy Bypass -File ops\l2\start-postgres.ps1
$ErrorActionPreference = 'Stop'
$base = 'D:\AI\pgsql-portable'
$pgctl = Join-Path $base 'pgsql\bin\pg_ctl.exe'
$data = Join-Path $base 'data'
$port = 55432

function Test-Port([int]$p) {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $p); $c.Close(); return $true } catch { return $false }
}

if (Test-Port $port) {
    Write-Output "PostgreSQL 已在监听 127.0.0.1:$port"
    exit 0
}

if (-not (Test-Path (Join-Path $data 'PG_VERSION'))) {
    Write-Error "数据目录未初始化: $data"
    exit 1
}

& $pgctl -D $data -o "-p $port -c listen_addresses=127.0.0.1" -l (Join-Path $base 'pg.log') start | Out-Null

for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Port $port) {
        Write-Output "PostgreSQL 已启动: 127.0.0.1:$port"
        exit 0
    }
}
Write-Error "PostgreSQL 启动超时，见 $base\pg.log"
exit 1
