# 打包一个 L3 组件：7z 压缩 + SHA256 + manifest 片段
# 用法：
#   .\ops\l3\build_component.ps1 -Name python-core -SourceDir D:\AI\mobius-runtime\python-core `
#       -Version 3.12-20261108 -OutDir D:\AI\components [-Kind runtime] [-SevenZip D:\AI\napcat-native\7z.exe]
param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$SourceDir,
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [string]$Kind = 'runtime',
    [string]$SevenZip = 'D:\AI\napcat-native\7z.exe',
    [string]$Target = '',
    [string]$License = 'mixed',
    [string]$Urls = ''
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$fragDir = Join-Path $repo 'ops\l3\packs'
New-Item -ItemType Directory -Force -Path $OutDir, $fragDir | Out-Null

if (-not (Test-Path $SevenZip)) { throw "找不到 7z: $SevenZip" }
if (-not (Test-Path $SourceDir)) { throw "找不到源目录: $SourceDir" }

# 组件包内保留顶层目录名（解压到 runtime/<id> 的父目录）
$sourceLeaf = Split-Path -Leaf $SourceDir
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("mobius-pack-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $staging | Out-Null
$link = Join-Path $staging $sourceLeaf
# 用 junction 避免复制大目录
cmd /c mklink /J "$link" "$SourceDir" | Out-Null

$archive = Join-Path $OutDir ("{0}-{1}.7z" -f $Name, $Version)
if (Test-Path $archive) { Remove-Item $archive -Force }

Write-Host "压缩中: $archive"
& $SevenZip a -t7z -mx=5 -y $archive $link | Out-Null
if (-not (Test-Path $archive)) { throw "7z 压缩失败" }

Remove-Item $link -Force -ErrorAction SilentlyContinue
Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue

$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLower()
$size = (Get-Item $archive).Length
$targetRel = if ($Target) { $Target } else { "runtime/$Name" }
$urlList = if ($Urls) { $Urls.Split(',') } else { @("https://example.invalid/mobius/components/" + (Split-Path -Leaf $archive)) }

$fragment = [ordered]@{
    id                  = $Name
    name                = $Name
    kind                = $Kind
    version             = $Version
    required            = $false
    depends_on          = @()
    size                = $size
    sha256              = $hash
    urls                = $urlList
    install             = @{ type = 'archive'; format = '7z'; target = $targetRel }
    license             = @{ type = $License; note = '' }
}
$fragPath = Join-Path $fragDir ("{0}-{1}.json" -f $Name, $Version)
$fragment | ConvertTo-Json -Depth 6 | Set-Content $fragPath -Encoding UTF8

Write-Host "完成："
Write-Host ("  包:      {0} ({1:N1} MB)" -f $archive, ($size / 1MB))
Write-Host ("  SHA256:  {0}" -f $hash)
Write-Host ("  片段:    {0}" -f $fragPath)
