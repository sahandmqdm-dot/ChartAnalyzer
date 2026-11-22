#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$BACKEND_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_PYTHON="$BACKEND_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    "$PYTHON_BIN" -m venv "$BACKEND_DIR/.venv"
fi

"$VENV_PYTHON" -c 'import sys; assert sys.version_info >= (3, 12), "ChartAnalyzer backend requires Python 3.12+"'
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -e ".[dev]"
"$VENV_PYTHON" -m ruff check app/market_data/futures_risk.py app/market_data/futures_risk_routes.py app/market_data/futures_risk_alerts.py app/market_data/risk_tiers.py tests/test_futures_risk.py tests/test_futures_risk_alerts.py tests/test_futures_risk_assessment_route.py tests/test_risk_tiers.py app/market_data/open_interest.py app/market_data/open_interest_repository.py app/backtest/routes.py app/market_data/futures_snapshot_repository.py tests/test_futures_engine.py tests/test_market_data.py app/market_data/market_pressure.py app/market_data/futures_analysis_routes.py tests/test_open_interest.py tests/test_open_interest_repository.py tests/test_open_interest_routes.py tests/test_market_pressure.py
APP_SECRET="${APP_SECRET:-test-only-secret-not-for-production}" "$VENV_PYTHON" -m pytest
