# 正式库 0026 发布检查：待单独授权

**本轮没有执行正式迁移。下文含写操作的步骤仅供后续获授权的发布窗口执行，不要作为演示启动步骤。**

## 1. 当前审查结论

- 正式库 `interview_agent` 仍0025；新功能仅在0026隔离库演示。
- migration：`apps/api/alembic/versions/0026_capability_memory.py`，down_revision=0025。新增2张能力表，6个索引含主键/唯一约束，2条 CASCADE 外键；不改旧字段、不回填历史、不改 Snapshot。
- 用户/主题唯一、能力/版本唯一；旧 MemoryProposal、CandidateFact 和 Session 不迁移语义。
- 用户表外键创建曾实测 ShareRowExclusiveLock，可能阻塞写入或等待长事务；不是零锁迁移。两单列索引与唯一索引左前缀重叠，暂不因此改 migration。
- downgrade 会删除能力状态和全部版本；运行产生能力数据后不能自动当成无损回滚。
- 当前代码默认新自适应 Session 会使用能力表，**没有降级到0025运行新V2.2流程的保证**。发布需协调代码与schema，不能先把最新代码当作旧schema上的新功能入口开放。
- 既有备份 `E:\work\backups\interview-agent-platform\v211-20261023-144352-e617d0\interview_agent.dump`，1,277,943 bytes，SHA256 `19931B3EBAD63C863AD0996F7BCFF73346BBAD4A63AD0C31F883DFB9D74C16AB`。此前已在全新隔离库实际恢复，旧表内容相同，0025→0026演练通过（52文档）。**旧备份不能代替发布窗口的新备份**。
- 本轮再次检查备份存在/大小/hash，并离线生成0025→0026 SQL；离线输出有显式 BEGIN/COMMIT、两表/索引/外键与 Alembic revision UPDATE，没有旧表回填。

## 2. 发布前必须确认

1. 用户明确授权正式库0026迁移，确定维护窗口、可接受锁等待/停写时间。
2. 原容器ID/镜像/原卷、实际正式库名与账户确认，Engine/pg_isready正常，磁盘空间可用。
3. 暂停本项目业务写入及可能运行的相关 Worker；不能停止无关容器/服务。等待项目中运行中模型任务结束，避免半途升级；保证单一迁移执行者，不能并行跑其他迁移命令。
4. 正式库恰好0025，新能力表不存在；若revision不符或出现半成品，停止，不猜修复、不覆盖。
5. 做新的逻辑备份，校验 SHA256、pg_restore清单，并可选恢复至全新临时审查库；不得对正式库试恢复。
6. 固定发布代码、0026 hash、迁移SQL、备份manifest及操作日志。维护旧代码快照以支持故障隔离，但不自动 commit。
7. 明确失败方案：迁移事务失败回滚并停止；迁移成功后业务异常先停写分析，不直接 downgrade 删除新数据。

## 3. 可执行操作顺序（仅授权后）

在仓库根目录 PowerShell 执行。所有外部命令检查 `$LASTEXITCODE`；示例不会输出数据库密码。

### A. 只读前置核对

```powershell
$pg = 'interview-agent-platform-postgres-1'
docker inspect $pg --format '{{.Id}} {{.Config.Image}} {{range .Mounts}}{{.Name}}:{{.Destination}} {{end}}'
docker exec $pg sh -c 'pg_isready -U "$POSTGRES_USER"'
```

SQL 使用 `docker exec -i ... psql` 管道方式，避免 PowerShell 与 shell 多层引号：

```powershell
$checkSql = @'
BEGIN READ ONLY;
SELECT current_database(), version_num FROM alembic_version;
SELECT to_regclass('public.capability_memories'), to_regclass('public.capability_memory_versions');
COMMIT;
'@
$checkSql | docker exec -i $pg sh -c 'exec psql -X -U "$POSTGRES_USER" -d interview_agent -v ON_ERROR_STOP=1'
if ($LASTEXITCODE -ne 0) { throw 'Preflight failed' }
```

人工核对输出必须为正式库0025、两表NULL后继续。不得仅凭命令成功就执行。

### B. 新备份，不覆盖旧文件

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupDir = "E:\work\backups\interview-agent-platform\release-0026-$stamp"
New-Item -ItemType Directory -Path $backupDir -ErrorAction Stop | Out-Null
$dumpName = "interview_agent-$stamp.dump"
docker exec $pg sh -c 'exec pg_dump -U "$POSTGRES_USER" -d interview_agent -Fc -f "$1"' sh "/tmp/$dumpName"
if ($LASTEXITCODE -ne 0) { throw 'pg_dump failed' }
docker cp "${pg}:/tmp/$dumpName" (Join-Path $backupDir $dumpName)
if ($LASTEXITCODE -ne 0) { throw 'Backup copy failed' }
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $backupDir $dumpName)
docker exec $pg pg_restore -l "/tmp/$dumpName"
if ($LASTEXITCODE -ne 0) { throw 'Backup archive unreadable' }
```

将 hash、文件大小、时间与实际数据库名记录到独立manifest。清单可读不是完整恢复保证；若发布要求重新恢复验证，应选择**新名称**隔离库，决不覆盖已有验收库或正式库。

### C. 生成准确 Alembic SQL，再有界执行

当前 Alembic env.py 自己创建 asyncpg 连接，不接收外部连接；不能假设 PGOPTIONS 就会生效。这里选 Alembic 标准 offline SQL，再交给 PostgreSQL原生客户端执行，以明确连接级锁/语句时限。**不是另写一套schema SQL。**

```powershell
Push-Location apps/api
try {
  $lines = & uv run --project . --no-sync python -m alembic upgrade 0025:0026 --sql
  if ($LASTEXITCODE -ne 0) { throw 'Offline render failed' }
} finally { Pop-Location }
$sql = $lines -join [Environment]::NewLine
if ($sql -notmatch '^BEGIN;' -or $sql -notmatch "version_num='0026'") { throw 'Unexpected SQL; inspect before proceeding' }
# 防止预检后 revision 改变；检查在同一DDL事务中执行。
$guard = @'
BEGIN;
DO $$
BEGIN
  IF (SELECT version_num FROM alembic_version) IS DISTINCT FROM '0025' THEN
    RAISE EXCEPTION 'Expected revision 0025';
  END IF;
END $$;
'@
$sql = $sql.Replace('BEGIN;', $guard)
$boundedSql = "SET lock_timeout='3s';`nSET statement_timeout='30s';`n" + $sql
$sqlFile = Join-Path $backupDir '0025-to-0026.sql'
[IO.File]::WriteAllText($sqlFile, $boundedSql, [Text.UTF8Encoding]::new($false))
Get-FileHash -Algorithm SHA256 -LiteralPath $sqlFile
# 先人工审阅SQL，确认仅0026操作、没有清库/旧数据改写，再执行：
docker cp $sqlFile "${pg}:/tmp/release-0026-$stamp.sql"
if ($LASTEXITCODE -ne 0) { throw 'SQL copy failed' }
docker exec $pg sh -c 'exec psql -X -U "$POSTGRES_USER" -d interview_agent -v ON_ERROR_STOP=1 -f "$1"' sh "/tmp/release-0026-$stamp.sql"
if ($LASTEXITCODE -ne 0) { throw 'Migration failed; STOP. Do not retry/reset automatically.' }
```

发布窗口还应给整个操作进程设置外层截止条件，并保留 stdout/stderr；不要使用无界循环。单条 statement_timeout 与锁等待限制不是全批次计时器。失败时断开连接会回滚未提交DDL，需重新只读确认revision；不能直接重跑或改migration迁就数据库。

这些步骤本轮仅离线渲染过 SQL，**没有执行 C 的正式写入，也未声称整个未来发布命令已经在线验收**。

## 4. 升级后检查清单

仍先保持业务写入暂停：

- `SELECT version_num FROM alembic_version` =0026；两表存在；所有相关FK/unique convalidated=true。
- 对比备份前后旧表 row counts/内容指纹，除 Alembic revision外不应因migration改变；新表最初应为空。
- 通过 `review_v22_migration.py --database interview_agent --output .runtime/acceptance/<唯一名称>.json` 生成只读证据。该脚本读取全表，当前小库适用，生产大表需评估开销。
- 原账号登录、简历/Profile、旧面试/报告、MemoryProposal审批历史可读取，资源归属仍隔离。
- 只读访问能力 API 可为空，没有旧观察不等于失败，不为展示补写“已验证”。
- 新建真正V2.2 Session的事务、Snapshot和能力投影写入属于额外业务smoke；是否使用收费模型需另行授权，不能把 migration 授权当成费用授权。
- 核对无未结束DDL锁或长事务；确认API/相关Worker版本后再恢复本项目写入。
- 有异常先停写，保留日志和新备份；不清表、不重建容器/卷，不自动执行 downgrade 或恢复覆盖。

## 5. 回滚的现实限制

事务尚未提交：原生PostgreSQL事务回滚，应保持0025；仍需实际查询确认。

已经0026但尚未产生新数据：评估旧代码兼容和是否需要保留新表，不必为了版本号好看立刻删表。

已经产生能力数据：downgrade会删两表；全库恢复备份则丢失备份后其他业务写入。均需单独批准和停写方案，不存在自动无损回滚承诺。

正式发布还欠：**迁移授权、维护窗口、新备份、实际执行、升级后业务验收**。本轮不代为执行。
