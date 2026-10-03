[CmdletBinding()]
param([switch]$Demo, [switch]$WithSearch, [switch]$SkipInfrastructure)
. "$PSScriptRoot/platform-common.ps1"
$operationLock = Enter-PlatformLock ([bool]$Demo)
Push-Location $script:PlatformRoot
try {
    & "$PSScriptRoot/check-environment.ps1" -Demo:$Demo
    if ($LASTEXITCODE -ne 0) { throw 'Environment check failed' }
    $config = Get-PlatformConfig ([bool]$Demo)
    Write-Host "Starting $($config.mode); DB=$($config.database)"
    if (!$SkipInfrastructure) {
        if ($config.db_host -notin @('localhost','127.0.0.1')) { throw 'External database: use -SkipInfrastructure after preparing dependencies' }
        $services = @('postgres','redis','minio')
        if ($config.vector_required -and !$Demo) { $services += @('etcd','milvus-standalone') }
        if ($WithSearch) { $services += 'searxng' }
        Invoke-Compose (@('up','-d') + $services)
    }
    Wait-Ready { Test-Tcp $config.db_host $config.db_port } 'PostgreSQL'
    Wait-Ready { Test-Tcp $config.redis_host $config.redis_port } 'Redis'
    Wait-Ready { Test-Http $config.minio_health_url } 'MinIO'
    if ($config.vector_required -and !$Demo -and !$SkipInfrastructure) { Wait-Ready { Test-Http 'http://127.0.0.1:9091/healthz' } 'Milvus' }
    # Migration failures happen before starting any new business processes.
    Invoke-Delivery 'migrate' ([bool]$Demo) | Write-Host
    if ($Demo) { Invoke-Delivery 'seed' $true | Write-Host }
    $modeArgs = if ($Demo) { @('--demo') } else { @() }
    Start-OwnedService 'api' ([bool]$Demo) $script:ApiPython (@('-X','utf8',$script:DeliveryHelper,'api')+$modeArgs) (Join-Path $script:PlatformRoot 'apps/api') $config.api_port
    Wait-Ready { Test-Http "http://127.0.0.1:$($config.api_port)/api/v1/health" } 'API'
    Start-OwnedService 'worker' ([bool]$Demo) $script:WorkerPython (@('-X','utf8',$script:DeliveryHelper,'worker')+$modeArgs) (Join-Path $script:PlatformRoot 'apps/worker')
    Wait-Ready { try { Invoke-Delivery 'worker-ping' ([bool]$Demo) | Out-Null; return $true } catch { return $false } } 'Celery'
    $node = (Get-Command node -CommandType Application | Select-Object -First 1).Source
    Start-OwnedService 'web' ([bool]$Demo) $node (@((Join-Path $script:PlatformRoot 'scripts/serve-web.mjs'))+$modeArgs) (Join-Path $script:PlatformRoot 'apps/web') $config.web_port
    Wait-Ready { Test-Http "http://127.0.0.1:$($config.web_port)" } 'Vue'
    & "$PSScriptRoot/status-interview-platform.ps1" -Demo:$Demo
} catch {
    Write-Error $_ -ErrorAction Continue
    Write-Host 'Startup stopped. Existing services/data are retained; inspect status/logs. No destructive workaround.'
    exit 1
} finally { Pop-Location; $operationLock.ReleaseMutex(); $operationLock.Dispose() }
