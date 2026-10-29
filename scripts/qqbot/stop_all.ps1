<#
.SYNOPSIS
    大肥鱼 QQ 机器人 —— 关闭所有服务

.DESCRIPTION
    停止顺序（避免互相抢端口 / 僵尸进程）：
      1. AstrBot   :6185
      2. RVC 服务  :8899
      3. LuckyLillia 主进程 —— 它会自动终止自己拉起的 QQ / LLBot / PMHQ
      4. 兜底清理 LLBot(node llbot.js) 和 PMHQ 残留
    注意：不会强杀用户自己手动开的 QQ.exe，避免误伤。
#>
$ErrorActionPreference = 'Continue'

Write-Host ''
Write-Host '--- 停止服务 ---' -ForegroundColor Cyan

$stopped = 0

# 1. AstrBot
Get-CimInstance Win32_Process -Filter "Name='astrbot.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("  停止 AstrBot (PID {0})" -f $_.ProcessId)
        $stopped++
    }

# 2. RVC 服务
Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'cover_service' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("  停止 RVC 服务 (PID {0})" -f $_.ProcessId)
        $stopped++
    }

# 3. LuckyLillia 主进程
#    LuckyLillia 是 LLOneBot 改名版，它启动时会拉起 QQ.exe / llbot.js / pmhq-win-x64.exe。
#    关闭 LuckyLillia 时它会主动终止这些子进程（日志会显示"正在终止 QQ 进程..."），
#    所以先停它，让它自己清理，避免强杀 QQ 导致 QQ 掉线重登。
Get-CimInstance Win32_Process -Filter "Name='LuckyLillia.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("  停止 LuckyLillia (PID {0})" -f $_.ProcessId)
        $stopped++
    }

# 4. 兜底清理 LuckyLillia 的子进程残留（若它没来得及回收）
Start-Sleep -Seconds 3
# LLBot：node 跑 llbot.js
Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'llbot' -and $_.CommandLine -notmatch 'dsh|runner' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("  停止 LLBot 残留 (PID {0})" -f $_.ProcessId)
        $stopped++
    }
# PMHQ
Get-CimInstance Win32_Process -Filter "Name='pmhq-win-x64.exe'" -ErrorAction SilentlyContinue |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host ("  停止 PMHQ 残留 (PID {0})" -f $_.ProcessId)
        $stopped++
    }

if ($stopped -eq 0) {
    Write-Host '  没有正在运行的服务' -ForegroundColor Gray
}

Start-Sleep -Seconds 3

Write-Host ''
Write-Host ("已停止 {0} 个进程" -f $stopped) -ForegroundColor Yellow
Write-Host ''
Write-Host '--- 端口状态 ---' -ForegroundColor Cyan
foreach ($p in 8899, 6185, 62000, 6198) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($c) {
        Write-Host ("  {0} = 仍在运行" -f $p) -ForegroundColor Red
    } else {
        Write-Host ("  {0} = 已关闭" -f $p) -ForegroundColor Green
    }
}
Write-Host ''
