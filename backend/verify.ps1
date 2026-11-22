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

& $Python -m ruff check app/market_data/futures_risk.py app/market_data/futures_risk_routes.py app/market_data/futures_risk_alerts.py app/market_data/risk_tiers.py tests/test_futures_risk.py tests/test_futures_risk_alerts.py tests/test_futures_risk_assessment_route.py tests/test_risk_tiers.py app/market_data/open_interest.py app/market_data/open_interest_repository.py app/backtest/routes.py app/market_data/futures_snapshot_repository.py tests/test_futures_engine.py tests/test_market_data.py app/market_data/market_pressure.py app/market_data/futures_analysis_routes.py tests/test_open_interest.py tests/test_open_interest_repository.py tests/test_open_interest_routes.py tests/test_market_pressure.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$env:APP_SECRET = if ($env:APP_SECRET) { $env:APP_SECRET } else { "test-only-secret-not-for-production" }
& $Python -m pytest
exit $LASTEXITCODE
