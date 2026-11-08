# L3 发布流水线（P6）
# 用法：
#   .\ops\l3\build_release.ps1 -Version 0.2.0
#   .\ops\l3\build_release.ps1 -Version 0.2.0 -SkipComponents -SkipLauncher   # 用已有产物
#   .\ops\l3\build_release.ps1 -Version 0.2.0 -SignThumbprint <THUMB> -BaseUrl <组件URL前缀>
#   .\ops\l3\build_release.ps1 -Version 0.2.0 -ExecuteUpload -Tag v0.2.0      # 需要 gh CLI
#
# 产物：release\<Version>\
#   MobiusSetup-<Version>.exe
#   fragments.json          组件 manifest（urls 已按 -BaseUrl 重写）
#   release.json            版本/哈希/大小/组件/安装器元数据
#   SHA256SUMS.txt
#   THIRD-PARTY.md / licenses.json / LICENSE
#   upload-commands.txt     上传命令（未执行时生成）
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$OutDir = "",
    [string]$ComponentDir = "D:\AI\components",
    [string]$RuntimeDir = "D:\AI\mobius-runtime",
    [string]$LangbotDir = "D:\AI\langbot-native",
    [string]$ComfyDir = "D:\AI\comic\ComfyUI_windows_portable",
    [string]$GsvDir = "D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50",
    [string]$PgDir = "D:\AI\pgsql-portable\pgsql",
    [string]$NapcatDir = "D:\AI\napcat-native\NapCat.52230.Shell\versions\9.9.33-52230\resources\app\napcat",
    [string]$BaseUrl = "",
    [string]$SignThumbprint = "",
    [switch]$SkipLauncher,
    [switch]$SkipComponents,
    [switch]$SkipInstaller,
    [switch]$SelfSign,
    [switch]$ExecuteUpload,
    [string]$Tag = ""
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo

if (-not $OutDir) { $OutDir = Join-Path $repo "release\$Version" }
$releaseDir = (New-Item -ItemType Directory -Force -Path $OutDir).FullName

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Error $msg; exit 1 }
function Get-Sha256($path) { (Get-FileHash $path -Algorithm SHA256).Hash.ToLower() }
function Rel($path) { $path.Replace($repo + '\', '') }

$sevenzip = 'D:\AI\napcat-native\7z.exe'
$iscc = 'D:\AI\InnoSetup\ISCC.exe'
$pyinstallerEnv = 'D:\AI\mobius-launcher\Scripts\python.exe'

if (-not (Test-Path $releaseDir)) { Fail "无法创建输出目录: $OutDir" }

# ---------------------------------------------------------------- 构建启动器
if (-not $SkipLauncher) {
    Step "构建启动器 exe"
    & (Join-Path $repo 'launcher\build.ps1')
    if ($LASTEXITCODE -ne 0) { Fail "启动器构建失败" }
}

# ---------------------------------------------------------------- 先签启动器（安装器会内嵌签名后的 exe）
if ($SelfSign -or $SignThumbprint) {
    Step "签名启动器（先于安装器打包）"
    $launcherTargets = @(
        (Join-Path $repo "launcher\dist\Mobius.exe"),
        (Join-Path $repo "launcher\dist\MobiusCLI.exe")
    )
    if ($SelfSign) {
        & (Join-Path $repo "ops\l3\sign.ps1") -SelfSigned -Trust -Path $launcherTargets
    } else {
        & (Join-Path $repo "ops\l3\sign.ps1") -Thumbprint $SignThumbprint -Path $launcherTargets
    }
    if ($LASTEXITCODE -ne 0) { Fail "启动器签名失败" }
}

# ---------------------------------------------------------------- 组件规格
$components = @(
    @{ Id = 'python-core';     Version = '3.12-20261108'; Source = "$RuntimeDir\python-core";     Target = 'runtime/python-core';     Exclude = '' },
    @{ Id = 'python-gateway';  Version = '3.12-20261108'; Source = "$RuntimeDir\python-gateway";  Target = 'runtime/python-gateway';  Exclude = '' },
    @{ Id = 'python-memory';   Version = '3.12-20261108'; Source = "$RuntimeDir\python-memory";   Target = 'runtime/python-memory';   Exclude = '' },
    @{ Id = 'python-langbot';  Version = '3.12-20261108'; Source = "$RuntimeDir\python-langbot";  Target = 'runtime/python-langbot';  Exclude = '' },
    @{ Id = 'python-kokoro';   Version = '3.12-20261108'; Source = "$RuntimeDir\python-kokoro";   Target = 'runtime/python-kokoro';   Exclude = '' },
    @{ Id = 'langbot';         Version = '20261108';      Source = $LangbotDir;                   Target = 'runtime/langbot';         Exclude = 'langbot-native\.venv\*;langbot-native\data\*;langbot-native\logs\*;langbot-native\tests\*;langbot-native\skills\*' },
    @{ Id = 'comfyui-runtime'; Version = '20261108';      Source = $ComfyDir;                     Target = 'runtime/comfyui';         Exclude = 'ComfyUI_windows_portable\ComfyUI\models\*;ComfyUI_windows_portable\cache\*;ComfyUI_windows_portable\ComfyUI\input\*;ComfyUI_windows_portable\ComfyUI\output\*;ComfyUI_windows_portable\ComfyUI\user\*' },
    @{ Id = 'gsv-runtime';     Version = '20261108';      Source = $GsvDir;                       Target = 'runtime/gsv';             Exclude = 'GPT-SoVITS-v4-20250529-nvidia50\GPT_weights_v2\*;GPT-SoVITS-v4-20250529-nvidia50\GPT_weights_v4\*;GPT-SoVITS-v4-20250529-nvidia50\SoVITS_weights_v2\*;GPT-SoVITS-v4-20250529-nvidia50\SoVITS_weights_v4\*' },
    @{ Id = 'postgres';        Version = '18.6-20261108'; Source = $PgDir;                        Target = 'runtime/postgres';        Exclude = 'pgsql\data\*;pgsql\*.log' },
    @{ Id = 'napcat-code';     Version = '4.18.42-20261108'; Source = $NapcatDir;                 Target = 'runtime/napcat';          Exclude = 'napcat\node_modules\*;napcat\config\*;napcat\logs\*;napcat\*.db;napcat\*.db-shm;napcat\*.db-wal' }
)

# ---------------------------------------------------------------- 构建组件
if (-not $SkipComponents) {
    Step "构建 $($components.Count) 个组件"
    if (-not (Test-Path $sevenzip)) { Fail "找不到 7z: $sevenzip" }
    foreach ($c in $components) {
        if (-not (Test-Path $c.Source)) {
            Write-Warning "跳过缺失源目录: $($c.Id) ($($c.Source))"
            continue
        }
        $pack = Join-Path $ComponentDir ("{0}-{1}.7z" -f $c.Id, $c.Version)
        if (Test-Path $pack) {
            Write-Host "  已存在，跳过: $pack"
            continue
        }
        Write-Host "  打包: $($c.Id)"
        $compArgs = @('-Name', $c.Id, '-SourceDir', $c.Source, '-Version', $c.Version,
                  '-OutDir', $ComponentDir, '-Target', $c.Target)
        if ($c.Exclude) { $compArgs += @('-ExcludePatterns', $c.Exclude) }
        & (Join-Path $repo 'ops\l3\build_component.ps1') @args
        if ($LASTEXITCODE -ne 0) { Fail "组件打包失败: $($c.Id)" }
    }
}

# ---------------------------------------------------------------- 汇总 manifest
# 聚合逻辑集中在 ops\l3\manifest_check.py（源片段 -> fragments.json）；
# 仅当 existing 同 id 的 sha256 一致时才沿用旧 parts/urls，防止旧分卷清单复活。
Step "汇总组件 manifest"
$manifestCheck = Join-Path $repo 'ops\l3\manifest_check.py'
& $pyinstallerEnv $manifestCheck write
if ($LASTEXITCODE -ne 0) { Fail "manifest 汇总失败" }

# 按 BaseUrl 重写 urls
$manifest = Get-Content 'ops\l3\packs\fragments.json' -Raw | ConvertFrom-Json
if ($BaseUrl) {
    foreach ($prop in $manifest.components.PSObject.Properties) {
        $c = $prop.Value
        $fn = "{0}-{1}.7z" -f $c.id, $c.version
        $c.urls = @(($BaseUrl.TrimEnd('/')) + '/' + $fn)
    }
    $manifest | ConvertTo-Json -Depth 10 | Set-Content 'ops\l3\packs\fragments.json' -Encoding UTF8
}

# ---------------------------------------------------------------- 构建安装器
if (-not $SkipInstaller) {
    Step "构建安装包"
    if (-not (Test-Path $iscc)) { Fail "找不到 Inno Setup: $iscc" }
    $iss = Get-Content 'installer\mobius.iss' -Raw
    $iss = $iss -replace '#define AppVersion "[^"]+"', "#define AppVersion ""$Version"""
    $iss | Set-Content 'installer\mobius.iss' -Encoding UTF8
    & $iscc 'installer\mobius.iss'
    if ($LASTEXITCODE -ne 0) { Fail "安装包构建失败" }
}

# ---------------------------------------------------------------- 签名（先签后哈希）
if ($SelfSign -or $SignThumbprint) {
    Step "签名（自签=$([bool]$SelfSign) 指纹=$SignThumbprint）"
    $signTargets = @(
        (Join-Path $repo "installer\dist\MobiusSetup-$Version.exe")
    )
    if ($SelfSign) {
        & (Join-Path $repo 'ops\l3\sign.ps1') -SelfSigned -Trust -Path $signTargets
    } else {
        & (Join-Path $repo 'ops\l3\sign.ps1') -Thumbprint $SignThumbprint -Path $signTargets
    }
    if ($LASTEXITCODE -ne 0) { Fail "签名失败" }
}

# ---------------------------------------------------------------- 发布前安全扫描（硬闸门）
Step "发布前安全扫描"
$scanner = Join-Path $repo 'ops\l3\secret_scan.py'
& $pyinstallerEnv $scanner --dir $repo --exclude .git release launcher logs tests ops/probe media_gateway/data media_gateway/tts_output media_gateway/uploads docs/archive config.json data secrets.json backups
if ($LASTEXITCODE -ne 0) { Fail "仓库文件安全扫描未通过，禁止发布" }
foreach ($c in $components) {
    $pack = Join-Path $ComponentDir ("{0}-{1}.7z" -f $c.Id, $c.Version)
    if (Test-Path $pack) {
        & $pyinstallerEnv $scanner --pack $pack
        if ($LASTEXITCODE -ne 0) { Fail "组件包未通过安全扫描：$pack" }
    }
}

# ---------------------------------------------------------------- 收集产物
Step "收集产物与哈希"
$installer = Join-Path $repo "installer\dist\MobiusSetup-$Version.exe"
if (-not (Test-Path $installer)) { Fail "找不到安装包: $installer" }
Copy-Item $installer $releaseDir -Force
Copy-Item 'ops\l3\packs\fragments.json' $releaseDir -Force
Copy-Item 'ops\l3\licenses\THIRD-PARTY.md' $releaseDir -Force
Copy-Item 'ops\l3\licenses\licenses.json' $releaseDir -Force
Copy-Item 'LICENSE' $releaseDir -Force

$componentEntries = @()
foreach ($prop in $manifest.components.PSObject.Properties) {
    $c = $prop.Value
    $pack = Join-Path $ComponentDir ("{0}-{1}.7z" -f $c.id, $c.version)
    if (Test-Path $pack) {
        $componentEntries += [ordered]@{
            id = $c.id; version = $c.version; size = (Get-Item $pack).Length
            sha256 = Get-Sha256 $pack; urls = $c.urls
        }
    }
}

$installerHash = Get-Sha256 $installer
$release = [ordered]@{
    product = 'Mobius'
    version = $Version
    built_at = (Get-Date).ToString('o')
    installer = [ordered]@{ file = (Split-Path $installer -Leaf); size = (Get-Item $installer).Length; sha256 = $installerHash }
    components = $componentEntries
    notes = 'L3 发布；组件需按 urls 托管；QQ 与受限模型不随包'
}
$release | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $releaseDir 'release.json') -Encoding UTF8

$sums = @()
Get-ChildItem $releaseDir -File | Where-Object { $_.Name -ne 'SHA256SUMS.txt' } | ForEach-Object {
    $sums += ("{0}  {1}" -f (Get-Sha256 $_.FullName), $_.Name)
}
$sums | Set-Content (Join-Path $releaseDir 'SHA256SUMS.txt') -Encoding UTF8

# ---------------------------------------------------------------- 签名
if ($SignThumbprint) {
    Step "签名"
    $targets = @((Join-Path $repo 'launcher\dist\Mobius.exe'), (Join-Path $repo 'launcher\dist\MobiusCLI.exe'), $installer)
    & (Join-Path $repo 'ops\l3\sign.ps1') -Thumbprint $SignThumbprint -Path $targets
}

# ---------------------------------------------------------------- 上传命令
$tag = if ($Tag) { $Tag } else { "v$Version" }
$cmdFile = Join-Path $releaseDir 'upload-commands.txt'
$lines = @(
    "@ components: 上传 $ComponentDir 下的 *.7z 到 release $tag",
    "gh release create $tag --title `"Mobius $Version`" --notes-file docs/L3-PLAN.md",
    "gh release upload $tag $ComponentDir\*.7z",
    "gh release upload $tag `"$releaseDir\MobiusSetup-$Version.exe`" `"$releaseDir\release.json`" `"$releaseDir\SHA256SUMS.txt`"",
    "# 或者用对象存储：",
    "ossutil cp -r $ComponentDir oss://<bucket>/mobius/components/",
    "ossutil cp `"$releaseDir\MobiusSetup-$Version.exe`" oss://<bucket>/mobius/releases/"
)
$lines | Set-Content $cmdFile -Encoding UTF8

if ($ExecuteUpload) {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Fail "需要 gh CLI（或手动执行 $cmdFile）" }
    Step "上传到 GitHub Release $tag"
    & gh release create $tag --title "Mobius $Version" --notes "Mobius $Version"
    & gh release upload $tag (Join-Path $ComponentDir '*.7z')
    & gh release upload $tag (Join-Path $releaseDir "MobiusSetup-$Version.exe") (Join-Path $releaseDir 'release.json') (Join-Path $releaseDir 'SHA256SUMS.txt')
}

Step "完成"
Write-Host ("  发布目录: {0}" -f $releaseDir)
Write-Host ("  安装包:   {0} ({1:N1} MB, sha256 {2})" -f (Split-Path $installer -Leaf), ((Get-Item $installer).Length / 1MB), $installerHash)
Write-Host ("  组件:     {0} 个" -f $componentEntries.Count)
Write-Host ("  上传命令: {0}" -f $cmdFile)
