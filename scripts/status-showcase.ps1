[CmdletBinding()]
param(
    [string]$Database = 'interview_agent_showcase_r123_20261117',
    [string]$Bucket = 'interview-agent-showcase-r123'
)
# 与 start-showcase 共用同一套目标库设置，状态里的 revision / provider / 端口
# 才不会来自 .env 的正式库。
. "$PSScriptRoot/platform-common.ps1"
$ProgressPreference = 'SilentlyContinue'
. "$PSScriptRoot/showcase-target.ps1"

$target = Set-ShowcaseEnvironment -Database $Database -Bucket $Bucket
Write-Host "展示库：$($target.Database)（下列 revision / provider 均取自该库配置）"
Write-Host ''

& "$PSScriptRoot/status-interview-platform.ps1"
