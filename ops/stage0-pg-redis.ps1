# Mobius 阶段 0：PG 18 + Redis 7 独立容器（修正版）
# 修正：
#   ① PG18 PGDATA 显式指定，named volume 挂 /var/lib/postgresql/data
#   ② named volume（避开 Windows bind mount 权限问题）
#   ③ Redis 只用 --requirepass（不写无用的 -e REDIS_PASSWORD）
#   ④ 连接测试统一走 docker exec / docker run（修正 PGPASSWORD 卡死）
#   ⑤ 预留 mobius + langbot 两个库
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$root = 'D:\AI\mobius'
$envFile = Join-Path $root '.env'
$backupDir = Join-Path $root 'pgbackup'

Write-Host '=== 0) 目录准备 ==='
New-Item -ItemType Directory -Path $root -Force | Out-Null
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
Write-Host "  $root / $backupDir 就绪"

Write-Host '=== 1) 生成强随机密码 ==='
$chars = (48..57) + (65..90) + (97..122)
$pgPassword = -join (1..32 | ForEach-Object { [char]($chars | Get-Random) })
$redisPassword = -join (1..32 | ForEach-Object { [char]($chars | Get-Random) })
@"
PG_PASSWORD=$pgPassword
REDIS_PASSWORD=$redisPassword
PG_PORT=55432
REDIS_PORT=56379
PG_VOLUME=mobius_pgdata
REDIS_VOLUME=mobius_redisdata
BACKUP_DIR=$backupDir
"@ | Out-File -FilePath $envFile -Encoding utf8
# 收紧权限：仅当前用户可读
icacls $envFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" 2>$null | Out-Null
Write-Host "  密码已写入 $envFile（已收紧 ACL，不打印）"

Write-Host '=== 2) 网络与数据卷 ==='
docker network inspect mobius_data 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { docker network create mobius_data | Out-Null; Write-Host '  网络 mobius_data 已创建' } else { Write-Host '  网络 mobius_data 已存在' }
docker volume inspect mobius_pgdata 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { docker volume create mobius_pgdata | Out-Null; Write-Host '  卷 mobius_pgdata 已创建' } else { Write-Host '  卷 mobius_pgdata 已存在' }
docker volume inspect mobius_redisdata 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { docker volume create mobius_redisdata | Out-Null; Write-Host '  卷 mobius_redisdata 已创建' } else { Write-Host '  卷 mobius_redisdata 已存在' }

Write-Host '=== 3) 启动 PostgreSQL 18 ==='
docker rm -f mobius-pg 2>$null | Out-Null
docker run -d `
  --name mobius-pg `
  --network mobius_data `
  --restart unless-stopped `
  --health-cmd "pg_isready -U postgres" --health-interval 10s --health-retries 6 `
  -e POSTGRES_PASSWORD=$pgPassword `
  -e POSTGRES_DB=mobius `
  -e PGDATA=/var/lib/postgresql/data/pgdata `
  -v mobius_pgdata:/var/lib/postgresql/data `
  -p 127.0.0.1:55432:5432 `
  postgres:18 `
  -c shared_buffers=512MB `
  -c max_connections=100 `
  -c work_mem=4MB `
  -c effective_cache_size=4GB `
  -c max_wal_size=4GB `
  -c checkpoint_completion_target=0.9 `
  -c synchronous_commit=on `
  -c log_min_duration_statement=200ms `
  -c listen_addresses=* | Out-Null
Write-Host '  容器已创建，等待就绪…'

Write-Host '=== 4) 启动 Redis 7 ==='
docker rm -f mobius-redis 2>$null | Out-Null
docker run -d `
  --name mobius-redis `
  --network mobius_data `
  --restart unless-stopped `
  --env REDIS_PASSWORD=$redisPassword `
  --health-cmd 'redis-cli --no-auth-warning -a $REDIS_PASSWORD ping | grep -q PONG' `
  --health-interval 10s --health-retries 6 `
  -v mobius_redisdata:/data `
  -p 127.0.0.1:56379:6379 `
  redis:7 `
  redis-server `
  --requirepass $redisPassword `
  --maxmemory 256mb `
  --maxmemory-policy volatile-lru `
  --appendonly yes `
  --appendfsync everysec `
  --auto-aof-rewrite-percentage 100 `
  --auto-aof-rewrite-min-size 64mb `
  --bind 0.0.0.0 | Out-Null
Write-Host '  容器已创建'

Write-Host '=== 5) 等待就绪（最多 120s）==='
$pgReady = $false
for ($i = 0; $i -lt 60; $i++) {
    docker exec mobius-pg pg_isready -U postgres 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $pgReady = $true; break }
    Start-Sleep -Seconds 2
}
Write-Host "  PG 就绪=$pgReady（等待 $($i*2)s）"
$redisReady = $false
for ($i = 0; $i -lt 30; $i++) {
    $pong = docker exec mobius-redis redis-cli -a $redisPassword ping 2>$null
    if ($pong -match 'PONG') { $redisReady = $true; break }
    Start-Sleep -Seconds 1
}
Write-Host "  Redis 就绪=$redisReady（等待 ${i}s）"

Write-Host '=== 6) 验收 0.1 容器状态 ==='
docker ps --filter "name=mobius-" --format '{{.Names}}\t{{.Status}}' | Out-String

Write-Host '=== 7) 验收 0.5 PG 配置落地 ==='
docker exec mobius-pg psql -U postgres -t -c "SHOW shared_buffers; SHOW max_connections; SHOW work_mem; SHOW max_wal_size; SHOW effective_cache_size;" 2>&1 | Out-String

Write-Host '=== 8) 验收 0.4 认证 ==='
docker exec mobius-pg psql -U postgres -t -c "SELECT version();" 2>&1 | Select-Object -First 1 | Out-String
$redisNoAuth = docker exec mobius-redis redis-cli ping 2>&1
Write-Host "  Redis 无密码 ping -> $redisNoAuth（应为 NOAUTH）"
$redisAuth = docker exec mobius-redis redis-cli -a $redisPassword ping 2>&1
Write-Host "  Redis 有密码 ping -> $redisAuth（应为 PONG）"

Write-Host '=== 9) 验收 0.6 Redis 持久化 ==='
docker exec mobius-redis redis-cli -a $redisPassword CONFIG GET appendonly 2>&1 | Out-String
docker exec mobius-redis redis-cli -a $redisPassword CONFIG GET appendfsync 2>&1 | Out-String

Write-Host '=== 10) 验收 0.3 容器间连通（修正：PGPASSWORD）==='
docker run --rm --network mobius_data -e PGPASSWORD=$pgPassword postgres:18 psql -h mobius-pg -U postgres -t -c "SELECT inet_server_addr();" 2>&1 | Out-String
docker run --rm --network mobius_data redis:7 redis-cli -h mobius-redis -a $redisPassword ping 2>&1 | Out-String

Write-Host '=== 11) 验收 0.7 重启存活 ==='
docker exec mobius-pg psql -U postgres -d mobius -c "CREATE TABLE stage0_test (id serial primary key, created_at timestamptz default now()); INSERT INTO stage0_test DEFAULT VALUES;" 2>&1 | Out-String
docker restart mobius-pg mobius-redis | Out-Null
$pgBack = $false
for ($i = 0; $i -lt 40; $i++) {
    docker exec mobius-pg pg_isready -U postgres 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $pgBack = $true; break }
    Start-Sleep -Seconds 2
}
Write-Host "  重启后 PG 就绪=$pgBack（${i}次重试）"
docker exec mobius-pg psql -U postgres -d mobius -c "SELECT * FROM stage0_test;" 2>&1 | Out-String
docker exec mobius-pg psql -U postgres -d mobius -c "DROP TABLE stage0_test;" 2>&1 | Out-String
docker exec mobius-redis redis-cli -a $redisPassword ping 2>&1 | Out-String

Write-Host '=== 12) 验收 0.8 端口绑定（应为 127.0.0.1）==='
netstat -ano | Select-String ':55432|:56379' | ForEach-Object { $_.Line.Trim() } | Out-String

Write-Host '=== 13) 预留 langbot 库 ==='
docker exec mobius-pg psql -U postgres -c "CREATE DATABASE langbot;" 2>&1 | Out-String
docker exec mobius-pg psql -U postgres -c "\l" 2>&1 | Select-String 'mobius|langbot' | ForEach-Object { $_.Line } | Out-String

Write-Host '=== 阶段 0 完成 ==='
