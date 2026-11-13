Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:PlatformRoot = Split-Path $PSScriptRoot -Parent
$script:RuntimeRoot = Join-Path $script:PlatformRoot '.runtime/platform'
$script:ApiPython = Join-Path $script:PlatformRoot 'apps/api/.venv/Scripts/python.exe'
$script:WorkerPython = Join-Path $script:PlatformRoot 'apps/worker/.venv/Scripts/python.exe'
$script:DeliveryHelper = Join-Path $script:PlatformRoot 'scripts/delivery.py'

function Enter-PlatformLock([bool]$DemoMode) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($script:PlatformRoot))).Replace('-','') }
    finally { $sha.Dispose() }
    $mutex = [Threading.Mutex]::new($false, "Local\IAP-$digest-$DemoMode")
    try { $acquired = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $acquired = $true }
    if (!$acquired) { $mutex.Dispose(); throw 'Another start/stop operation owns this checkout/mode; retry after it completes' }
    return $mutex
}

function Invoke-Delivery([string]$Action, [bool]$DemoMode, [string]$AuthorizeDatabase = '') {
    $argsList = @('-X', 'utf8', $script:DeliveryHelper, $Action)
    if ($DemoMode) { $argsList += '--demo' }
    # Forwarded only when the operator typed it; .env alone never authorises a
    # migration of the production, E2E or any other non-test database.
    if ($AuthorizeDatabase) { $argsList += @('--authorize-database', $AuthorizeDatabase) }
    $output = & $script:ApiPython @argsList
    if ($LASTEXITCODE -ne 0) { throw "Delivery check failed: $Action (exit $LASTEXITCODE). Credentials are not printed." }
    return $output
}
function Get-PlatformConfig([bool]$DemoMode) {
    return (Invoke-Delivery 'config' $DemoMode | ConvertFrom-Json)
}
function Get-StatePath([string]$Role, [bool]$DemoMode) {
    $modeName = if ($DemoMode) { 'demo' } else { 'local' }
    return Join-Path $script:RuntimeRoot "pids/$modeName-$Role.json"
}
function Get-OwnedProcess([string]$StatePath) {
    if (!(Test-Path -LiteralPath $StatePath)) { return $null }
    $record = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    if ($record.root -ne $script:PlatformRoot) { throw 'PID record belongs to another checkout' }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($record.process_id)"
    if (!$process) { return $null }
    if ($process.CreationDate.ToUniversalTime().Ticks.ToString() -ne $record.started_ticks -or
        $process.ExecutablePath -ne $record.executable -or $process.CommandLine -ne $record.command_line) {
        throw 'PID ownership mismatch; refusing to manage a reused/unrelated process'
    }
    return $process
}
function Test-Tcp([string]$Server, [int]$Port) {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $connect = $client.ConnectAsync($Server, $Port)
        if (!$connect.Wait(1500)) { return $false }
        return $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}
function Test-Http([string]$Url) {
    try { return (Invoke-WebRequest -Uri $Url -TimeoutSec 3 -UseBasicParsing).StatusCode -eq 200 }
    catch { return $false }
}
function Wait-Ready([scriptblock]$Check, [string]$Name, [int]$Seconds=60) {
    $until = (Get-Date).AddSeconds($Seconds)
    do {
        if (& $Check) { return }
        Start-Sleep -Milliseconds 1000
    } while ((Get-Date) -lt $until)
    throw "$Name did not become ready; inspect .runtime/platform/logs"
}
function Start-OwnedService([string]$Role, [bool]$DemoMode, [string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory, [int]$Port=0) {
    $statePath = Get-StatePath $Role $DemoMode
    if (Get-OwnedProcess $statePath) { Write-Host "$Role already owned/running; reused"; return }
    if ($Port -and (Test-Tcp '127.0.0.1' $Port)) { throw "Port $Port is occupied by an unmanaged service; no process was killed" }
    foreach ($directory in @('pids','logs')) { New-Item -ItemType Directory -Force -Path (Join-Path $script:RuntimeRoot $directory) | Out-Null }
    if (Test-Path -LiteralPath $statePath) {
        Move-Item -LiteralPath $statePath -Destination "$statePath.stale.$([DateTime]::UtcNow.Ticks)"
    }
    $modeName = if ($DemoMode) { 'demo' } else { 'local' }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $logBase = Join-Path $script:RuntimeRoot "logs/$modeName-$Role-$stamp"
    # Only literal repo paths/operational flags, never credentials, are arguments.
    $quoted = $Arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' }
    $started = Start-Process -FilePath $Executable -ArgumentList $quoted -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -RedirectStandardOutput "$logBase.stdout.log" -RedirectStandardError "$logBase.stderr.log" -PassThru
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$($started.Id)"
    if (!$process) { throw "$Role exited immediately; inspect its logs" }
    @{root=$script:PlatformRoot; process_id=$started.Id; started_ticks=$process.CreationDate.ToUniversalTime().Ticks.ToString(); executable=$process.ExecutablePath; command_line=$process.CommandLine; stdout="$logBase.stdout.log"; stderr="$logBase.stderr.log"} |
        ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    Write-Host "$Role started (owned PID $($started.Id))"
}
function Stop-OwnedService([string]$Role, [bool]$DemoMode) {
    $statePath = Get-StatePath $Role $DemoMode
    $process = Get-OwnedProcess $statePath
    if ($process) {
        # Capture descendants while the verified parent still exists; never kill by port/name.
        $tree = [Collections.Generic.List[object]]::new()
        $tree.Add($process)
        for ($index=0; $index -lt $tree.Count; $index++) {
            $parent = $tree[$index]
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($parent.ProcessId)" | Where-Object { $_.CreationDate -ge $parent.CreationDate })
            foreach ($child in $children) { $tree.Add($child) }
        }
        for ($index=$tree.Count-1; $index -ge 0; $index--) {
            $owned = $tree[$index]
            $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($owned.ProcessId)"
            if ($current -and $current.CreationDate -eq $owned.CreationDate -and $current.ExecutablePath -eq $owned.ExecutablePath) {
                Stop-Process -Id $current.ProcessId -ErrorAction Stop
            }
        }
        Write-Host "$Role stopped (verified owned process tree)"
    } else { Write-Host "$Role not running" }
    if (Test-Path -LiteralPath $statePath) { Move-Item -LiteralPath $statePath -Destination "$statePath.stopped.$([DateTime]::UtcNow.Ticks)" }
}
function Invoke-Compose([string[]]$ComposeArguments) {
    & docker compose --project-directory $script:PlatformRoot -f (Join-Path $script:PlatformRoot 'docker-compose.yml') @ComposeArguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed (exit $LASTEXITCODE)" }
}
