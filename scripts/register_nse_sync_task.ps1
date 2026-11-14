param(
    [string]$At = $env:INDIA_STOCK_NSE_SYNC_DAILY_TIME,
    [string]$TaskName = 'IndiaStockIntelligence NSE EOD Sync'
)

$ErrorActionPreference = 'Stop'
if (-not $At) { $At = '18:15' }
try {
    $ScheduleTime = [DateTime]::ParseExact($At, 'HH:mm', [Globalization.CultureInfo]::InvariantCulture)
} catch {
    throw 'At must be a 24-hour local time in HH:mm format, for example 18:15.'
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'The project virtual environment was not found. Create it before registering the task.'
}

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument '-m india_stock_intelligence.cli sync' `
    -WorkingDirectory $ProjectRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $ScheduleTime
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 5)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description 'Runs the India Stock Intelligence NSE sync framework; live retrieval remains blocked until an approved source adapter is configured.'

Write-Output "Registered '$TaskName' at local time $At. The current adapter remains blocked and does not download market data."
