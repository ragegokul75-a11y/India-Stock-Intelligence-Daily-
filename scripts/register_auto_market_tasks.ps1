$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Arguments = "-m india_stock_intelligence.cli auto-run --days 3 --top-debate 15"

Write-Host "Registering automatic Market Open (09:15) and Market Close (18:15) scheduled tasks..."

$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument $Arguments -WorkingDirectory $ProjectRoot
$TriggerOpen = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 09:15AM
$TriggerClose = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 06:15PM
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName "IndiaStockIntelligence_MarketOpen" -Action $Action -Trigger $TriggerOpen -Settings $Settings -Description "Automatic NSE Market Open Expectation & 3-Agent Debate (Bull/Bear/Judge)" -Force | Out-Null
Register-ScheduledTask -TaskName "IndiaStockIntelligence_MarketClose" -Action $Action -Trigger $TriggerClose -Settings $Settings -Description "Automatic NSE Market Close EOD Bhavcopy Ingestion & 3-Agent Debate (Bull/Bear/Judge)" -Force | Out-Null

Write-Host "Successfully registered scheduled tasks:"
Write-Host "  1. IndiaStockIntelligence_MarketOpen  (Mon-Fri 09:15 IST)"
Write-Host "  2. IndiaStockIntelligence_MarketClose (Mon-Fri 18:15 IST)"
