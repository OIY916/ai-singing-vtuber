# Supervise the MC bot: restart it whenever it exits (OOM / crash).
# ASCII only on purpose (PS5 misreads UTF-8 without BOM).
# 2026-10-29: idempotent - check port 3008 before spawning, so we never
# end up with two bots (both connect as BigFatFish -> server kicks one).
$root = "E:\qq-bot\mc"
$env:MC_VERSION = "1.20.1"
while ($true) {
  $busy = Get-NetTCPConnection -LocalPort 3008 -State Listen -ErrorAction SilentlyContinue
  if ($busy) {
    Start-Sleep -Seconds 5
    continue
  }
  $p = Start-Process node -ArgumentList "--max-old-space-size=1536", "$root\bot\bot.js" `
    -WorkingDirectory "$root\bot" -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput "$root\bot\bot.out.log" -RedirectStandardError "$root\bot\bot.err.log"
  $p.WaitForExit()
  Start-Sleep -Seconds 5
}
