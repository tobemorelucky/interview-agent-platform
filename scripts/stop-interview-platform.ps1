[CmdletBinding()]
param([switch]$Demo, [switch]$StopInfrastructure, [switch]$KeepInfrastructure)
. "$PSScriptRoot/platform-common.ps1"
if ($StopInfrastructure -and $KeepInfrastructure) { throw 'Choose either -StopInfrastructure or -KeepInfrastructure' }
$operationLock = Enter-PlatformLock ([bool]$Demo)
try {
foreach ($role in @('web','worker','api')) { Stop-OwnedService $role ([bool]$Demo) }
if ($StopInfrastructure) {
    foreach ($role in @('web','worker','api')) {
        if (Get-OwnedProcess (Get-StatePath $role (!$Demo))) { throw 'Another managed mode is running; stop it before stopping shared infrastructure' }
    }
    Invoke-Compose @('stop')
}
else { Write-Host 'Infrastructure retained (default); no containers or volumes removed' }
} finally { $operationLock.ReleaseMutex(); $operationLock.Dispose() }
