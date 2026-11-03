# 启动 GPT-SoVITS 八重樱微调语音服务（原生 API 7873 + OpenAI 适配层 7871）
# 用法：powershell -ExecutionPolicy Bypass -File ops\tts\start-gsv.ps1
$ErrorActionPreference = 'SilentlyContinue'
$root = 'D:\AI\GPT-SoVITS-v4\GPT-SoVITS-v4-20250529-nvidia50'
$py = Join-Path $root 'runtime\python.exe'

function Test-Port($port) {
    try { $c = New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $port); $c.Close(); return $true } catch { return $false }
}

# 1) 原生 API
if (-not (Test-Port 7873)) {
    Start-Process -FilePath $py -ArgumentList 'api_v2.py','-a','127.0.0.1','-p','7873','-c','GPT_SoVITS/configs/tts_infer_yae.yaml' -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput "$root\api_native.log" -RedirectStandardError "$root\api_native.err.log"
    for ($i = 0; $i -lt 90; $i++) { Start-Sleep -Seconds 2; if (Test-Port 7873) { break } }
}

# 2) 加载微调权重
try {
    Invoke-RestMethod 'http://127.0.0.1:7873/set_gpt_weights?weights_path=GPT_weights_v2/yae-e12.ckpt' -TimeoutSec 180 | Out-Null
    Invoke-RestMethod 'http://127.0.0.1:7873/set_sovits_weights?weights_path=SoVITS_weights_v2/yae_e12_s300.pth' -TimeoutSec 180 | Out-Null
} catch { }

# 3) OpenAI 适配层
if (-not (Test-Port 7871)) {
    Start-Process -FilePath $py -ArgumentList 'D:\AI\gsv_openai_adapter.py' -WindowStyle Hidden -RedirectStandardOutput "$root\adapter.log" -RedirectStandardError "$root\adapter.err.log"
    for ($i = 0; $i -lt 60; $i++) { Start-Sleep -Seconds 2; if (Test-Port 7871) { break } }
}

Write-Output "GSV 语音服务就绪: 7873=$(Test-Port 7873) 7871=$(Test-Port 7871)"
