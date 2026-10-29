# Stop MC server + bot. ASCII only.
# Match by what the command line actually contains: java "-jar server.jar", node "bot.js".
Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -like "*server.jar*" -or
  ($_.Name -eq "node.exe" -and $_.CommandLine -like "*bot.js*") -or
  ($_.Name -eq "python.exe" -and ($_.CommandLine -like "*http.server*" -or $_.CommandLine -like "*camera_driver*")) -or
  ($_.Name -eq "powershell.exe" -and $_.CommandLine -like "*watchdog.ps1*")
} | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "MC server + bot stopped."