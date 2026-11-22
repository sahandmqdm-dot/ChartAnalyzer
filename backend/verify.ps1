$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    py -3.12 -m venv (Join-Path $PSScriptRoot ".venv")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

& $Python -c "import sys; assert sys.version_info >= (3, 12), 'ChartAnalyzer backend requires Python 3.12+'"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python -m ruff check app tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:APP_SECRET = if ($env:APP_SECRET) { $env:APP_SECRET } else { "test-only-secret-not-for-production" }
& $Python -m pytest
exit $LASTEXITCODE
