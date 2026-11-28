# 守护脚本：保证 9225 上有已登录的 Chrome、8355 上有桥。
#
#   powershell -File service.ps1          # 常驻监督（默认）：拉起后每 60s 巡检，缺什么补什么
#   powershell -File service.ps1 -Once    # 只巡检一次就退出（手动排错用）
#   powershell -File service.ps1 -Headed  # 有头模式（想看窗口时）
#
# 为什么要常驻：计划任务每次执行完，Windows 会回收它那棵进程树；一次性的脚本拉起 Chrome 后
# 立刻退出，Chrome 会跟着收到控制台关闭事件而死 —— 表现就是"每 10 分钟弹一次、起了又没"。
# 所以这里改成任务里跑一个不退出的小循环，并且由 service.vbs 以隐藏窗口启动。
param([switch]$Headed, [switch]$Once)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$profile = Join-Path $root "chrome_profile"
$data = Join-Path $here "data"
$logFile = Join-Path $data "service.log"
New-Item -ItemType Directory -Force -Path $data | Out-Null
. (Join-Path $here "browser.ps1")

if ($env:GPTWEB_HEADED -eq "1") { $Headed = [switch]$true }

function Write-Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Ensure-Chrome {
    if (Test-Cdp -Port 9225) { return $true }
    Write-Log "Chrome 不在（$($(if ($Headed) {'有头'} else {'无头'}))），拉起"
    return (Start-Chrome -Profile $profile -Port 9225 -Headed:$Headed)
}

function Ensure-Bridge {
    if (Test-Bridge -Port 8355) { return $true }
    $env:GPTWEB_CDP = "http://127.0.0.1:9225"
    $env:GPTWEB_PORT = "8355"
    if (-not $env:GPTWEB_REPO) { $env:GPTWEB_REPO = "https://github.com/Zhengshuji/chatgpt-web-bridge" }
    $env:GPTWEB_DUMP = "1"
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { Write-Log "找不到 python"; return $false }
    Write-Log "桥不在，拉起"
    Start-Process -FilePath $py.Source -ArgumentList "server.py" -WorkingDirectory $here `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $data "service.out.log") `
        -RedirectStandardError (Join-Path $data "service.err.log")
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Bridge -Port 8355) { return $true }
    }
    Write-Log "桥没起来，看 data\service.err.log"
    return $false
}

if ($Once) {
    $null = Ensure-Chrome
    $null = Ensure-Bridge
    if ((Test-Cdp -Port 9225) -and (Test-Bridge -Port 8355)) { Write-Log "巡检完成：一切正常" }
    else { Write-Log "巡检完成：仍有组件未起来" }
    exit 0
}

Write-Log "监督进程启动（Chrome: $(if ($Headed) {'有头'} else {'无头'})，每 60s 巡检）"
$round = 0
while ($true) {
    $round++
    $chromeOk = Ensure-Chrome
    $bridgeOk = Ensure-Bridge
    if ($round % 30 -eq 1) {  # 每 30 分钟记一条心跳，避免日志被刷爆
        Write-Log ("心跳：Chrome={0} 桥={1}" -f $(if ($chromeOk) {'ok'} else {'down'}), $(if ($bridgeOk) {'ok'} else {'down'}))
    }
    Start-Sleep -Seconds 60
}
