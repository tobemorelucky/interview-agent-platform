[CmdletBinding()]
param([switch]$Demo)
. "$PSScriptRoot/platform-common.ps1"
foreach ($tool in @('docker','uv','node','pnpm')) {
    if (!(Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Required tool missing: $tool (nothing installed automatically)" }
    Write-Host "$tool found"
}
foreach ($file in @($script:ApiPython,$script:WorkerPython,(Join-Path $script:PlatformRoot '.env'),(Join-Path $script:PlatformRoot 'apps/web/node_modules/vite/package.json'))) {
    if (!(Test-Path -LiteralPath $file)) { throw 'Dependencies/.env missing; follow README uv sync / pnpm install instructions' }
}
& $script:ApiPython --version
& docker info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker daemon unavailable' }
& docker compose version
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose unavailable' }
$config = Get-PlatformConfig ([bool]$Demo)
if (!$Demo -and !$config.model_key_present) { Write-Warning 'Model API key missing; Live agent calls will not work' }
if ($config.jwt_development_default) { Write-Warning 'Development JWT configured; prohibited outside development/test' }
foreach ($port in @($config.api_port,$config.web_port,$config.db_port,$config.redis_port,$config.milvus_port,9000,8080)) {
    Write-Host "Port $port listening: $(Test-Tcp '127.0.0.1' $port) (no process killed)"
}
Write-Host 'Environment checked; no software installed, credentials printed or system settings changed'
