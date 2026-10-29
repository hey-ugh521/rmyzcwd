# 重放 LangBot 的「回复里的音频 → QQ 真语音」补丁
#
# 背景：语音回传是打在容器内 /app/src/langbot/pkg/pipeline/respback/respback.py 的热补丁。
#      `docker restart` 会保留（writable layer），但 `docker compose up --force-recreate`
#      或删容器重建会丢掉，且不会有任何报错——语音会静默退化成一条 URL 文本。
# 本脚本幂等：已打过就跳过，最后可选重启 LangBot 让补丁生效。

param(
    [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'
$patch = 'C:\Users\lenovo\Desktop\_说明文档\qqbot\langbot\patch_respback_voice.py'
$target = '/app/src/langbot/pkg/pipeline/respback/respback.py'

if (-not (Test-Path $patch)) { throw "找不到补丁脚本: $patch" }

$running = docker ps --format '{{.Names}}' | Where-Object { $_ -eq 'langbot' }
if (-not $running) { throw "langbot 容器未运行" }

Write-Host "[patch] 检查补丁是否已在位 ..."
$has = docker exec langbot sh -c "grep -c '_fetch_media_base64' $target || true"
if ([int]($has -as [string]).ToString() -ne '0') {
    Write-Host "[patch] 已打过补丁，跳过" -ForegroundColor Yellow
} else {
    Write-Host "[patch] 复制补丁进容器 ..."
    docker cp $patch "langbot:/tmp/patch_respback_voice.py" | Out-Null
    Write-Host "[patch] 执行补丁 ..."
    docker exec langbot python /tmp/patch_respback_voice.py
}

Write-Host "[patch] 校验关键分支："
docker exec langbot sh -c "grep -n \"audio/\" $target | head -4"

if (-not $NoRestart) {
    Write-Host "[patch] 重启 LangBot 使补丁生效 ..."
    docker restart langbot | Out-Null
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        try {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:5300/healthz' -TimeoutSec 3 -UseBasicParsing
            if ($r.StatusCode -eq 200) { Write-Host "[patch] LangBot 已恢复" -ForegroundColor Green; break }
        } catch { }
    }
}
Write-Host "[patch] 完成"
