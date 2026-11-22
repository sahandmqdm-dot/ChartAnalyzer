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
"$VENV_PYTHON" -m ruff check app tests
APP_SECRET="${APP_SECRET:-test-only-secret-not-for-production}" "$VENV_PYTHON" -m pytest
