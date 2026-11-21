[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8765",
    [string]$CohortId = ("usdtirr-1h-h4-" + (Get-Date -Format "yyyyMMdd-HHmm")),
    [ValidateSet("ramzinex", "ccxt")]
    [string]$Source = "ramzinex",
    [string]$Symbol = "USDTIRR",
    [ValidateSet("1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d")]
    [string]$Timeframe = "1h",
    [ValidateRange(1, 300)]
    [int]$HorizonBars = 4,
    [ValidateRange(500, 10000)]
    [int]$Paths = 3000,
    [ValidateRange(120, 5000)]
    [int]$HistoryBars = 1200,
    [ValidateRange(1, 365)]
    [int]$CollectionDays = 45,
    [ValidateRange(30, 100000)]
    [int]$MinForecasts = 200,
    [ValidateRange(5, 10000)]
    [int]$MinSegmentCount = 30,
    [string]$Exchange = "binance",
    [switch]$UseBinanceMicrostructure
)

$ErrorActionPreference = "Stop"
if ($UseBinanceMicrostructure -and -not ($Source -eq "ccxt" -and $Exchange -eq "binance" -and $Symbol -eq "BTCUSDT")) {
    throw "UseBinanceMicrostructure requires -Source ccxt -Exchange binance -Symbol BTCUSDT."
}
$Start = [DateTime]::UtcNow.AddMinutes(2)
$End = $Start.AddDays($CollectionDays)
$Payload = [ordered]@{
    cohort_id = $CohortId
    source = $Source
    symbol = $Symbol
    tf = $Timeframe
    horizon_bars = $HorizonBars
    paths = $Paths
    n = $HistoryBars
    exchange = $Exchange
    use_binance_microstructure = [bool]$UseBinanceMicrostructure
    collection_start = $Start.ToString("o")
    collection_end = $End.ToString("o")
    min_forecasts = $MinForecasts
    min_segment_count = $MinSegmentCount
}

try {
    $Json = $Payload | ConvertTo-Json -Depth 5
    $Result = Invoke-RestMethod -Method Post -Uri ($BaseUrl.TrimEnd("/") + "/api/prospective-holdout/register") -ContentType "application/json" -Body $Json -TimeoutSec 60
    $Result | ConvertTo-Json -Depth 8
    Write-Host ""
    Write-Host "Cohort registered. Next, schedule Invoke-ProspectiveHoldoutTick.ps1 every five minutes."
    Write-Host ("Cohort ID: " + $CohortId)
}
catch {
    Write-Error ("Prospective holdout registration failed: " + $_.Exception.Message)
    exit 1
}
