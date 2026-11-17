[CmdletBinding()]
param(
    [string]$Database = 'interview_agent_showcase_r123_20261117',
    [string]$Bucket = 'interview-agent-showcase-r123'
)
# 展示环境入口：复用原有的启动/停止/状态脚本，只把目标库切到独立展示库。
# 不修改 platform-common.ps1 的守卫逻辑；数据库名由显式参数给出并原样授权。
. "$PSScriptRoot/platform-common.ps1"
$ProgressPreference = 'SilentlyContinue'
. "$PSScriptRoot/showcase-target.ps1"

$target = Set-ShowcaseEnvironment -Database $Database -Bucket $Bucket

# local 模式只允许一组进程：否则 status/stop 会误管上一组，且两套进程连不同库会串台。
foreach ($role in @('api', 'worker', 'web')) {
    $statePath = Get-StatePath $role $false
    $running = $null
    try { $running = Get-OwnedProcess $statePath }
    catch { throw "PID 记录异常（$role）；请先运行 stop-dev.bat 再试" }
    if ($running) {
        throw "已有 local 模式服务在运行（$role，PID $($running.ProcessId)）。请先执行 stop-dev.bat，避免不同数据库串台。"
    }
}

Write-Host "展示库        : $($target.Database)"
Write-Host "模型账本库    : $($target.Database)"
Write-Host "对象存储桶    : $($target.Bucket)（不存在时自动创建，不写正式桶）"
Write-Host "基础设施      : 沿用已在运行的 postgres / redis / minio（不拉起 Milvus）"
Write-Host ''

& "$PSScriptRoot/start-interview-platform.ps1" -SkipInfrastructure -AuthorizeDatabase $Database
if ($LASTEXITCODE) { exit $LASTEXITCODE }

try {
    $login = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/v1/auth/login' `
        -ContentType 'application/json' -TimeoutSec 10 `
        -Body (@{ email = 'showcase-r123-user@example.com'; password = 'Showcase123!' } | ConvertTo-Json)
    $token = $login.data.access_token
    $jobs = (Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/v1/public-career/jobs?limit=100' `
        -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 10).data
    if ($jobs.Count -eq 50) { Write-Host "公开岗位自检    : $($jobs.Count) / 50 OK" }
    else { Write-Warning "公开岗位自检异常：$($jobs.Count) 个（期望 50）" }
} catch {
    Write-Warning "公开岗位自检失败：$($_.Exception.Message)"
}

Write-Host ''
Write-Host '访问地址：http://127.0.0.1:5173'
Write-Host '验收账号：showcase-r123-user@example.com / Showcase123!（0 简历 / 0 面试 / 0 记忆）'
Write-Host '关闭环境：stop-dev.bat      查看状态：status-dev.bat'
