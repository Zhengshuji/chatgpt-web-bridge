# 启动桥接服务：先确保 9225 上有已登录的 Chrome，再起 server.py
#
#   pwsh -File bridge\run.ps1              # 有头（默认，好排错）
#   pwsh -File bridge\run.ps1 -Headless    # 无头（无人值守用）
#
# 速度提醒：有头窗口被遮挡/最小化时，Chrome 会节流定时器与渲染，而桥是靠轮询 DOM 收回复的，
# 实测会慢 2~3 倍（45s+ vs 20s）。下面三个 --disable-*-throttling 就是治这个的，别删。
param([switch]$Headless)

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
    $ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
    $args = @(
        "--user-data-dir=$profile",
        "--remote-debugging-port=9225",
        "--no-first-run", "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding"
    )
    if ($Headless) {
        # 无头必须伪装 UA：HeadlessChrome 的 UA 会直接被 Cloudflare 挑战页挡住
        $args += @("--headless=new", "--disable-gpu", "--window-size=1440,900",
                   "--disable-blink-features=AutomationControlled", "--user-agent=`"$ua`"")
    }
    $args += "https://chatgpt.com/"
    Write-Host "启动 Chrome（profile: $profile，headless=$Headless）..."
    Start-Process -FilePath $chrome -ArgumentList $args
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Cdp "http://127.0.0.1:9225") { break }
    }
    if (-not (Test-Cdp "http://127.0.0.1:9225")) { throw "Chrome 的 9225 调试端口没起来" }
}

$env:GPTWEB_CDP = "http://127.0.0.1:9225"
$env:GPTWEB_PORT = "8355"
if (-not $env:GPTWEB_REPO) { $env:GPTWEB_REPO = "https://github.com/Zhengshuji/chatgpt-web-bridge" }

Set-Location $here
python server.py
