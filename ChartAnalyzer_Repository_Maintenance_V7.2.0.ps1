$ErrorActionPreference = "Stop"
$Root = (Get-Location).Path
foreach ($Item in @(".git", "CHANGELOG.md", ".gitignore", "app.py")) {
    if (-not (Test-Path (Join-Path $Root $Item))) { throw "Run from ChartAnalyzer repository root. Missing: $Item" }
}
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$GitIgnore = @'
# Python
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
htmlcov/

# Virtual environments
.venv/
venv/
env/

# Secrets and local configuration
.env
.env.*
!.env.example
config.json
secrets*.json
credentials*.json
token*.json

# Runtime data and local state
data/
logs/
*.log
*.tmp
*.bak*
*.db
*.db-wal
*.db-shm
holdout_vault.json
strategy_validation_evidence.json

# Operating system / editor
.DS_Store
Thumbs.db
.idea/
.vscode/
'@
[System.IO.File]::WriteAllText((Join-Path $Root ".gitignore"), ($GitIgnore.Trim() + "`n"), $Utf8NoBom)

$ChangelogPath = Join-Path $Root "CHANGELOG.md"
$Changelog = [System.IO.File]::ReadAllText($ChangelogPath)
$OldTop = @'
## 7.1.0 - 2026-11-15
- Added optional read-only iran-crypto-mcp stdio client and schema-driven tool invocation.
- Added dashboard/API endpoints for price comparison, order books, candles and connection status.
- Added Windows setup script and Persian setup/release documentation.
- Kept live exchange snapshots isolated from historical backtest and OOS data.
- No signal, entry/exit, backtest or OOS gate logic changes.
- 184 pytest tests passed; upstream live test blocked by build-environment DNS/PyPI access.
'@
$NewTop = @'
## 7.2.0 - 2026-11-16
- Added Quant Research Engine: immutable hypothesis/experiment registry, dataset/config lineage and multiple-testing controls.
- Added circular moving-block bootstrap inference and block signal-permutation controls.
- Added execution-delay and market-impact cost modeling to backtests and passive benchmarks.
- Added chronological split, purged/embargoed CV and feature-availability audit primitives.
- Added parameter-neighborhood, regime and cross-symbol robustness research tools.
- Added a one-way final holdout seal with overlap protection and one-time exact-config evaluation.
- Integrated Research Lab dashboard/API and unified the canonical strategy fingerprint with the OOS deployment gate.
- Tightened deployment evidence floors: at least 200 OOS trades, PF >= 1.20, drawdown <= 15%, positive bootstrap lower bound and FDR q <= 0.05.
- No claim of profitability; deployment remains fail-closed until matching, fresh OOS evidence satisfies every gate.

## 7.1.0 - 2026-11-15
- Added optional read-only iran-crypto-mcp stdio client and schema-driven tool invocation.
- Added dashboard/API endpoints for price comparison, order books, candles and connection status.
- Added Windows setup script and Persian setup/release documentation.
- Kept live exchange snapshots isolated from historical backtest and OOS data.
- No signal, entry/exit, backtest or OOS gate logic changes.
- 184 pytest tests passed; upstream live test blocked by build-environment DNS/PyPI access.
'@
if (-not $Changelog.Contains($OldTop)) { throw "CHANGELOG header mismatch. Inspect before editing." }
$Changelog = $Changelog.Replace($OldTop, $NewTop)
$Changelog = [regex]::Replace($Changelog, '(?ms)^## 7\.2\.0 - Quant Research Engine\r?\n(?:- .*\r?\n)+', "", 1)
[System.IO.File]::WriteAllText($ChangelogPath, $Changelog, $Utf8NoBom)

$WorkflowDir = Join-Path $Root ".github\workflows"
New-Item -ItemType Directory -Force -Path $WorkflowDir | Out-Null
$Workflow = @'
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  test:
    name: Python ${{ matrix.python-version }} tests
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.12", "3.13"]

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt

      - name: Compile Python sources
        run: python -m compileall -q .

      - name: Run test suite
        run: python -m pytest -q
'@
[System.IO.File]::WriteAllText((Join-Path $WorkflowDir "ci.yml"), ($Workflow.Trim() + "`n"), $Utf8NoBom)

Write-Host "Updated .gitignore, CHANGELOG.md and .github/workflows/ci.yml" -ForegroundColor Green
git diff --check
git diff -- .gitignore CHANGELOG.md
python -m compileall -q .
python -m pytest -q
Write-Host "`nAfter reviewing successful tests, publish:" -ForegroundColor Yellow
Write-Host 'git add .gitignore CHANGELOG.md .github/workflows/ci.yml'
Write-Host 'git commit -m "chore: harden repository and add CI for v7.2.0"'
Write-Host 'git push origin main'
