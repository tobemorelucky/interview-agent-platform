[CmdletBinding()]
param([switch]$Demo)
. "$PSScriptRoot/platform-common.ps1"
$config = Get-PlatformConfig ([bool]$Demo)
foreach ($role in @('api','worker','web')) {
    try { $owned = [bool](Get-OwnedProcess (Get-StatePath $role ([bool]$Demo))) } catch { $owned = $false; Write-Warning 'Ownership mismatch; process will not be managed' }
    Write-Host "$role owned PID alive: $owned"
}
$checks = [ordered]@{
    PostgreSQL_TCP = (Test-Tcp $config.db_host $config.db_port)
    Redis_TCP = (Test-Tcp $config.redis_host $config.redis_port)
    MinIO_HTTP = (Test-Http $config.minio_health_url)
    Milvus_TCP = (Test-Tcp $config.milvus_host $config.milvus_port)
    Milvus_local_HTTP = (Test-Http "http://$($config.milvus_host):9091/healthz")
    SearXNG_HTTP = (Test-Http $config.searxng_url)
    FastAPI_health = (Test-Http "http://127.0.0.1:$($config.api_port)/api/v1/health")
    Vue_HTTP = (Test-Http "http://127.0.0.1:$($config.web_port)")
}
foreach ($entry in $checks.GetEnumerator()) { Write-Host "$($entry.Key): $($entry.Value)" }
try { Invoke-Delivery 'redis-ping' ([bool]$Demo) | Write-Host } catch { Write-Host 'Redis ping: unavailable' }
try { Invoke-Delivery 'worker-ping' ([bool]$Demo) | Write-Host } catch { Write-Host 'Celery ping: unavailable' }
try { Invoke-Delivery 'revision' ([bool]$Demo) | Write-Host } catch { Write-Host 'DB revision: unavailable' }
Write-Host "Environment: $($config.app_env) / $($config.mode)"
Write-Host "Configured provider: $($config.provider); Primary: $($config.primary_model); Fallback: $($config.fallback_model)"
Write-Host 'Search is frozen; SearXNG health does not imply Live Search acceptance'
