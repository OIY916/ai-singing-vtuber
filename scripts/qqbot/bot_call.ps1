# Bot 通话 一键启动 / 关闭
# 用法：bot_call.ps1 start | stop | status
#
# 这条链路是 2026-10-26 实测打通的（见 STATUS.md「零之九」）：
#   LuckyLillia 有头模式拉起真实 QQ（机器人号有窗口）
#     → tools/call_service.py 轮询来电窗口，鼠标点击「接听」
#     → 环回录音 → ASR → LLM → TTS → CABLE Input → QQ 麦克风 → 对方听到
#
# ⚠️ 与旧的 start_all.ps1 无关：那个管的是 NapCat，NapCat 无头模式永远没有窗口，
#    也就永远接不了电话。这条链路必须用 LuckyLillia。

param(
    [Parameter(Position = 0)]
    [ValidateSet('start', 'stop', 'status')]
    [string]$Action = 'start',

    # 只在 stop 时有效：只停通话服务，保留 LuckyLillia 和 QQ，
    # 这样机器人文字聊天照常在线，只是不再自动接电话。
    [switch]$KeepBot
)

$ErrorActionPreference = 'Stop'
$Root = 'E:\qq-bot'
$Python = Join-Path $Root 'astrbot-venv\Scripts\python.exe'
$ServiceScript = Join-Path $Root 'tools\call_service.py'
$LLExe = Join-Path $Root 'runtime\LL-test\LuckyLillia.exe'
$LogDir = Join-Path $Root 'logs'
$CallLog = Join-Path $LogDir 'call_service.log'
$PidFile = Join-Path $LogDir 'call_service.pid'

function Write-Head([string]$text) {
    Write-Host ''
    Write-Host '============================================' -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host '============================================' -ForegroundColor Cyan
}

function Write-Ok([string]$t)   { Write-Host "  [OK]   $t" -ForegroundColor Green }
function Write-Warn([string]$t) { Write-Host "  [注意] $t" -ForegroundColor Yellow }
function Write-Bad([string]$t)  { Write-Host "  [失败] $t" -ForegroundColor Red }
function Write-Info([string]$t) { Write-Host "         $t" -ForegroundColor Gray }

# ---------------------------------------------------------------- 进程查找

function Get-CallServiceProc {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match 'call_service\.py' }
}

function Get-LLBotProc {
    # LuckyLillia 可能有多实例；用可执行路径筛
    Get-CimInstance Win32_Process -Filter "Name='LuckyLillia.exe'" -ErrorAction SilentlyContinue
}

function Get-BotQQProc {
    # QQ 进程里属于机器人号那个（命令行带 -q 3862642627 或者是 LLBot 拉起的）
    # 说明：LLBot 拉起的 QQ 主进程命令行形如 "D:\qq9\QQ.exe" (无 --type=)
    # 这里只做"有没有 QQ 主进程"的粗略判断
    Get-CimInstance Win32_Process -Filter "Name='QQ.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -notmatch '--type=' }
}

function Get-AnswerWindow {
    # 是否存在「语音通话」窗口（说明正在来电/通话中）
    param()
    $found = @()
    Add-Type -AssemblyName UIAutomationClient -ErrorAction SilentlyContinue
    try {
        $root = [System.Windows.Automation.AutomationElement]::RootElement
        $cond = [System.Windows.Automation.Condition]::TrueCondition
        foreach ($w in $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)) {
            try {
                if ($w.Current.Name -eq '语音通话') { $found += $w }
            } catch { }
        }
    } catch { }
    return $found
}

# ---------------------------------------------------------------- status

function Show-Status {
    Write-Head 'Bot 通话 · 状态'

    $ll = Get-LLBotProc
    if ($ll) {
        $llIds = ($ll | ForEach-Object { $_.ProcessId }) -join ','
        Write-Ok "LuckyLillia 运行中 (pid=$llIds)"
    } else {
        Write-Bad 'LuckyLillia 未运行（必须它拉起 QQ 才有窗口）'
    }

    $qq = Get-BotQQProc
    if ($qq) {
        Write-Ok "QQ 主进程 $($qq.Count) 个"
    } else {
        Write-Bad '没有 QQ 主进程'
    }

    $svc = Get-CallServiceProc
    if ($svc) {
        $svcIds = ($svc | ForEach-Object { $_.ProcessId }) -join ','
        Write-Ok "通话服务运行中 (pid=$svcIds)"
    } else {
        Write-Warn '通话服务未运行（不会自动接听）'
    }

    $win = Get-AnswerWindow
    if ($win.Count -gt 0) {
        Write-Warn "当前有「语音通话」窗口 $($win.Count) 个（来电中或通话中）"
    }

    # OneBot 反向连接（文字功能是否在线）
    $conn = Get-NetTCPConnection -LocalPort 62000 -State Established -ErrorAction SilentlyContinue
    if ($conn) {
        Write-Ok "文字功能在线（OneBot 62000 已连接）"
    } else {
        Write-Warn '文字功能未连接（OneBot 62000 无 ESTABLISHED）'
    }

    Write-Host ''
    Write-Info "日志: $CallLog"
}

# ---------------------------------------------------------------- start

function Start-All {
    Write-Head 'Bot 通话 · 启动'

    if (-not (Test-Path $Python)) {
        Write-Bad "找不到 Python: $Python"
        return 1
    }
    if (-not (Test-Path $ServiceScript)) {
        Write-Bad "找不到通话服务脚本: $ServiceScript"
        return 1
    }
    if (-not (Test-Path $LLExe)) {
        Write-Bad "找不到 LuckyLillia: $LLExe"
        return 1
    }

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    # 1) LuckyLillia（它负责拉起带窗口的 QQ 并登机器人号）
    $ll = Get-LLBotProc
    if ($ll) {
        Write-Ok "LuckyLillia 已在运行，跳过启动"
    } else {
        Write-Info '启动 LuckyLillia……'
        Start-Process -FilePath $LLExe -WorkingDirectory (Split-Path $LLExe) | Out-Null
        Start-Sleep -Seconds 12
        $ll = Get-LLBotProc
        if (-not $ll) {
            Write-Bad 'LuckyLillia 启动失败'
            return 1
        }
        $llIds = ($ll | ForEach-Object { $_.ProcessId }) -join ','
        Write-Ok "LuckyLillia 已启动 (pid=$llIds)"
    }

    # 2) 等 QQ 主进程出现（LLBot 拉起需要时间）
    Write-Info '等待 QQ 窗口就绪……'
    $waited = 0
    while ($waited -lt 40) {
        if (Get-BotQQProc) { break }
        Start-Sleep -Seconds 2
        $waited += 2
    }
    if (-not (Get-BotQQProc)) {
        Write-Warn '没等到 QQ 主进程 —— 可能需要在 LuckyLillia 里登录机器人号'
        Write-Info '打开 LuckyLillia 界面 → 系统配置 → 确认「自动登录 QQ 号」= 3862642627'
    } else {
        Write-Ok 'QQ 已就绪'
    }

    # 3) 通话服务
    $svc = Get-CallServiceProc
    if ($svc) {
        $svcIds = ($svc | ForEach-Object { $_.ProcessId }) -join ','
        Write-Ok "通话服务已在运行 (pid=$svcIds)"
    } else {
        Write-Info '启动通话服务（后台，日志写入 logs\call_service.log）……'
        $env:PYTHONIOENCODING = 'utf-8'
        $env:PYTHONUTF8 = '1'
        $env:ASTRBOT_ROOT = $Root

        # ⚠️ 不能用 Start-Process -RedirectStandardOutput + -PassThru：
        #    PowerShell 5.1 下这个组合会等子进程退出才返回，导致脚本"卡住"。
        #    实测踩到（2026-10-26）。改用 cmd /c start "" 让它彻底脱离。
        $cmdLine = "/c start `"`" /B `"$Python`" -u `"$ServiceScript`" > `"$CallLog`" 2> `"$LogDir\call_service.err.log`""
        $sp = Start-Process -FilePath 'cmd.exe' `
            -ArgumentList $cmdLine `
            -WorkingDirectory $Root `
            -WindowStyle Hidden `
            -PassThru
        Start-Sleep -Seconds 5

        # 用进程名 + 命令行反查，确认真的起来了
        $svc2 = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -match 'call_service\.py' }
        if ($svc2) {
            $ids = ($svc2 | ForEach-Object { $_.ProcessId }) -join ','
            $ids | Set-Content -Path $PidFile -Encoding ascii
            Write-Ok "通话服务已启动 (pid=$ids)"
        } else {
            Write-Bad '通话服务启动失败，看日志：'
            if (Test-Path $CallLog) { Get-Content $CallLog -Tail 15 | ForEach-Object { Write-Host "    $_" } }
            if (Test-Path "$LogDir\call_service.err.log") {
                Write-Info '错误输出：'
                Get-Content "$LogDir\call_service.err.log" -Tail 10 | ForEach-Object { Write-Host "    $_" }
            }
            return 1
        }
    }

    Write-Host ''
    Write-Ok '全部就绪，现在可以直接打电话给机器人号 3862642627'
    Write-Host ''
    Write-Warn '打电话前必须确认：手机上不要同时登着 3862642627'
    Write-Info '否则 QQ 会把来电推给手机，电脑端不响、也不会有来电窗口。'
    Write-Host ''
    Write-Info '查看实时日志：'
    Write-Info "  Get-Content '$CallLog' -Tail 30 -Wait"
    return 0
}

# ---------------------------------------------------------------- stop

function Stop-All {
    param([switch]$KeepBot)

    Write-Head 'Bot 通话 · 关闭'

    $stopped = 0

    # 1) 通话服务
    $svc = Get-CallServiceProc
    if ($svc) {
        foreach ($s in $svc) {
            # 只杀父进程（venv 的 python.exe 是转发器，子进程会跟着退出）
            # 子进程单独杀会报"找不到"，是噪音，不用管
            $isChild = $svc | Where-Object { $_.ProcessId -ne $s.ProcessId -and $_.ParentProcessId -eq $s.ProcessId }
            if ($isChild) { continue }
            try {
                Stop-Process -Id $s.ProcessId -Force -ErrorAction Stop
                Write-Ok "已停止通话服务 pid=$($s.ProcessId)"
                $stopped++
            } catch {
                # 进程可能已经跟着父进程退出了，不算失败
                Write-Info "pid=$($s.ProcessId) 已退出"
            }
        }
        Start-Sleep -Seconds 1
    } else {
        Write-Info '通话服务本来就没在运行'
    }

    if (Test-Path $PidFile) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }

    # 2) LuckyLillia + 它拉起的 QQ（-KeepBot 时跳过）
    if ($KeepBot) {
        Write-Info '-KeepBot 已指定：保留 LuckyLillia 与 QQ，机器人文字功能继续在线'
        Write-Host ''
        Write-Ok "已关闭 $stopped 个进程（仅通话服务）"
        Write-Info '如需再关电话功能，随时可再跑一次本脚本'
        return 0
    }

    #    ⚠️ 只关 LuckyLillia 拉起的那些 QQ，不碰用户自己开的 QQ
    $ll = Get-LLBotProc
    if ($ll) {
        # 先记下 LuckyLillia 的 pid，稍后找它的子进程树
        $llPids = @($ll.ProcessId)

        foreach ($p in $ll) {
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
                Write-Ok "已停止 LuckyLillia pid=$($p.ProcessId)"
                $stopped++
            } catch {
                Write-Warn "停止 LuckyLillia pid=$($p.ProcessId) 失败: $($_.Exception.Message)"
            }
        }

        # 关掉由 LLBot 派生的 QQ（父进程是 LuckyLillia 或已被终止）
        Start-Sleep -Seconds 2
        Write-Info '清理 LuckyLillia 拉起的 QQ 进程……'
        $all = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue
        $killed = 0
        foreach ($q in $all) {
            if ($q.Name -ne 'QQ.exe') { continue }
            if ($q.ParentProcessId -in $llPids) {
                try { Stop-Process -Id $q.ProcessId -Force -ErrorAction SilentlyContinue; $killed++ } catch {}
            }
        }
        if ($killed -gt 0) { Write-Ok "已清理 $killed 个 QQ 子进程" }
        else { Write-Info '没有由 LLBot 直接派生的 QQ 子进程（QQ 可能仍在后台，属正常）' }
    } else {
        Write-Info 'LuckyLillia 本来就没在运行'
    }

    Write-Host ''
    if ($stopped -gt 0) {
        Write-Ok "已关闭 $stopped 个进程"
    } else {
        Write-Info '没有需要关闭的进程'
    }
    Write-Host ''
    Write-Warn '机器人文字功能也随之离线（LuckyLillia 停了，QQ 就不在 OneBot 上了）'
    Write-Info '若只想关电话、保留文字聊天，请手动只停通话服务，不要关 LuckyLillia。'
    return 0
}

# ---------------------------------------------------------------- 主入口

switch ($Action) {
    'start'  { $code = Start-All }
    'stop'   { $code = Stop-All -KeepBot:$KeepBot }
    'status' { Show-Status; $code = 0 }
}

exit $code
