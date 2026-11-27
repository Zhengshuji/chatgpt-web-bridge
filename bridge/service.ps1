# 守护脚本：保证 9225 上有已登录的 Chrome、8355 上有桥。
# 由计划任务每 10 分钟跑一次：健康就直接退出，不健康就把缺的那半拉起来。
# 这样 DSH 崩溃/重启（会带走它自己的子进程）也不会把桥和浏览器一起带走。
$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$profile = Join-Path $root "chrome_profile"
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$data = Join-Path $here "data"
New-Item -ItemType Directory -Force -Path $data | Out-Null

function Test-Cdp {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:9225/json/version" | Out-Null; return $true }
    catch { return $false }
}
function Test-Bridge {
    try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:8355/health" | Out-Null; return $true }
    catch { return $false }
}

if (-not (Test-Cdp)) {
    if (-not (Test-Path $chrome)) { Write-Host "找不到 Chrome"; exit 1 }
    # 上次被杀可能留下单例锁
    foreach ($f in @("SingletonLock", "SingletonCookie", "SingletonSocket")) {
        $p = Join-Path $profile $f
        if (Test-Path $p) { Remove-Item $p -Force -ErrorAction SilentlyContinue }
    }
    Start-Process -FilePath $chrome -ArgumentList @(
        "--user-data-dir=$profile",
        "--remote-debugging-port=9225",
        "--no-first-run", "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "https://chatgpt.com/"
    )
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Cdp) { break }
    }
    if (-not (Test-Cdp)) { Write-Host "Chrome 的 9225 没起来"; exit 1 }
    Write-Host "Chrome 已启动"
}

if (-not (Test-Bridge)) {
    $env:GPTWEB_CDP = "http://127.0.0.1:9225"
    $env:GPTWEB_PORT = "8355"
    if (-not $env:GPTWEB_REPO) { $env:GPTWEB_REPO = "https://github.com/Zhengshuji/chatgpt-web-bridge" }
    $env:GPTWEB_DUMP = "1"
    $py = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $py) { Write-Host "找不到 python"; exit 1 }
    Start-Process -FilePath $py.Source -ArgumentList "server.py" -WorkingDirectory $here `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $data "service.out.log") `
        -RedirectStandardError (Join-Path $data "service.err.log")
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Bridge) { break }
    }
    if (-not (Test-Bridge)) { Write-Host "桥没起来，看 data\service.err.log"; exit 1 }
    Write-Host "桥已启动"
}

Write-Host "一切正常"
