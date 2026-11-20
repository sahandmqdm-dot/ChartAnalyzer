# ChartAnalyzer Versioning Policy

## Current product release

**ChartAnalyzer application version: 7.3.0**

The canonical runtime version is `APP_VERSION` in `version.py`. Runtime APIs, reports, diagnostics, MCP identity and research provenance must import this constant. Do not hard-code a second product version in application modules.

The root `VERSION` file and the current-version label in `README.md` are distribution/documentation mirrors and must match `version.py`. The newest dated section in `CHANGELOG.md` is the release-history record.

## Verify a checkout

Run these commands from the repository root:

```powershell
Get-Content .\VERSION
Select-String -Path .\version.py -Pattern 'APP_VERSION'
git rev-parse --short HEAD
```

The first two commands must report the same application version. The Git SHA identifies the exact source revision, not the product release.

When the application is running, query `GET /api/version`. If its result differs from the source checkout, confirm that the expected project directory and process are running, then restart the application.

## Separate dependency versions

GitHub Actions, Python packages, plugins and build tools have independent versions. Their version labels must never be described as the ChartAnalyzer application version. Workflow actions should remain pinned to immutable commit SHAs for supply-chain integrity; explanatory comments should identify them as CI dependencies without suggesting they are product releases.

## Release procedure

For each product release, update the canonical `APP_VERSION` once, synchronize the `VERSION` and README mirrors, add a dated top entry to `CHANGELOG.md`, and verify the runtime `/api/version` endpoint and version consistency tests before merging.
