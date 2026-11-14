# 第二十一轮：RC V1 数据安全与备份收口

> 真实模型 HTTP = 0。本轮不实施 0028、不动正式库、不做故障注入、不 push。
> 三个阶段：Git 成果保护 → 用户数据撤回与删除设计（只读审查）→ 阻断项分级。
> 交付一份免迁移的小修复（JD 用量归因，`473f6db`）；**数据删除方案本身未实施**，等你批准。

---

## 1. Git 成果保护

### 1.1 状态核实

| 项 | 值 |
|---|---|
| 分支 / HEAD | `master` / `473f6db`（本轮新增 1 个提交；轮初为 `8a795ee`） |
| 远端 | `origin = https://github.com/tobemorelucky/interview-agent-platform.git` |
| **远端最新状态（本轮已核实）** | `git ls-remote` 成功：`refs/heads/master = 411c345`。**与本地 `origin/master` 跟踪值一致**，即第二十轮的"schannel 无法确认"已消除：远端确实停在 `411c345`，本地领先 **99 个提交**（含本轮） |
| 工作区 | 仅 `.workbuddy/` 未跟踪（按约定不处理） |

### 1.2 Bundle 备份（已验证可恢复）

- 文件：`E:\work\code\python\backups\interview-agent-platform-rc-v1-20261114-1041-8a795ee.bundle`（**2.7 MB**，在仓库目录之外，`.runtime/` 重置不影响它）。
- 内容：`git bundle create --all`，含 `master`、`origin/master` 跟踪引用、`v0.9.0-finalization` 标签，以及两条 `refs/codex/turn-diffs/*`（Codex 侧 Agent 的内部快照引用；普通 push master 不会带它们，但备份里保留是安全的）。
- `git bundle verify`：**"The bundle records a complete history"**，sha1。
- **恢复演练**：`git clone --no-local <bundle> .runtime/backups/restore-check` 成功——
  `HEAD = 8a795ee`（与轮初一致）、`153` 个提交、`706` 个文件、工作树干净、`git fsck` 仅 2 个 dangling tree（对应未被检出的 codex 引用，无害）。

> 注意：bundle 固定在 `8a795ee`。本轮之后又产生了 1 个提交（`473f6db`），如需把最新提交也纳入备份，批准后重打一次即可（一条命令）。

### 1.3 完整历史敏感信息扫描（不止工作区）

| 扫描对象 | 方法 | 结果 |
|---|---|---|
| 待推送历史 `origin/master..master`（99 commits / 172 个改动文件 / 2.05 MB patch） | 全部新增行 grep：`sk-`、`Bearer <长token>`、`AKIA`、`-----BEGIN PRIVATE KEY`、`api_key=`、真实邮箱（排除 example/invalid/localhost）、手机号 `1[3-9]\d{9}` | **0 命中** |
| 全部引用（含 codex refs，7.49 MB patch） | 同上 + 路径级扫描：`.env`、`.pem`、`id_rsa`、`.runtime/`、`.workbuddy/`、`credential/secret/token/password` 命名的文件 | 路径只命中 `.env.example`（安全的示例文件）与 `docs/70_*token_estimate.md`（估算文档）；内容 0 密钥命中 |
| 二进制 | 历史中的 `sample_docs/resume_agent_rag_backend.docx` 属于远端已包含的 `7e79f7d`（阶段 3.1 的合成样本），**不在**未推送范围 | 无新增二进制 |
| `.gitignore` 边界 | `.env`、`.runtime/`、`dist/`、`.venv/`、`node_modules/` 均被忽略；仓库内只有 `.env.example` | runtime capture、样本、密钥不可能被普通 `git add -A` 带入 |

**结论**：待推送历史干净，可以随时 push；未 push（按约定）。TLS 证书验证未做任何改动，本轮远端探测成功，无遗留网络阻断。

---

## 2. 用户数据撤回与删除（只读审查）

### 2.1 个人数据真实存放点（67 张表逐一核对）

| 类别 | 位置 |
|---|---|
| **原始材料** | `resumes.raw_text` + MinIO 对象（`resumes.storage_key`）；`user_job_targets.raw_text / structured_json / requirements_json` |
| **原文逐字切片** | `resume_evidence.excerpt`（按固定切片），`resume_reports.summary_json` |
| **派生事实** | `candidate_profiles` → `candidate_profile_versions` → `candidate_facts` / `candidate_*_experiences` / `candidate_user_skills` ← `candidate_fact_evidence` |
| **会话与产物** | `interview_sessions` → `interview_messages.content`（**含用户回答原文**）、`interview_answers`、`interview_evaluations.artifact_json`、`interview_context_snapshots`、`final_interview_reports.coach_analysis_json` |
| **记忆** | `user_memory_items.content`、`capability_memories.projection`（+ versions）、`memory_proposals`、`user_skill_profiles`、`user_memory_events` |
| **经验挖掘** | `experience_source_items`、`experience_agent_runs/step_runs` 的 `input_json/output_json`（**含用户粘贴文本**） |
| **账本/审计** | `model_admissions`（**只有** route/model/token 数，无内容）、`model_budget_owners`、`audit_logs.before_json/after_json/metadata_json`（**可能内嵌资源内容片段**，含 `ip_address`）、`interview_agent_calls`（仅 id） |
| **进程外** | MinIO 对象、Redis broker（仅任务信封 resume_id，无内容）、Milvus（只存已发布公共知识，无个人数据）、`logs/*.log`（本地文件，本轮抽查无真实邮箱） |

### 2.2 外键依赖（决定"能不能删"）

关键约束（全部 `RESTRICT`，即**应用层必须先删子行或拒绝**）：

```
resumes ← resume_evidence(RESTRICT), candidate_profile_versions.source_resume_id(RESTRICT),
          candidate_profile_generations(RESTRICT), interview_sessions.resume_id(SET NULL)
resumes → resume_reports(CASCADE)

candidate_profile_versions ← candidate_facts / candidate_*_experiences / candidate_user_skills /
          candidate_fact_evidence(经 facts) / candidate_profile_corrections(RESTRICT) /
          candidate_profile_generations / candidate_profiles.current_version_id(RESTRICT) /
          interview_context_snapshots.candidate_profile_version_id(RESTRICT) / job_match_runs(RESTRICT)

user_job_targets ← interview_context_snapshots.job_target_id(RESTRICT)

interview_sessions ← final_interview_reports(RESTRICT), interview_performance_observations(RESTRICT),
          memory_proposals(RESTRICT)；子表（answers/evaluations/messages/session_questions/agent_calls/context_snapshots）CASCADE
users ← model_budget_owners(RESTRICT)（账本随用户保留）；resumes/user_job_targets/sessions/memory 等 CASCADE
```

### 2.3 七个问题的回答

**1）删除未使用的简历/JD，目前能否直接完成？**
能，且已经实现：`ResumeService.delete_resume`（`service.py:166`）在被画像/证据引用时返回 `409 RESUME_REFERENCED_BY_PROFILE`，否则删 MinIO 对象 + 报告（CASCADE）+ 行；`JobTargetService.delete`（`service.py:383`）被会话快照引用时 `409 JOB_TARGET_IN_USE`。**没有被引用的私人材料今天就能真正删掉（含 MinIO 文件）。**

**2）被 Session 引用时哪些外键阻止删除？**
- 删简历：`resume_evidence`、`candidate_profile_versions.source_resume_id`、`candidate_profile_generations`（应用层 409）；`interview_sessions.resume_id` 是 `SET NULL`——**即使删掉简历，旧会话不拦**，只是失去简历链接。
- 删 JD：`interview_context_snapshots.job_target_id`（应用层 409 + 数据库 RESTRICT）。
- 删会话：`final_interview_reports`、`interview_performance_observations`、`memory_proposals`（RESTRICT）。

**3）归档与停止未来使用能否在现有 Schema 下安全实现？**
- **简历：可以，零迁移**。`resumes.status` 是普通 `String(20)` 且**无 CHECK 约束**，新增 `'ARCHIVED'` 值只需代码；列表/画像入口过滤即可。
- **JD：需要一次小迁移**。`user_job_targets.parse_status` 在 0027 里有 `ck_user_job_targets_status` CHECK（四值枚举），加 `'ARCHIVED'` 必须改约束；或改用独立 `archived_at` 列。**未实施，待批准。**

**4）用户要求彻底清除时，哪些派生数据仍会保留？**
账本与审计类必须保留（否则历史面试的计费与合规无法解释）：`model_admissions`（无个人内容，只含 route/model/token）、`model_budget_owners`（随用户 RESTRICT）、`audit_logs`（**需要脱敏**而非保留原文，见 7）、`interview_agent_calls`（仅 id）。此外 `interview_sessions.resume_id` 的 `SET NULL` 意味着"清除简历"后旧会话仍在，只是不再指向它。

**5）如何避免删除破坏历史面试与账本完整性？**
- **顺序删除 + 引用即拒绝**：先证据 → 事实 → 版本 → profile（`candidate_fact_evidence` 挂在 facts 上，随事实删）；任何一级被 `interview_context_snapshots` / `job_match_runs` / `final_interview_reports` 引用即整体拒绝，降级为归档。
- **账本永不删**：`model_admissions` 按 `owner_key` 保留；删除用户时它因 `model_budget_owners RESTRICT` 天然保留。
- **会话完整性**：有报告的会话不允许物理删除；若必须清除内容，只做"内容置空 + 保留骨架"的脱敏（见 7）。

**6）如何实现幂等、可恢复的异步清理？**
- 用**删除凭据（purge token）而非直接删**：用户发起 → 服务生成 `purge_request`（幂等键 = user_id + 资源 id + 内容指纹），先做引用检查并**把资源置为归档态**（立即生效、可撤销）；
- 清理由 Celery 任务执行（与现有 `process_resume` 同一 worker），每步按"存在才删、删前记录行数"写 `purge_audit`；重投幂等：`_is_stale_task` 同款思路 + "资源已不存在即成功返回"（`process_resume_async` 对 COMPLETED 的跳过逻辑是现成范式）；
- MinIO 对象最后删（DB 事务提交成功后），失败仅告警重试，不阻塞 DB 清理；**顺序必须与上传相反**（先对象后行会丢溯源）。
- **这部分是方案，未实施**；需要一张 `purge_requests` 表（迁移）。

**7）哪些日志/审计要脱敏而不是删除？**
- `audit_logs.before_json / after_json / metadata_json`：删除资源时把其中的内容片段替换为 `{"redacted": true, "resource": id}`，保留 action/resource/ip/时间；
- `interview_messages` / `interview_evaluations` 等"会话内容"在归档场景可置空正文保留元数据（长度、时间、评分），保证报告统计仍成立；
- `model_admissions`、`interview_agent_calls`：本就无内容，不动；
- `logs/*.log`：本地文件加保留期（如 30 天轮转），不进库。

### 2.4 最小数据生命周期方案（供批准，未实施）

| 层 | 内容 | 需要迁移？ |
|---|---|---|
| **L1 归档（立即可做）** | 简历 `status='ARCHIVED'`（无 CHECK，纯代码）；列表/画像/训练入口过滤 ARCHIVED；MinIO 对象保留 | 否 |
| **L2 JD 归档** | `user_job_targets` 增加 `archived_at` 或扩 CHECK | **是（小迁移）** |
| **L3 清除（需设计）** | `purge_requests` 表 + Celery 清理任务 + `audit_logs` 脱敏函数；未引用资源走"真删除"（同现有 409 语义的相反分支），被引用资源走"脱敏保留骨架" | **是（小迁移）** |
| **L4 账号级** | 用户注销：CASCADE 表随 `users` 级联；`model_budget_owners`/`audit_logs` 保留并脱敏 | 需要补一个账号删除端点 |

**明确区分**：L1/L2 是"**归档隐藏**"——数据仍在库里、用户不可见、不计入任何展示；L3/L4 才是"**个人数据实际清除**"。演示阶段 L1 即够；真实用户开放前必须有 L3。

---

## 3. 其余发布阻断项分级（只读核对）

| 级别 | 项 | 核对结果 | 涉及文件 | 最小修复 | 测试方案 |
|---|---|---|---|---|---|
| **P1** | UNKNOWN 发送围栏只覆盖面试答题路径 | `governed_call` 崩溃后 admission 停在 `RESERVED`，下次 reserve 翻成 `UNKNOWN` 并保留计费；**简历/JD 路径没有 `INTERVIEW_OUTCOME_UNKNOWN` 式确认闸**——重试会再次真实发送 | `infrastructure/models/governance.py:44-62`（翻 UNKNOWN 逻辑）、`core_service.py:387/447`（面试围栏）、`resume/processor.py`、`job_target/service.py::extract` | 复用面试模式的"结果未知需确认"：给 resume/JD 任务记录"上次调用未决"状态，重试前要求显式确认（或让闸门在 UNKNOWN 未决时拒绝同资源重发） | 单测：模拟"reserve 后进程消失"，断言重发被拒、`UNKNOWN` 行保留计费 |
| **P1** | Celery 重投 | 已有两个现成防护：`_is_stale_task`（新 task 覆盖旧 task 时空转）+ **COMPLETED+报告存在即跳过重抽取**（`processor.py:55-67`）。残余缺口：**抽取中途崩溃**→重投会重跑并重复计费（数据不损坏，账本保留两笔） | `worker/tasks/resume_tasks.py:12`、`resume/processor.py:49-67`、`celery_app.py:26`（`acks_late=True` 无 `autoretry`） | 任务开始前检查"存在未决 UNKNOWN admission 且同 resume"→ 跳过模型、标记 FAILED；或对 resume 抽取加 `autoretry` 白名单（只对可安全重试的网络错误） | 故障注入单测（mock provider 中途抛错后重投，断言不二次发送） |
| ~~P1~~ **已修（本轮）** | `legacy_llm` 归因 | `governed_call` 无 scope 时兜底 `legacy_llm`；JD 路径从未设置 scope——第二十轮 6 次调用中 2 次记为 `legacy_llm`，无法按功能归因 | `infrastructure/models/governance.py:90`；本轮修复：`job_target/service.py::extract`（设置 `model_scope=(factory,"user:{id}",id,EXTRACT_ROUTE)`）+ `router.py` 传 `user.id` | **已实施（`473f6db`，无迁移）**；`resume_report` 路由本就正确 | 新增 `test_jd_extract_route_attribution.py`（2 条）；全量 932 passed / 0 failed / 1 skipped |
| **P0（部署前）** | 正式库 0025 → 0027 | 两步均**纯增量**：0026 建两张记忆表；0027 建 `user_job_targets` + 把快照三个公共 FK 改 nullable + CHECK `ck_interview_snapshot_source`（要么三公共齐/无 target，要么三空/有 target）。**前置条件**：生产 `interview_context_snapshots` 不能存在"三个公共 FK 不全"的行，否则 CHECK 建失败；downgrade 在存在私有快照时**主动拒绝**（不删历史） | `alembic/versions/0026_capability_memory.py`、`0027_user_job_targets.py:47-53` | 迁移前跑只读预检：`SELECT count(*) FROM interview_context_snapshots WHERE company_id IS NULL OR job_id IS NULL OR job_description_version_id IS NULL`，须为 0；先备份（pg_dump）再 `alembic upgrade`；回滚预案=0027 downgrade 拒绝时保持 0027 | 在生产数据副本库演练 upgrade/downgrade（本轮未做，未动生产库） |
| P2 | `user_job_targets` 状态 CHECK 限制归档值 | 见 §2.4 L2 | `0027_user_job_targets.py`（CHECK） | 随 L2 一起做 | 约束迁移用例 |

---

## 4. 本轮改动与测试

| 项 | 值 |
|---|---|
| 代码提交 | `473f6db`（JD 用量归因到 `public_research_extract`，无迁移，2 条新测试） |
| 全量回归 | `interview_agent_wb_r21_full_test_20261114`：**932 passed / 0 failed / 1 skipped**（既有 skip 仍为缺本地 realworld_eval bundle） |
| 迁移专项 | 本轮未改迁移，沿用第二十轮 10 passed 结论；未对生产库执行 |
| 备份 | bundle `8a795ee` 已验证可恢复；`473f6db` 未入 bundle |
| 真实模型 HTTP | **0** |

## 5. 待你批准的清单（本轮全部未实施）

1. **重打 bundle** 纳入 `473f6db`（一条命令，零风险）；
2. **L1 简历归档**（零迁移）；
3. **L2 JD 归档**（小迁移）；
4. **L3/L4 清除方案**（`purge_requests` + 脱敏）；
5. **UNKNOWN 未决围栏**推广到简历/JD 路径；
6. **0025→0027 生产迁移**（先跑只读预检 + 副本演练）。

完成后停止，等待你决定。
