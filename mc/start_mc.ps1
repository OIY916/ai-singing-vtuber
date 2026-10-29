# Start MC server + bot.  ASCII ONLY on purpose (PS5 misreads UTF-8 without BOM, breaks lines).
# Client must match server.jar version: currently vanilla 1.20.1.
$root = "E:\qq-bot\mc"
# static HTTP server for the skin resource pack (server.properties -> resource-pack)
Start-Process python -ArgumentList "-m","http.server","8088","--directory","$root\http" -WindowStyle Hidden `
  -RedirectStandardOutput "$root\http_server.out.log" -RedirectStandardError "$root\http_server.err.log"
Start-Process java -ArgumentList "-Xmx3G","-Xms1G","-jar","$root\server\server.jar","nogui" `
  -WorkingDirectory "$root\server" -WindowStyle Hidden `
  -RedirectStandardOutput "$root\server\server.out.log" -RedirectStandardError "$root\server\server.err.log"
Start-Sleep -Seconds 2
$env:MC_VERSION = "1.20.1"
# bot 交给 watchdog 托管：崩了/OOM 会自动重启
Start-Process powershell -ArgumentList "-NoProfile","-ExecutionPolicy","Bypass","-File","$root\bot\watchdog.ps1" -WindowStyle Hidden
# first-person camera driver (RCON, step-limited teleports to dodge the moved-too-quickly check)
Start-Process python -ArgumentList "$root\camera_driver.py" -WorkingDirectory "E:\qq-bot" -WindowStyle Hidden `
  -RedirectStandardOutput "$root\camera_driver.out.log" -RedirectStandardError "$root\camera_driver.err.log"
Write-Host "MC server (25565) + bot watchdog + camera driver started. MC_VERSION=1.20.1"