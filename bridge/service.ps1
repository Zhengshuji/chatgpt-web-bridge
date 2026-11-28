# 守护脚本：保证 9225 上有已登录的 Chrome、8355 上有桥。
# 由计划任务每 10 分钟跑一次：健康就直接退出，不健康就把缺的那半拉起来。
# 这样 DSH 崩溃/重启（会带走它自己的子进程）也不会把桥和浏览器一起带走。
#
#   powershell -File service.ps1            # 默认无头（无人值守）
#   powershell -File service.ps1 -Headed    # 有头（调试时看得到窗口）
#   $env:GPTWEB_HEADED="1"                  # 计划任务里想临时改有头，就设这个
param([switch]$Headed)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$profile = Join-Path $root "chrome_profile"
$data = Join-Path $here "data"
New-Item -ItemType Directory -Force -Path $data | Out-Null
. (Join-Path $here "browser.ps1")

if ($env:GPTWEB_HEADED -eq "1") { $Headed = [switch]$true }

if (-not (Test-Cdp -Port 9225)) {
    if (-not (Start-Chrome -Profile $profile -Port 9225 -Headed:$Headed)) {
        Write-Host "Chrome 的 9225 没起来"
        exit 1
    }
    Write-Host "Chrome 已启动"
}

if (-not (Test-Bridge -Port 8355)) {
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
        if (Test-Bridge -Port 8355) { break }
    }
    if (-not (Test-Bridge -Port 8355)) { Write-Host "桥没起来，看 data\service.err.log"; exit 1 }
    Write-Host "桥已启动"
}

Write-Host "一切正常"
