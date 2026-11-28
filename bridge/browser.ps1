# 浏览器启动的公共部分：service.ps1 和 run.ps1 都 dot-source 这个文件，
# 免得两处各写一遍参数、以后改了一处忘了另一处。

$script:ChromeExe = "C:\Program Files\Google\Chrome\Application\chrome.exe"
# 无头模式必须伪装 UA：HeadlessChrome 的 UA 会被 Cloudflare 挑战页挡住（实测）
$script:ChromeUA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
                   "(KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"

function Test-Cdp {
    param([int]$Port = 9225)
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:$Port/json/version" | Out-Null
        return $true
    } catch { return $false }
}

function Test-Bridge {
    param([int]$Port = 8355)
    try {
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:$Port/health" | Out-Null
        return $true
    } catch { return $false }
}

function Get-ChromeArgs {
    param(
        [Parameter(Mandatory = $true)][string]$Profile,
        [int]$Port = 9225,
        [switch]$Headed
    )
    $args = @(
        "--user-data-dir=$Profile",
        "--remote-debugging-port=$Port",
        "--no-first-run", "--no-default-browser-check",
        # 这三个防 Chrome 在窗口不可见/被遮挡时节流定时器与渲染（桥靠轮询 DOM 收回复）
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding"
    )
    if (-not $Headed) {
        $args += @(
            "--headless=new",
            "--disable-gpu",
            "--window-size=1440,900",
            "--disable-blink-features=AutomationControlled",
            "--user-agent=`"$script:ChromeUA`""
        )
    }
    $args += "https://chatgpt.com/"
    return $args
}

function Start-Chrome {
    param(
        [Parameter(Mandatory = $true)][string]$Profile,
        [int]$Port = 9225,
        [switch]$Headed,
        [int]$WaitSeconds = 30
    )
    if (-not (Test-Path $script:ChromeExe)) { throw "找不到 Chrome: $($script:ChromeExe)" }
    # 上次被杀可能留下单例锁，无头模式下会直接起不来
    foreach ($f in @("SingletonLock", "SingletonCookie", "SingletonSocket")) {
        $p = Join-Path $Profile $f
        if (Test-Path $p) { Remove-Item $p -Force -ErrorAction SilentlyContinue }
    }
    $mode = if ($Headed) { "有头" } else { "无头" }
    Write-Host "启动 Chrome（$mode，profile: $Profile，port: $Port）..."
    Start-Process -FilePath $script:ChromeExe -ArgumentList (Get-ChromeArgs -Profile $Profile -Port $Port -Headed:$Headed)
    for ($i = 0; $i -lt $WaitSeconds; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Cdp -Port $Port) { return $true }
    }
    return $false
}
