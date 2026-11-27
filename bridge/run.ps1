# 启动桥接服务：先确保 9225 上有已登录的 Chrome，再起 server.py
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$profile = Join-Path (Split-Path -Parent $here) "chrome_profile"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"

function Test-Cdp([string]$url) {
    try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "$url/json/version" | Out-Null); return $true }
    catch { return $false }
}

if (-not (Test-Cdp "http://127.0.0.1:9225")) {
    if (-not (Test-Path $chrome)) { throw "找不到 Chrome: $chrome" }
    Write-Host "启动 Chrome（profile: $profile）..."
    Start-Process -FilePath $chrome -ArgumentList @(
        "--user-data-dir=$profile", "--remote-debugging-port=9225",
        "--no-first-run", "--no-default-browser-check", "https://chatgpt.com/"
    )
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Cdp "http://127.0.0.1:9225") { break }
    }
    if (-not (Test-Cdp "http://127.0.0.1:9225")) { throw "Chrome 的 9225 调试端口没起来" }
}

$env:GPTWEB_CDP = "http://127.0.0.1:9225"
$env:GPTWEB_PORT = "8355"
if (-not $env:GPTWEB_REPO) { $env:GPTWEB_REPO = "https://github.com/Zhengshuji/q1-test-temp" }

Set-Location $here
py -3 server.py 2>$null
if ($LASTEXITCODE -ne 0) { python server.py }
