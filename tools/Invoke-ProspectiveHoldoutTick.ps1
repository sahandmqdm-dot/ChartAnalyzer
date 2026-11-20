[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8765",
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')]
    [string]$CohortId,
    [ValidateSet("ramzinex", "ccxt")]
    [string]$Source = "ramzinex",
    [Parameter(Mandatory = $true)]
    [string]$Symbol,
    [ValidateSet("1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d")]
    [string]$Timeframe = "1h",
    [ValidateRange(1, 300)]
    [int]$HorizonBars = 4,
    [ValidateRange(500, 10000)]
    [int]$Paths = 3000,
    [ValidateRange(120, 5000)]
    [int]$HistoryBars = 1200,
    [string]$Exchange = "binance"
)

$ErrorActionPreference = "Stop"
$Payload = [ordered]@{
    cohort_id = $CohortId
    source = $Source
    symbol = $Symbol
    tf = $Timeframe
    horizon_bars = $HorizonBars
    paths = $Paths
    n = $HistoryBars
    exchange = $Exchange
}

try {
    $Json = $Payload | ConvertTo-Json -Depth 5
    $Result = Invoke-RestMethod -Method Post -Uri ($BaseUrl.TrimEnd("/") + "/api/prospective-holdout/tick") -ContentType "application/json" -Body $Json -TimeoutSec 120
    $Result | ConvertTo-Json -Depth 8
}
catch {
    Write-Error ("Prospective holdout tick failed: " + $_.Exception.Message)
    exit 1
}
