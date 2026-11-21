# Ruff baseline: 2026-11-20

## Scope

This baseline was captured from the GitHub Actions run on `main` at commit
`f6f147c1b7e63fb57ffaa992b0168812204f1b32`.

Workflow command: `ruff check . --output-format=github`  
Ruff version: `0.17.6`  
Python version: `3.12.16`

## Results

- 459 Ruff diagnostics
- 73 affected Python files
- Static-analysis job failed (the workflow currently marks this job advisory)

### Diagnostics by rule

| Rule | Count |
|---|---:|
| UP006 | 109 |
| I001 | 87 |
| UP035 | 53 |
| BLE001 | 51 |
| UP045 | 41 |
| RUF046 | 25 |
| F401 | 17 |
| UP017 | 15 |
| TRY004 | 10 |
| RUF022 | 9 |
| F841 | 6 |
| RUF059 | 5 |
| C408 | 4 |
| SIM115 | 4 |
| SIM102 | 3 |
| RUF012 | 3 |
| RUF007 | 3 |
| UP031 | 2 |
| EXE001 | 2 |
| UP012 | 2 |
| PIE807 | 2 |
| FURB188 | 1 |
| FURB167 | 1 |
| RUF034 | 1 |
| FURB157 | 1 |
| S110 | 1 |
| FURB162 | 1 |

## Remediation policy

1. Preserve this baseline so later runs can be compared objectively.
2. Do not apply repository-wide autofixes without reviewing the complete diff.
3. Treat broad exception handling (`BLE001`), swallowed exceptions (`S110`),
   mutable class defaults (`RUF012`), and unused computed values (`F841`)
   as manual-review items. Do not silence these rules merely to obtain a clean
   lint run.
4. Apply mechanical import and annotation changes in small, reviewable batches.
5. Run the complete test matrix (Python 3.12 and 3.13), compile check, security
   audit, Bandit, and Ruff after each meaningful batch.
6. Keep this work separate from collector, market-data, signal, strategy,
   backtest, and evaluation behavior. Any behavior-changing fix requires its
   own focused tests and explicit review.

## Important limitation

This document records the lint state only. It does not establish that the
application is correct, that collector data is complete, or that the
42-day forward evaluation is ready to start.
