$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$OriginalTempPath = $env:TEMP
$OriginalTmpPath = $env:TMP
$TaskTempPath = Join-Path $ProjectRoot '.tmp'
New-Item -ItemType Directory -Force -Path $TaskTempPath | Out-Null
$env:TEMP = $TaskTempPath
$env:TMP = $TaskTempPath
Push-Location $ProjectRoot
try {
    $VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        $PythonCandidates = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
            (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
        )
        $PythonExe = $null
        foreach ($Candidate in $PythonCandidates) {
            if (Test-Path -LiteralPath $Candidate) {
                $VersionText = & $Candidate --version 2>&1
                if ($LASTEXITCODE -eq 0 -and "$VersionText" -match '^Python 3\.(1[2-9]|[2-9][0-9])') { $PythonExe = $Candidate; break }
            }
        }
        if (-not $PythonExe) {
            $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
            if ($PythonCommand) {
                $VersionText = & $PythonCommand.Source --version 2>&1
                if ($LASTEXITCODE -eq 0 -and "$VersionText" -match '^Python 3\.(1[2-9]|[2-9][0-9])') { $PythonExe = $PythonCommand.Source }
            }
        }
        if (-not $PythonExe) {
            throw 'Python 3.12 or newer is required. Install it from python.org and enable Add Python to PATH, then reopen PowerShell.'
        }
        & $PythonExe -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv.' }
    }
    & $VenvPython -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) {
        & $VenvPython -m ensurepip --upgrade --default-pip
        if ($LASTEXITCODE -ne 0) { throw 'Could not install pip into .venv.' }
    }
    & $VenvPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Could not install requirements.' }
    & $VenvPython -m pip install -e . --no-build-isolation
    if ($LASTEXITCODE -ne 0) { throw 'Could not install the local application.' }
    Write-Host 'Setup complete. Run: .\.venv\Scripts\python.exe -m india_stock_intelligence.cli migrate'
}
finally {
    Pop-Location
    $env:TEMP = $OriginalTempPath
    $env:TMP = $OriginalTmpPath
}
