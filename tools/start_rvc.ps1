# 启动 RVC 翻唱服务（8899）
# 用 cmd /c start 彻底脱离父进程，避免被调用方的 job 取消连带杀掉

$ErrorActionPreference = 'Continue'

# 项目根 = 本脚本所在目录（tools\）的上一层；RVC 位置读 paths.json
$Root = Split-Path -Parent $PSScriptRoot
$PathsFile = Join-Path $Root 'paths.json'
$Paths = if (Test-Path $PathsFile) { Get-Content $PathsFile -Raw -Encoding UTF8 | ConvertFrom-Json } else { $null }
$RvcHome = if ($Paths -and $Paths.rvc_home) { $Paths.rvc_home }
           elseif ($env:DFY_RVC_HOME) { $env:DFY_RVC_HOME } else { '' }

$RVC_PY = if ($RvcHome) { Join-Path $RvcHome 'Runtime\python.exe' } else { '' }
$SCRIPT = Join-Path $Root 'cover_service\cover_service.py'
$WORKDIR = Join-Path $Root 'cover_service'
$OUTLOG = Join-Path $Root 'cover_service\service.log'
$ERRLOG = Join-Path $Root 'cover_service\service.err.log'

Write-Host '=== 检查是否已在运行 ==='
$conn = Get-NetTCPConnection -LocalPort 8899 -State Listen -ErrorAction SilentlyContinue
if ($conn) {
    Write-Host '  ✅ 8899 已在监听，无需启动'
    exit 0
}

Write-Host '=== 启动 RVC 服务 ==='
if (-not $RVC_PY) {
    Write-Host '  ⚠️ 没有配置 RVC 路径：请在项目根 paths.json 里填 rvc_home'
    Write-Host '     （或设环境变量 DFY_RVC_HOME，模板见 paths.example.json）'
    exit 1
}
if (-not (Test-Path $RVC_PY)) { Write-Host "  找不到 python: $RVC_PY"; exit 1 }
if (-not (Test-Path $SCRIPT)) { Write-Host "  找不到脚本: $SCRIPT"; exit 1 }

# 关键：cmd /c start /B 让子进程完全脱离 PowerShell 的进程树
$inner = 'set COVER_PORT=8899 && set PYTHONIOENCODING=utf-8 && "' + $RVC_PY + '" "' + $SCRIPT + '" > "' + $OUTLOG + '" 2> "' + $ERRLOG + '"'
$cmd = '/c start "" /B cmd.exe /c "' + $inner + '"'

Start-Process -FilePath 'cmd.exe' -ArgumentList $cmd -WorkingDirectory $WORKDIR -WindowStyle Hidden | Out-Null

Write-Host '  等待启动（RVC 加载较慢，约 40 秒）……'
Start-Sleep -Seconds 40

Write-Host ''
Write-Host '=== 检查结果 ==='
$conn2 = Get-NetTCPConnection -LocalPort 8899 -State Listen -ErrorAction SilentlyContinue
if ($conn2) {
    $proc = Get-Process -Id $conn2.OwningProcess -ErrorAction SilentlyContinue
    Write-Host "  ✅ 8899 已监听  pid=$($conn2.OwningProcess)  $($proc.ProcessName)"
} else {
    Write-Host '  ❌ 8899 未监听，查看日志：'
    if (Test-Path $ERRLOG) {
        Write-Host '  --- service.err.log ---'
        Get-Content $ERRLOG -Tail 25 | ForEach-Object { Write-Host "    $_" }
    }
    if (Test-Path $OUTLOG) {
        Write-Host '  --- service.log ---'
        Get-Content $OUTLOG -Tail 25 | ForEach-Object { Write-Host "    $_" }
    }
    exit 1
}

# 健康检查
try {
    $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8899/health' -TimeoutSec 10
    $n = 0
    if ($r.models) { $n = $r.models.Count }
    Write-Host "  ✅ health OK，可用模型 $n 个"
} catch {
    Write-Host "  ⚠️ health 失败: $($_.Exception.Message)"
}

Write-Host ''
Write-Host '完成'
