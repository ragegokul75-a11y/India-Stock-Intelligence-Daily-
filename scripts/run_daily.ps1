param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$MarketFile,
    [ValidateSet('NSE','BSE')]
    [string]$Exchange
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run .\scripts\setup.ps1 first."
}
if (-not (Test-Path -LiteralPath $MarketFile)) {
    throw "Market file not found: $MarketFile"
}
$Arguments = @('-m', 'india_stock_intelligence.cli', 'import', $MarketFile)
if ($Exchange) { $Arguments += @('--exchange', $Exchange) }
Push-Location $ProjectRoot
try {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Import exited with code $LASTEXITCODE" }
    & $Python -m india_stock_intelligence.cli report
    if ($LASTEXITCODE -ne 0) { throw "Report exited with code $LASTEXITCODE" }
}
finally { Pop-Location }
