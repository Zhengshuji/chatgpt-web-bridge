# 手动启动：拉起 Chrome 与桥（不动计划任务）。
#
#   powershell -File run.ps1             # 默认无头
#   powershell -File run.ps1 -Headed     # 有头，方便看页面/排错
param([switch]$Headed)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$profile = Join-Path $root "chrome_profile"
. (Join-Path $here "browser.ps1")

if (-not (Test-Cdp -Port 9225)) {
    if (-not (Start-Chrome -Profile $profile -Port 9225 -Headed:$Headed)) {
        throw "Chrome 的 9225 调试端口没起来"
    }
}

$env:GPTWEB_CDP = "http://127.0.0.1:9225"
$env:GPTWEB_PORT = "8355"
if (-not $env:GPTWEB_REPO) { $env:GPTWEB_REPO = "https://github.com/Zhengshuji/chatgpt-web-bridge" }

Set-Location $here
python server.py
