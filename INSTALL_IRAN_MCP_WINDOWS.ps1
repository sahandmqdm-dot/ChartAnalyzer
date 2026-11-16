param(
    [string]$UvxPath = "uvx"
)
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$ConfigPath = Join-Path $Project "config.json"

$uvx = Get-Command $UvxPath -ErrorAction SilentlyContinue
if (-not $uvx) {
    throw "uvx not found. Install uv first, reopen PowerShell, then run this script again."
}
& $uvx.Source --version
if ($LASTEXITCODE -ne 0) { throw "uvx did not run successfully." }

if (Test-Path $ConfigPath) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    Copy-Item $ConfigPath "$ConfigPath.$stamp.bak"
    $raw = Get-Content $ConfigPath -Raw -Encoding UTF8
    $config = if ([string]::IsNullOrWhiteSpace($raw)) { @{} } else { ConvertFrom-Json $raw -AsHashtable }
} else {
    $config = @{}
}
if ($config -isnot [System.Collections.IDictionary]) { throw "config.json root must be a JSON object." }

$config["iran_crypto_mcp"] = @{
    enabled = $true
    command = $uvx.Source
    args = @("iran-crypto-mcp")
    timeout_sec = 30
    default_quote = "IRT"
    default_exchange = "wallex"
    proxy = ""
}
$config | ConvertTo-Json -Depth 30 | Set-Content $ConfigPath -Encoding UTF8
Write-Host "Iran Crypto MCP enabled in config.json." -ForegroundColor Green
Write-Host "No exchange credentials are used. The integration is read-only." -ForegroundColor Cyan
Write-Host "Start ChartAnalyzer, then use the Iran Exchange Intelligence panel. First use may download the package from PyPI."
