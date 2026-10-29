# 大肥鱼 AI 唱歌直播 —— 一键启动
#
# 用法（右键「使用 PowerShell 运行」，或在 PowerShell 里执行）：
#     .\启动直播.ps1              # 启动三个服务
#     .\启动直播.ps1 -Stop        # 停止三个服务
#     .\启动直播.ps1 -Status      # 只看状态
#
# 启动顺序有讲究：
#   1) douyinLive  弹幕源（1088）——主服务要连它
#   2) RVC 翻唱     （8899）——最慢，先起来，后面等它
#   3) 直播主服务   （8790）——依赖上面两个
#
# 注意：本脚本只负责「启动服务」。开播前还要确认：
#   · OBS 已打开，场景里「皮套」浏览器源 = http://127.0.0.1:8790/avatar（1920×1080）
#   · 直播伴侣的「主麦克风」= CABLE Output
#   · 直播伴侣的「摄像头」= OBS Virtual Camera
#   · 抖音后台勾选「AI 生成」声明

param(
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = "Stop"

# ⚠️ 不写死盘符：从脚本位置往上找项目根（认 stream\main.py 当标记文件），
#    所以这个脚本放在项目根、或 scripts\live\ 下都能正常用。
function Find-ProjectRoot([string]$start) {
    $d = $start
    for ($i = 0; $i -lt 4 -and $d; $i++) {
        if (Test-Path (Join-Path $d "stream\main.py")) { return $d }
        $d = Split-Path -Parent $d
    }
    return $start
}
$Root = Find-ProjectRoot $PSScriptRoot

# 本机路径配置：项目根的 paths.json（不进仓库，模板见 paths.example.json）
$PathsFile = Join-Path $Root "paths.json"
$Paths = if (Test-Path $PathsFile) { Get-Content $PathsFile -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }

# RVC 安装根目录（其下应有 Runtime\python.exe 和 RVC_Core\）
$RvcHome = if ($Paths -and $Paths.rvc_home) { $Paths.rvc_home } elseif ($env:DFY_RVC_HOME) { $env:DFY_RVC_HOME } else { "" }
# 跑直播主服务的解释器：paths.json > 项目内 venv > 系统 python
$Py = if ($Paths -and $Paths.python -and (Test-Path $Paths.python)) { $Paths.python }
      elseif (Test-Path (Join-Path $Root "astrbot-venv\Scripts\python.exe")) { Join-Path $Root "astrbot-venv\Scripts\python.exe" }
      else { "python" }

function Get-PortPid([int]$Port) {
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($c) { return $c.OwningProcess } else { return $null }
}

function Show-StatusRow([string]$Name, [int]$Port) {
    $pid_ = Get-PortPid $Port
    if ($pid_) {
        Write-Host ("  {0,-14} :{1,-5} ✅ 运行中 (PID {2})" -f $Name, $Port, $pid_) -ForegroundColor Green
    } else {
        Write-Host ("  {0,-14} :{1,-5} ⏹ 未运行" -f $Name, $Port) -ForegroundColor DarkGray
    }
}

function Show-Status {
    Write-Host "`n=== 服务状态 ===" -ForegroundColor Cyan
    Show-StatusRow "弹幕采集"  1088
    Show-StatusRow "RVC 翻唱"  8899
    Show-StatusRow "直播主服务" 8790

    $obs = Get-Process obs64 -ErrorAction SilentlyContinue
    if ($obs) {
        Write-Host ("  {0,-14}        ✅ 运行中 (PID {1})" -f "OBS", $obs.Id) -ForegroundColor Green
    } else {
        Write-Host ("  {0,-14}        ⏹ 未运行" -f "OBS") -ForegroundColor DarkGray
    }

    $mate = Get-Process -Name "webcast_mate","直播伴侣" -ErrorAction SilentlyContinue
    if ($mate) {
        Write-Host ("  {0,-14}        ✅ 运行中" -f "直播伴侣") -ForegroundColor Green
    } else {
        Write-Host ("  {0,-14}        ⏹ 未运行" -f "直播伴侣") -ForegroundColor DarkGray
    }
    Write-Host ""
}

function Stop-All {
    foreach ($p in @(8790, 8899, 1088)) {
        $procId = Get-PortPid $p
        if ($procId) {
            Write-Host "  停止端口 $p (PID $procId)…"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
    # ⚠️ 光按端口杀不够：多实例时只有一个是绑定端口的，另一个照样在跑
    #    worker 抢 RVC（实测踩到，表现是翻唱 503「服务忙」）。
    Clear-DuplicateMain
    Write-Host "已停止（OBS / 直播伴侣 需要手动关）" -ForegroundColor Yellow
}

# ⚠️ 关于「重复实例」的正确判断（2026-10-27 实测澄清）
#
# 先说个坑：我一开始看到两个 python.exe 都在跑 stream/main.py，就判定是
# 重复实例，结果误杀了正常运行的服务。真相是：
#     astrbot-venv\Scripts\python.exe   ← 只是 launcher shim（26 万字节）
#              └── Python310\python.exe  ← 它 spawn 出来的真解释器，跑我们的代码
# 所以「两个进程」是**正常的一对**，不是重复！
#
# 真正该防的是：**两个独立的启动动作**（两棵不同的进程树）。
# 判断方法：把这些进程按「树根」去重，树根 ≥2 才是真重复。
# 而端口只有一个能绑上，抢 RVC 的通常是 bind 失败那棵树的子进程，
# 表现是每首歌都收到 RVC 503「服务忙，正在处理另一首」。
function Get-MainTrees {
    $procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*stream/main.py*" -or $_.CommandLine -like "*stream\main.py*" })
    if ($procs.Count -eq 0) { return @() }

    $byId = @{}
    foreach ($p in $procs) { $byId[$p.ProcessId] = $p }

    $roots = @{}
    foreach ($p in $procs) {
        $cur = $p
        $guard = 0
        while ($byId.ContainsKey($cur.ParentProcessId) -and $guard -lt 10) {
            $cur = $byId[$cur.ParentProcessId]
            $guard++
        }
        $roots[$cur.ProcessId] = $cur
    }
    return @($roots.Values)
}

function Clear-DuplicateMain {
    $roots = @(Get-MainTrees)
    if ($roots.Count -eq 0) { return }
    foreach ($r in $roots) {
        Write-Host ("  结束主服务进程树 root PID $($r.ProcessId)") -ForegroundColor Yellow
        # /T 连子进程一起结束，避免留下 shim 或真解释器
        & taskkill /PID $r.ProcessId /T /F 2>$null | Out-Null
    }
    Start-Sleep -Seconds 2
}

function Assert-SingleMain {
    $roots = @(Get-MainTrees)
    if ($roots.Count -gt 1) {
        $ids = ($roots | ForEach-Object { $_.ProcessId }) -join ", "
        Write-Host "  ❌ 检测到 $($roots.Count) 个独立的主服务进程树（root PID: $ids）！" -ForegroundColor Red
        Write-Host "     多棵进程树会互相抢 RVC 的那把锁，翻唱必然 503。" -ForegroundColor Red
        Write-Host "     执行：.\启动直播.ps1 -Stop  然后重新启动" -ForegroundColor Red
        return $false
    }
    return $true
}

function Wait-Port([int]$Port, [int]$TimeoutSec, [string]$Name) {
    for ($i = 0; $i -lt $TimeoutSec; $i++) {
        if (Get-PortPid $Port) { return $true }
        Start-Sleep -Seconds 1
    }
    Write-Host "  ⚠️ $Name 在 ${TimeoutSec}s 内没起来（端口 $Port）" -ForegroundColor Yellow
    return $false
}

if ($Stop)   { Stop-All; Show-Status; exit 0 }
if ($Status) { Show-Status; exit 0 }

Write-Host @"
========================================
  🐋 大肥鱼 AI 唱歌直播 —— 启动
========================================
"@ -ForegroundColor Cyan

# ---------- 日志目录 ----------
# ⚠️ 为什么要显式重定向（2026-10-27 踩过）：
#    Start-Process 默认**不捕获子进程的 stdout/stderr**，
#    结果主服务和 RVC 的 print() 全部消失，排查「连唱卡住」时只能靠猜，
#    还多次把旧日志当新日志，得出过错误结论。
#    现在强制重定向到 logs\ 下，按「哪天 / 哪个服务」分开。
$LogDir = Join-Path $Root "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$Day = Get-Date -Format "yyyy-MM-dd"

# 每次启动单独一个文件（带时分秒），这样「本次直播」的日志一眼能找到，
# 不会被上一次的旧内容混淆。
$Stamp = Get-Date -Format "HHmmss"

# ---------- 1) 弹幕采集 ----------
if (Get-PortPid 1088) {
    Write-Host "[1/3] 弹幕采集已在运行，跳过" -ForegroundColor DarkGray
} else {
    Write-Host "[1/3] 启动弹幕采集 (douyinLive :1088)…"
    # douyinLive 自己会写 run.log，这里额外捕获它的控制台输出
    # douyinLive 是第三方弹幕采集器，不随本仓库分发（见 README「第三方依赖」）。
    # 默认放在 <项目根>\tools_raw\douyinLive\ 下，也可用 paths.json 的 douyin_live_exe 指到别处。
    $DouyinDir = if ($Paths -and $Paths.douyin_live_exe) { Split-Path -Parent $Paths.douyin_live_exe }
                 else { Join-Path $Root "tools_raw\douyinLive" }
    $DouyinExe = if ($Paths -and $Paths.douyin_live_exe) { $Paths.douyin_live_exe }
                 else { Join-Path $DouyinDir "douyinLive.exe" }
    if (-not (Test-Path $DouyinExe)) {
        Write-Host "  ⚠️ 找不到弹幕采集器：$DouyinExe" -ForegroundColor Yellow
        Write-Host "     若已装到别处，请在 paths.json 里填 douyin_live_exe" -ForegroundColor DarkGray
    }
    Start-Process $DouyinExe `
        -ArgumentList "--config", (Join-Path $DouyinDir "config.yaml") `
        -WorkingDirectory $DouyinDir `
        -RedirectStandardOutput "$LogDir\douyin-$Day-$Stamp.log" `
        -RedirectStandardError "$LogDir\douyin-$Day-$Stamp.err.log"
    if (-not (Wait-Port 1088 30 "弹幕采集")) {
        Write-Host "  抖音未开播时它照样会起，只是收不到弹幕，可以继续" -ForegroundColor DarkGray
    }
}

# ---------- 2) RVC ----------
if (Get-PortPid 8899) {
    Write-Host "[2/3] RVC 翻唱服务已在运行，跳过" -ForegroundColor DarkGray
} else {
    Write-Host "[2/3] 启动 RVC 翻唱服务 (:8899)…（首次加载模型较慢，请耐心等）"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"
    if (-not $RvcHome) {
        Write-Host "  ⚠️ 没有配置 RVC 路径：请在项目根 paths.json 里填 rvc_home" -ForegroundColor Red
        Write-Host "     （或设环境变量 DFY_RVC_HOME，模板见 paths.example.json）" -ForegroundColor DarkGray
        exit 1
    }
    $RvcPy = Join-Path $RvcHome "Runtime\python.exe"
    if (-not (Test-Path $RvcPy)) {
        Write-Host "  ⚠️ 找不到 RVC 解释器：$RvcPy" -ForegroundColor Red
        exit 1
    }
    Start-Process $RvcPy `
        -ArgumentList "-u", (Join-Path $Root "cover_service\cover_service.py") `
        -WorkingDirectory (Join-Path $Root "cover_service") `
        -RedirectStandardOutput "$LogDir\rvc-$Day-$Stamp.log" `
        -RedirectStandardError "$LogDir\rvc-$Day-$Stamp.err.log"
    Wait-Port 8899 120 "RVC 翻唱" | Out-Null
}

# ---------- 3) 直播主服务 ----------
if (Get-PortPid 8790) {
    $ok = Assert-SingleMain
    if ($ok) {
        Write-Host "[3/3] 直播主服务已在运行，跳过" -ForegroundColor DarkGray
    } else {
        exit 1
    }
} else {
    # 端口空着，但可能有 bind 失败的僵尸实例还在跑 worker —— 先清干净再起
    Clear-DuplicateMain
    Write-Host "[3/3] 启动直播主服务 (:8790)…"
    $env:PYTHONIOENCODING = "utf-8"
    $env:PYTHONUTF8 = "1"
    # 主服务自己会 tee 到 logs\main-<date>.log，这里再兜一层进程级输出，
    # 万一 Python 层还没起来就崩了（比如 venv 坏了），也能留证据
    Start-Process $Py `
        -ArgumentList "-u", (Join-Path $Root "stream\main.py") `
        -WorkingDirectory $Root `
        -RedirectStandardOutput "$LogDir\main-stdout-$Day-$Stamp.log" `
        -RedirectStandardError "$LogDir\main-stderr-$Day-$Stamp.log"
    Wait-Port 8790 30 "直播主服务" | Out-Null
    Assert-SingleMain | Out-Null
}

Show-Status

Write-Host @"
访问地址：
  控制台   http://127.0.0.1:8790/
  直播间   http://127.0.0.1:8790/avatar    ← OBS 浏览器源用这个
  字幕页   http://127.0.0.1:8790/overlay

日志位置（$LogDir）：
  main-<日期>.log          主服务全部输出 + 异常堆栈
  events-<日期>.log        业务事件：点歌/翻唱/播放/礼物/在线人数
  rvc-<日期>-<时间>.log     RVC 服务输出（含 503「服务忙」）
  main-stdout/stderr-...    进程级兜底（Python 层没起来也能留证据）
  看最新事件：  Get-Content (Get-ChildItem "$LogDir\events-*.log" | Sort-Object LastWriteTime | Select-Object -Last 1)

开播前自测（不需要真实观众）：
  $Py $Root\tools\test_panel_data.py

提醒：
  · OBS「皮套」源应为 1920×1080，URL = http://127.0.0.1:8790/avatar
  · 直播伴侣 主麦克风 = CABLE Output，摄像头 = OBS Virtual Camera
  · 抖音后台记得勾「AI 生成」声明
"@ -ForegroundColor Gray
