# 展示环境公共设置：start / status 共用，避免三处各写一份连接串逻辑。
# 只做三件事：校验库名、把 .env 的 DATABASE_URL 换库名、合并大小写重复的环境变量。
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:ShowcaseRoot = Split-Path $PSScriptRoot -Parent

function Set-ShowcaseEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$Database,
        [string]$Bucket = 'interview-agent-showcase-r123'
    )
    if ($Database -notlike 'interview_agent_showcase_*') {
        throw "本入口只启动展示库（interview_agent_showcase_*）；当前参数：$Database"
    }

    # Git Bash 等终端会同时导出 https_proxy 与 HTTPS_PROXY。PowerShell 5.1 用
    # Start-Process 构建环境块时按大小写不敏感建字典，遇到同名双份会直接抛
    # "字典中的关键字已添加"。这里把同名的多个大小写合并成一个，值保持不变。
    foreach ($name in @('http_proxy', 'https_proxy', 'no_proxy', 'all_proxy')) {
        for ($attempt = 0; $attempt -lt 4; $attempt++) {
            $variants = @([Environment]::GetEnvironmentVariables('Process').Keys |
                Where-Object { $_ -ieq $name })
            if ($variants.Count -le 1) { break }
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
    }

    $root = $script:ShowcaseRoot
    $envPath = Join-Path $root '.env'
    $line = Get-Content -LiteralPath $envPath | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
    if (!$line) { throw '.env 缺少 DATABASE_URL' }
    $dsn = $line.Substring('DATABASE_URL='.Length).Trim()
    if ($dsn -notmatch '^(?<head>.+//[^/]+)/(?<db>[^?]*)(?<tail>\?.*)?$') {
        throw '无法解析 .env 中的 DATABASE_URL'
    }
    $env:DATABASE_URL = "$($Matches['head'])/$Database$($Matches['tail'])"
    $env:MODEL_LEDGER_DATABASE = $Database
    $env:MINIO_BUCKET = $Bucket
    return [pscustomobject]@{ Database = $Database; Bucket = $Bucket }
}
