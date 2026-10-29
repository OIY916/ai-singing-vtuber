<#
.SYNOPSIS
    大肥鱼 QQ 机器人 —— 一键启动 / 状态检查 / 守护

.DESCRIPTION
    管理的服务：
      - RVC 翻唱服务  :8899    (RVC 自带 Python)
      - AstrBot WebUI :6185
      - AstrBot WS    :62000   (aiocqhttp 反向 WS 服务器，等 LuckyLillia 连入)
      - LuckyLillia   :6198    (LLOneBot 改名版，自动拉起 QQ + LLBot + PMHQ)

    LuckyLillia 通过 ws-reverse 连到 ws://127.0.0.1:62000/ws，
    与 AstrBot 的 aiocqhttp 适配器端口天然匹配，无需改配置。

.EXAMPLE
    .\start_all.ps1            # 启动缺失的服务
    .\start_all.ps1 check      # 只看状态
    .\start_all.ps1 watch      # 守护模式，掉线自动拉起
#>
param(
    [ValidateSet('start', 'check', 'watch')]
    [string]$Action = 'start'
)

$ErrorActionPreference = 'Continue'

$ROOT     = 'E:\qq-bot'
$RVC_PY   = 'E:\RVC-bsq\RVCMAX_Nvidia_xiaoyuan\RVCMAX_Nvidia_xiaoyuan\Runtime\python.exe'
$ASTRBOT  = Join-Path $ROOT 'astrbot-venv\Scripts\astrbot.exe'

# LuckyLillia：LLOneBot 官方改名版，GUI 程序，auto_start_bot=true 会自动拉起 QQ/LLBot/PMHQ
$LUCKY_EXE = 'E:\qq-bot\runtime\LL-test\LuckyLillia.exe'
$LUCKY_DIR = 'E:\qq-bot\runtime\LL-test'

# 关键：UTF-8，否则 group_chat_plus 的 emoji 日志会抛 UnicodeEncodeError 崩进程
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8       = '1'
$env:COVER_PORT       = '8899'

$Services = @(
    [pscustomobject]@{ Name = 'RVC 翻唱服务';   Port = 8899;  ProcName = 'python' }
    [pscustomobject]@{ Name = 'AstrBot WebUI';  Port = 6185;  ProcName = 'astrbot' }
    [pscustomobject]@{ Name = 'AstrBot WS';     Port = 62000; ProcName = 'astrbot' }
    [pscustomobject]@{ Name = 'LuckyLillia';    Port = 6198;  ProcName = 'LuckyLillia' }
)

function Test-Port([int]$p) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    return [bool]$c
}

# LuckyLillia 是 GUI 程序，没有稳定监听端口（6198 是它的 WebUI，可关）。
# 所以用「进程存在」来判断是否已启动。
function Test-LuckyRunning {
    return [bool](Get-Process -Name 'LuckyLillia' -ErrorAction SilentlyContinue)
}

function Show-Status {
    Write-Host "`n---- 服务状态 ----" -ForegroundColor Cyan
    foreach ($s in $Services) {
        if (Test-Port $s.Port) {
            Write-Host ("  [OK]   {0,-16} :{1}" -f $s.Name, $s.Port) -ForegroundColor Green
        } else {
            Write-Host ("  [DOWN] {0,-16} :{1}" -f $s.Name, $s.Port) -ForegroundColor Red
        }
    }
    # LuckyLillia 主进程
    if (Test-LuckyRunning) {
        Write-Host ("  [OK]   {0,-16} 进程在运行" -f 'LuckyLillia(主)') -ForegroundColor Green
    } else {
        Write-Host ("  [DOWN] {0,-16} 进程未运行" -f 'LuckyLillia(主)') -ForegroundColor Red
    }
    # QQ 端是否真的连上了 AstrBot 的 62000
    $conn = Get-NetTCPConnection -LocalPort 62000 -State Established -ErrorAction SilentlyContinue
    if ($conn) {
        Write-Host "  [OK]   QQ<->AstrBot  已连接" -ForegroundColor Green
    } else {
        Write-Host "  [INFO] QQ<->AstrBot  未连接（LuckyLillia 可能正在登录/扫码）" -ForegroundColor Yellow
    }
    Write-Host ''
}

function Start-Cover {
    if (Test-Port 8899) { return $false }
    Write-Host '  启动 RVC 翻唱服务...' -ForegroundColor Yellow
    Start-Process -FilePath $RVC_PY `
        -ArgumentList (Join-Path $ROOT 'cover_service\cover_service.py') `
        -WorkingDirectory (Join-Path $ROOT 'cover_service') `
        -RedirectStandardOutput (Join-Path $ROOT 'cover_service\service.log') `
        -RedirectStandardError  (Join-Path $ROOT 'cover_service\service.err.log') `
        -WindowStyle Hidden
    return $true
}

function Start-Astrbot {
    if (Test-Port 6185) { return $false }
    Write-Host '  启动 AstrBot...' -ForegroundColor Yellow
    Start-Process -FilePath $ASTRBOT -ArgumentList 'run' `
        -WorkingDirectory $ROOT `
        -RedirectStandardOutput (Join-Path $ROOT 'astrbot_run.log') `
        -RedirectStandardError  (Join-Path $ROOT 'astrbot_run.err.log') `
        -WindowStyle Hidden
    return $true
}

function Start-Lucky {
    if (Test-LuckyRunning) { return $false }
    Write-Host '  启动 LuckyLillia...' -ForegroundColor Yellow
    Start-Process -FilePath $LUCKY_EXE `
        -WorkingDirectory $LUCKY_DIR
    return $true
}

function Wait-Port([int]$p, [int]$seconds = 40) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Test-Port $p) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Invoke-Start {
    $started = $false
    if (Start-Cover)   { $started = $true; if (-not (Wait-Port 8899 45)) { Write-Host '  ! RVC 启动超时' -ForegroundColor Red } }
    if (Start-Astrbot) { $started = $true; if (-not (Wait-Port 6185 45)) { Write-Host '  ! AstrBot 启动超时' -ForegroundColor Red } }
    if (Start-Lucky)   {
        $started = $true
        Write-Host '  等待 LuckyLillia 拉起 QQ / LLBot / PMHQ...' -ForegroundColor Gray
        Start-Sleep -Seconds 12
    }
    if (-not $started) { Write-Host '  所有服务都在运行，无需启动' -ForegroundColor Gray }
    return $started
}

switch ($Action) {
    'check' { Show-Status }
    'start' { Invoke-Start | Out-Null; Show-Status }
    'watch' {
        Write-Host '=== 守护模式：每 30 秒检查，掉线自动拉起（Ctrl+C 退出）===' -ForegroundColor Cyan
        while ($true) {
            Invoke-Start | Out-Null
            Show-Status
            Start-Sleep -Seconds 30
        }
    }
}
