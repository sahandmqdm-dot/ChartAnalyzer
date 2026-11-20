[CmdletBinding()]
param(
    [ValidateSet("Start", "Stop", "Status")]
    [string]$Action = "Status",
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$PythonPath = "",
    [string]$DataDir = "",
    [ValidateRange(1, 1000000)][int]$FsyncEveryEvents = 100,
    [ValidateRange(1, 1000000)][int]$HealthEveryEvents = 1000,
    [switch]$FsyncEachEvent,
    [int]$StopTimeoutSeconds = 45
)
$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
if (-not $PythonPath) { $PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe" }
if (-not $DataDir) { $DataDir = Join-Path $ProjectRoot "data\binance_raw" }
$PythonPath = [System.IO.Path]::GetFullPath($PythonPath)
$DataDir = [System.IO.Path]::GetFullPath($DataDir)
$RuntimeDir = Join-Path $ProjectRoot "runtime\binance_collector"
$PidFile = Join-Path $RuntimeDir "collector.pid.json"
$StopFile = Join-Path $RuntimeDir "STOP"
$StdoutLog = Join-Path $RuntimeDir "collector.stdout.log"
$StderrLog = Join-Path $RuntimeDir "collector.stderr.log"
$CollectorScript = Join-Path $ProjectRoot "binance_stream_collector.py"
function Get-CollectorProcess {
    if (-not (Test-Path $PidFile)) { return $null }
    try { $meta = Get-Content $PidFile -Raw | ConvertFrom-Json } catch { throw "PID metadata is unreadable: $PidFile. Inspect it manually; refusing to start or signal another process." }
    $proc = Get-Process -Id ([int]$meta.pid) -ErrorAction SilentlyContinue
    if (-not $proc) { return $null }
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $($meta.pid)" -ErrorAction SilentlyContinue
    if (-not $cim -or $cim.CommandLine -notlike "*binance_stream_collector.py*") { throw "PID file points to a process that is not the ChartAnalyzer Binance collector. Refusing to signal it." }
    return $proc
}
switch ($Action) {
    "Start" {
        if (-not (Test-Path $CollectorScript)) { throw "Collector script not found: $CollectorScript" }
        if (-not (Test-Path $PythonPath)) { throw "Virtual-environment Python not found: $PythonPath. Create .venv and install requirements first." }
        $null = New-Item -ItemType Directory -Force -Path $RuntimeDir, $DataDir
        $existing = Get-CollectorProcess
        if ($existing) { Write-Host "Collector already running. PID=$($existing.Id)"; exit 0 }
        Remove-Item $PidFile, $StopFile -Force -ErrorAction SilentlyContinue
        & $PythonPath -c "import websockets; print('websockets', websockets.__version__)"
        if ($LASTEXITCODE -ne 0) { throw "Python dependency check failed. Install requirements.txt in this virtual environment." }
        $argsList = @($CollectorScript, "--data-dir", $DataDir, "--symbol", "BTCUSDT", "--fsync-every-events", "$FsyncEveryEvents", "--health-every-events", "$HealthEveryEvents", "--stop-file", $StopFile)
        if ($FsyncEachEvent) { $argsList += "--fsync-each-event" }
        $quoted = $argsList | ForEach-Object { '"' + $_ + '"' }
        $proc = Start-Process -FilePath $PythonPath -ArgumentList ($quoted -join " ") -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $StdoutLog -RedirectStandardError $StderrLog
        $meta = [ordered]@{ pid = $proc.Id; started_at_utc = [DateTime]::UtcNow.ToString("o"); project_root = $ProjectRoot; python = $PythonPath; collector_script = $CollectorScript; data_dir = $DataDir; fsync_every_events = $FsyncEveryEvents; fsync_each_event = [bool]$FsyncEachEvent; health_every_events = $HealthEveryEvents }
        $tmp = "$PidFile.tmp"
        $meta | ConvertTo-Json -Depth 4 | Set-Content -Encoding utf8 $tmp
        Move-Item -Force $tmp $PidFile
        Start-Sleep -Seconds 2
        if ($proc.HasExited) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue; Get-Content $StderrLog -Tail 80 -ErrorAction SilentlyContinue; throw "Collector exited during startup. See $StderrLog" }
        Write-Host "Collector started. PID=$($proc.Id)"
        Write-Host "Raw data: $DataDir"
        Write-Host "Logs: $StdoutLog ; $StderrLog"
    }
    "Stop" {
        $proc = Get-CollectorProcess
        if (-not $proc) { Write-Host "Collector is not running."; Remove-Item $PidFile, $StopFile -Force -ErrorAction SilentlyContinue; exit 0 }
        New-Item -ItemType File -Force -Path $StopFile | Out-Null
        $deadline = [DateTime]::UtcNow.AddSeconds($StopTimeoutSeconds)
        do { Start-Sleep -Seconds 1; $proc.Refresh() } while (-not $proc.HasExited -and [DateTime]::UtcNow -lt $deadline)
        if (-not $proc.HasExited) { throw "Graceful stop timed out. Process was NOT force-killed. Inspect logs and stop manually only after preserving data." }
        Remove-Item $PidFile, $StopFile -Force -ErrorAction SilentlyContinue
        Write-Host "Collector stopped gracefully. Hash-chain store close/flush completed."
    }
    "Status" {
        $proc = Get-CollectorProcess
        if (-not $proc) { Write-Host "Collector is not running."; if (Test-Path $StderrLog) { Get-Content $StderrLog -Tail 20 }; exit 1 }
        $meta = Get-Content $PidFile -Raw | ConvertFrom-Json
        Write-Host "Collector RUNNING. PID=$($proc.Id)"
        Write-Host "Started UTC: $($meta.started_at_utc)"
        Write-Host "Data: $($meta.data_dir)"
        Write-Host "Fsync cadence: $($meta.fsync_every_events); fsync each: $($meta.fsync_each_event)"
        Write-Host "Health cadence: $($meta.health_every_events)"
        if (Test-Path $StdoutLog) { Write-Host "--- stdout tail ---"; Get-Content $StdoutLog -Tail 12 }
        if (Test-Path $StderrLog) { Write-Host "--- stderr tail ---"; Get-Content $StderrLog -Tail 12 }
    }
}
