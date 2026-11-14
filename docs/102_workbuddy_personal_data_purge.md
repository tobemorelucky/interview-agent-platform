# 第二十三轮：L3 简历与私人 JD 的个人数据实际清除（隔离环境限定）

> 按有条件批准执行：**仅在隔离数据库与合成用户数据上实现、故障注入与验收**。
> 未对正式数据库或历史 Live 数据执行清除；未开放真实用户的永久删除入口；L4 账号删除未实施。
> 真实模型 HTTP = **0**（全部 Replay / Mock）。生产库仍 `0025`，未自动迁移。Coach Harness 保持 OFF。
>
> **本轮最重要结论**：数据库用 7 类 `immutable_history` / `m4_snapshot_immutable` / `m5_*_guard` /
> `job_match_*_guard` 触发器把已发布历史设为**不可变**。按"不要为了完成这一轮破坏已有系统"的
> 明确指示，本轮**不绕过触发器**：被引用的资料一律进入 `BLOCKED`（零写入、完全可恢复），
> 只有无历史引用的资源才执行真正的物理清除并标 `COMPLETED`。因此**本轮尚未达到真实用户
> 清除功能的上线条件**，需要先批准 §10 的迁移与归属方案。

---

## 1. Git 成果保护

| 项 | 结果 |
|---|---|
| 轮初 HEAD | `71e9d8f`（`master`，工作区仅 `.workbuddy/` 未跟踪），与交接一致 |
| 远端 | `git ls-remote` **本轮成功**：远端 `master = 411c345`，与本地跟踪值一致；本地领先 **109** 提交 |
| 新 Bundle | `E:\work\code\python\backups\interview-agent-platform-rc-v1-20261114-1528-71e9d8f.bundle`（**2.7 MB**，`--all`） |
| 验证 | `git bundle verify` → "The bundle records a complete history"；`git clone --no-local` 到独立目录 `.runtime/backups/restore-check-r23` 实际恢复：`HEAD=71e9d8f`、**163 commits**、**713 files**、工作树干净（`git fsck` 仅两个 codex 快照的 dangling tree，非损坏） |
| 旧 Bundle | `...-1041-8a795ee.bundle`、`...-1432-c134a99.bundle` **均保留**，未覆盖 |
| 异地容灾 | **不具备**：三个 Bundle 与仓库同在 E: 盘。未 push、未合并、未 rebase、未 reset、未 clean |
| 保护文件 | `AGENTS.md`、`CLAUDE.md`、`.claude/`、其他编码 Agent 记忆均未触碰 |

> Bundle 固定在轮初 `71e9d8f`；本轮提交未入内，批准后可再打一次。

---

## 2. 清除依赖图（重新核实 67 张表）

### 2.1 内容复制关系（本轮最关键的发现）

`interview_context_snapshots.context_json` 不是引用，而是**完整副本**：`build_context()`
把 `profile`（画像全字段）、`normalized`、逐条 `candidate_facts`、含简历逐字切片 `excerpt`
的 `candidate_evidence`、JD 派生 `requirements`、`company_name` / `job_title`、以及记忆层
全部 `deepcopy` 进快照。因此**删掉 `resumes` / `user_job_targets` 源行并不能清除个人内容**。
此外 `interview_messages.content`、`interview_session_questions`、`interview_evaluations.artifact_json`、
`final_interview_reports.coach_analysis_json`、`memory_proposals`、`job_match_runs.input_snapshot_json`
都可能含原文或可识别派生内容。

外键上，快照被 `interview_sessions.context_snapshot_id` 与
`final_interview_reports.context_snapshot_id` **RESTRICT**；画像版本被快照与
`job_match_runs` RESTRICT；这些行**不能删**，只能擦内容。

### 2.2 五类标注

| 类别 | 位置 | 本轮处理 |
|---|---|---|
| **1 必须删除** | `resumes` / `user_job_targets` 行、MinIO 对象、`resume_evidence`、`resume_reports`、`candidate_facts` / `*_experiences` / `user_skills` / `fact_evidence`、`candidate_profile_versions` / `corrections` / `generations`、`user_confirmed_evidence`、`job_match_*` | 无历史引用时**物理删除**；有历史引用时被触发器阻断 |
| **2 必须删除个人字段或脱敏** | 快照 `context_json`、`interview_messages`、`interview_answers`、`interview_session_questions`、`interview_evaluations`、`final_interview_reports`、`interview_performance_observations`、`memory_proposals`、`audit_logs.before/after/metadata_json`；用户级聚合 `capability_memories` / `user_memory_items` / `user_skill_profiles` / `chat_*` | 声明在 `ERASE_PLAN` / `USER_LEVEL_PLAN`，按资源血缘或所属用户擦除；**有历史引用时触发 §2.3 阻断，不执行** |
| **3 可保留不含个人内容的最小审计** | `model_admissions`（route/model/token）、`model_budget_owners`、`interview_agent_calls`、`audit_logs` 骨架（action/resource/time/ip）、各表 id/FK/时间戳/枚举/token 计数、`purge_requests` 自身 | 保留；验证阶段核对账本行数不变 |
| **4 备份/外部系统，不能立即清除** | PostgreSQL 物理备份与 WAL、MinIO 版本控制/副本、外部模型服务端 prompt 日志、本地 `.runtime` Capture/Replay 与 `logs/*.log`、历史 Live 数据库 | 如实写入 `scope_json.external_boundaries` 与 UI 提示，**不宣称已清除** |
| **5 当前无法确定，必须阻断成功声明** | 触发 UNKNOWN 结果的模型调用（已发出但结果未知）、无资源外键的用户级聚合（无法归属到单份简历/JD）、外部服务是否留存 | `UNKNOWN` → `BLOCKED`（`UNRESOLVED_MODEL_OUTCOME`）；无法归属 → `BLOCKED`（`UNSAFE_DEPENDENCY`）；**绝不标 COMPLETED** |

公共知识链路（`claims`、`claim_evidence`、`tool_invocations`、`experience_source_items`、
`experience_agent_runs`、`source_snapshots`）经核实**无用户外键**，属于公共 Research 数据，
不在个人清除范围内，保持不变。

### 2.3 决定性阻断：数据库不可变触发器

| 触发器 | 保护对象 | 行为 |
|---|---|---|
| `immutable_history` | `candidate_facts` / `*_experiences` / `user_skills` / `fact_evidence` / `candidate_profile_versions` / `corrections` / `resume_evidence` / `user_confirmed_evidence` | `BEFORE UPDATE OR DELETE` 无条件抛异常 |
| `m4_snapshot_immutable` | `interview_context_snapshots` | `BEFORE UPDATE` 无条件抛异常 |
| `m5_report_guard` / `m5_observation_guard` / `m5_proposal_guard` | 报告、观察、记忆提案 | 已完成内容不可变 |
| `job_match_run_guard` / `job_match_candidates_guard` / `job_match_recommendations_guard` | 匹配历史 | 禁止删除与输入变更 |

**决策**：事务内 `ALTER TABLE ... DISABLE TRIGGER USER` 技术上可行且可完整回滚（本轮已实测），
但等于在清理窗口内关闭账本/报告的完整性保证，属于"为了完成这一轮破坏已有系统"。
因此**不采用**。改为 `preflight_blockers()` 在任何写入**之前**做只读检测，命中即
`BLOCKED`，诊断里只写表名与触发器名，不含任何个人原文。这样 BLOCKED 是**零写入、
完全可恢复**的：原始资料原封不动，围栏仍然生效。

---

## 3. L3 准确清除范围与状态机

### 3.1 迁移 0029（仅隔离库）

`purge_requests` 单表，**不改动任何现有表结构、外键或约束**。列：
`user_id`、`resource_type`(CHECK RESUME/PRIVATE_JD)、`resource_id`、`idempotency_key`、
`status`(CHECK 七态)、`policy_version`、`scope_json`、`completed_steps`、`object_key`
（服务端派生、不暴露给客户端）、`error_code`、`error_diagnostic`、`attempts`、
`residual_json`、以及 `created/fenced/cleaning/verifying/completed_at` + `created/updated_at`。

三个唯一/索引约束：`uq_purge_user_resource`（同用户同资源幂等）、
`uq_purge_user_idempotency_key`（**含 user_id**，不同用户可用同一 key 且互不可达）、
`idx_purge_status` / `idx_purge_user`。

`down_revision = 0028`，`upgrade` 建表、`downgrade` 只删表。已实测
`0027 → 0028 → 0029` 升级与安全回滚（0028 的 `archived_at`、0027 的
`parse_status` CHECK 在回滚后原样保留）。

### 3.2 状态机与围栏

```
REQUESTED → FENCED → CLEANING → VERIFYING → COMPLETED
                 ↘ FAILED（可恢复，保留 completed_steps 断点）
                 ↘ BLOCKED（依赖阻断，需额外审批）
```

- **围栏即 `purge_requests` 行本身**：受理那一刻起，任何状态（含 FAILED / BLOCKED / COMPLETED）
  都让该资源退出所有新业务。咽喉点覆盖 `build_context`（会话/训练）、`private_target`、
  画像生成端点、匹配 `create`/`generate`、JD 的 `read`/`owned`（解析/确认/编辑/推荐/匹配）、
  简历归档/恢复/旧删除路径。
- **直传 id 不可绕过**：`candidate_profile_version_id` 会反查 `source_resume_id` 再验围栏；
  `resume_id`、`job_target_id` 直接验围栏。
- **任务侧**：`ResumeProfileAgent.run` 入口返回 `SKIPPED/RESOURCE_PURGED`；
  `process_resume_async` 与每个写回点（`_mark_processing_started`、`_save_raw_text`、
  `_update_stage`、`_save_report_and_complete`、`_mark_failed_best_effort`）在**复用同一
  DB session** 的前提下检查围栏，防止旧任务写回个人内容。
- **UNKNOWN 不重发**：清除流程零模型调用；若该用户存在 `UNKNOWN` 的 `model_admission`，
  直接 `BLOCKED`，不重试也不宣称清除完成。
- **PROCESSING 简历**：受理**不返回 409**（不像归档），围栏立即生效，正在跑的任务在下一个
  写回点停止。用户不会陷入"永久 409 且无法处理"。
- **清除期间恢复归档**：`restore` 检查围栏，一律 409。

### 3.3 兼容 0025（无 `purge_requests` 表）

所有围栏探针先用 `to_regclass('public.purge_requests')`，表不存在时直接返回"未围栏"。
**正式库 0025 上的正常面试/上传链路不会因本轮改动报错**。

---

## 4. PostgreSQL / MinIO / Redis 清理顺序与失败恢复

**顺序不可颠倒**：数据库内容先擦除（事务可回滚）→ MinIO 对象删除（不可逆）→
Redis（无个人内容，短 TTL）→ 独立验证 → `COMPLETED`。

| 步骤 | 记录 | 失败行为 |
|---|---|---|
| 0 `FENCED` | `fenced_at` | 任何状态都保持围栏 |
| 1 `db_content_erased` | `completed_steps` | 整段事务；异常 rollback → `FAILED`，零半删 |
| 2 `minio_object_removed` | `completed_steps` | `MINIO_OBJECT_REMOVE_FAILED` → `FAILED`，**不宣称完成**；重投只重试此步 |
| 3 `redis_cache_cleared` | `completed_steps` + `residual_json.redis` | 失败 → `FAILED` 可重试；Redis 仅存限流计数/锁/Celery 信封，不含个人内容 |
| 4 `independently_verified` | `completed_steps` | 任一检查失败 → `FAILED`，**绝不写 COMPLETED** |

- 幂等：每步 `completed_steps` 持久化，重投跳过已完成步骤；所有 UPDATE 覆盖为固定
  `[PURGED]` / `{"erased":true}` / `[]` 标记，重跑结果一致；DELETE 用"不存在即成功"。
- MinIO `remove_object` 对 `NoSuchKey` 视为成功（provider 既有行为）。
- 独立验证还会用**独立对象探针**复查对象确实不存在——即使删除器谎报成功也会被拦下（专项用例）。
- 任务参数**只传 `purge_request_id`**；`user_id` / `resource_id` / `object_key` 全部由 worker
  从数据库重新读取，不接受任何请求参数或工具参数覆盖，因此重投不可能执行跨用户清理。
- `acks_late=True` 下 worker 崩溃会重投；专项模拟"DB 步完成后崩溃 → 全新 session 重投"
  ，验证从断点续做并最终 COMPLETED。

**日志与隐私**：`error_diagnostic` 只写固定 code + 表名/触发器名；`scope_json` 不落客户端；
`object_key` 不暴露、不写日志；清理日志不写原始文件、JD 原文或完整 prompt。

---

## 5. 审计脱敏与账本处理

- `audit_logs.before_json / after_json / metadata_json / error_message` 覆盖为
  `{"erased":true}` / `[PURGED]`；**保留** `action`、`resource_type`、`resource_id`、
  `actor_user_id`、`ip_address`、`status`、时间戳。
- `model_admissions`、`model_budget_owners`、`interview_agent_calls` **完全保留**
  （本就只有 route/model/token/id，无内容）；验证阶段核对清除前后行数不变。
- 用户级聚合按 `user_id` 脱敏，但**仅归属用户自己的行**；其他用户数据零触碰。
  UI 与 `scope_json` 明确写出这一保守取舍。

---

## 6. 前端范围（执行默认关闭）

- 两页都有 `归档` / `申请彻底清除` / `清除处理中` / `清除完成（约定范围）` /
  `清除失败或被阻断` 五态区分；状态来自 `/api/v1/purges`。
- 文案逐字区分归档与清除，**不出现"永久删除成功"**，不在完成前声称清除完成；
  COMPLETED 的文案明确写出"历史会话骨架、非内容型模型账本、外部备份与模型服务端日志不在
  可清除范围内"。
- 申请必须用户显式点击并二次确认；前端**没有**执行接口，也没有执行开关。
- **执行是服务端三闸**：`purge_execution_enabled`（默认 `False`）+ `purge_test_user_ids`
  白名单 + `db_targets` 判定必须是隔离 TEST/E2E/MIG 库（PRODUCTION / ARCHIVED_LIVE /
  DEMO / SYSTEM 一律拒绝）。E2E 验收时才通过环境变量为合成用户 id=1 开启；
  生产与历史 Live 环境无法靠前端按钮启用。

---

## 7. 测试、故障注入与浏览器证据

| 项目 | 结果 |
|---|---|
| 隔离库 | 新建 `interview_agent_wb_r23_test_20261114`、`..._full_test_20261114`、`..._final_test_20261114`、`..._e2e_20261114`、`interview_agent_v21_wb_r23_mig*_20261114`，全部迁到 `0029`；正式库 `0025` 未动 |
| L3 专项 | **13 passed / 0 failed**（`test_purge_lifecycle.py`）：未使用简历真删除、历史引用简历 BLOCKED、历史引用 JD BLOCKED、未使用 JD 真删除、跨用户隔离、幂等键、MinIO 失败重试、对象谎报成功被验证拦下、worker 崩溃断点恢复、旧任务重投不复活、审计脱敏+账本完整、默认关闭与不误报成功、pre-0029 兼容、公共数据/他人数据不受影响 |
| 全量业务回归 | **966 passed / 0 failed / 1 skipped** |
| 迁移专项 | **12 passed / 0 failed**（含 `test_11_v29_purge_migration.py`：0027→0028→0029 升级、0029→0028→0027 回滚、真实请求行往返） |
| 前端 | `vue-tsc --noEmit` 通过；`vite build` 通过；单测 **7 passed** |
| 故障注入 | MinIO 删除抛错 → FAILED 且不记录该步 → 重投成功；独立探针仍见对象 → 验证失败；DB 步完成后"崩溃" → 新 session 续做 |
| Canary 验证 | 全库扫描所有 text/varchar/json/jsonb 列，清除后合成 canary **0 命中**（账本 `call_key` 不含 canary） |
| 真实 Chrome | E2E 库 `interview_agent_wb_r23_e2e_20261114` + Replay 模型 + 出站 HTTP 拦截；合成用户 id=1；`vue-tsc`/build 通过。详见 §7.1 |
| 真实模型 HTTP | **0** |

### 7.1 真实 Chrome 合成用户验收（实测）

启动 `serve_offline_acceptance.py api/worker`（Replay、出站 HTTP 拦截）、`serve-web.mjs`、
真实 Chrome 157（CDP 9222）。`GET /openapi.json` 确认 `/api/v1/purges/capability`、
`/api/v1/purges`、`/api/v1/purges/{request_id}` 三个路由已挂载。仅通过**服务端环境变量**
`PURGE_EXECUTION_ENABLED=1` + `PURGE_TEST_USER_IDS=1` 为 E2E 库的合成用户开启执行，
前端按钮本身不含执行能力。

脚本 `.runtime/ui/r23_ui.mjs`（自包含 CDP 客户端）以真实 UI 表单登录后逐项核对，结果：

| 检查 | 结果 |
|---|---|
| 「归档仅停止用于新业务」「申请彻底清除」两种入口同时可见 | ✅ |
| 未出现「永久删除成功」「隐私数据已清除」 | ✅（申请前后均为 true） |
| 归档与清除语义说明、外部边界提示（"不在本服务可立即清除的范围内"） | ✅ |
| 五态能力横幅（本 E2E 显示"仅隔离测试环境启用清除执行（仅受控合成测试用户可用）"） | ✅ |
| 点击「申请彻底清除」并二次确认后，状态徽标显示「清除申请已受理」 | ✅ |
| JD 页同样具备生命周期说明与「申请彻底清除」入口 | ✅ |

真实 Celery 链路同步核对：worker 日志显示
`Task purge_personal_data[...] received` → `purge task done request_id=1 status=BLOCKED`；
数据库里该请求为 `status=BLOCKED, error_code=UNSAFE_DEPENDENCY, completed_steps=[]`，
诊断只列触发器与表名（`immutable_history=candidate_facts,...`），**无任何个人原文**。
这与 §8 的阻断设计完全一致：合成用户已有完整画像/快照历史，因此零写入 BLOCKED。

截图：`r23_01_resume_lifecycle.png`、`r23_02_resume_purge_state.png`（状态徽标 + 文案）、
`r23_03_jd_lifecycle.png`。验收进程已全部停止，端口 8000/5173/9222 关闭。

### 7.2 全量回归抓到并修复的真实缺陷

1. **跨类型资源 ID 误用（严重）**：清除 `PRIVATE_JD` 时，`resume_evidence` 作用域曾误用
   `resume_id = :rid`，在全量库里命中了**同一用户另一份简历**的 11 行 evidence。
   已改为 `resume_rid` / `target_rid` 分离，且 `audit_ids` 增加 `resource_type` 条件。
   修复前正是 `preflight_blockers` 的 BLOCKED 阻止了误删。
2. **processor 写回点新开 DB session**：导致 Windows 上跨事件循环 asyncpg 句柄报错。
   改为复用已有 session（同时减少连接数）。
3. 迁移测试假设库从 base 开始，组合运行时前面用例已到 0029；改为状态无关
   （先 `upgrade head` → `downgrade 0027` → 逐步升级）。

---

## 8. 被历史 Session 引用的资料如何处理（明确回答）

**本轮不清除，明确 `BLOCKED`。** 原因不是"想保留报告可读性"，而是数据库用触发器把
已发布历史设为不可变；绕过它会破坏报告与账本的完整性保证。BLOCKED 时：

- 零数据写入，`resumes.raw_text` / JD 原文 / 快照 / Evaluation / Coach **原封不动、可完全恢复**；
- 围栏立即生效，该资料已停止一切新画像、面试、训练、匹配，且不能通过恢复归档重新启用；
- `error_diagnostic` 精确列出被哪个触发器钉住（如 `m4_snapshot_immutable`），
  为后续审批提供可执行的输入。

要真正清除这类资料，需要 §10 的**单独批准**：一条为清除路径开窄例外的迁移
（例如在事务级 purge 上下文中允许把指定列写成固定擦除标记），并配套回归证明
正常业务路径的不可变性不受影响。本轮未实施，避免破坏已有系统。

---

## 9. 仍然保留的个人数据 / 无法控制的边界

| 边界 | 状态 |
|---|---|
| 历史 Session 骨架、快照、Evaluation、Coach、Memory（BLOCKED 场景） | **原文仍在**，需额外审批 |
| `model_admissions` / `model_budget_owners` / `interview_agent_calls` | 保留（无个人内容的最小账本） |
| `audit_logs` 骨架（action/resource/ip/时间） | 保留；内容已脱敏 |
| 用户级聚合记忆（COMPLETED 场景） | 内容已按所属用户脱敏，但**行仍在** |
| PostgreSQL 物理备份 / WAL / PITR | **可能仍含原文**，本服务无法立即清除；需备份轮转/保留期策略 |
| MinIO 版本控制 / 桶复制 / 对象副本 | 如启用则不在控制范围，需运维核对 |
| 外部模型服务已收到的 prompt | **不能声称已清除**；历史真实调用已在供应商侧，无验证依据 |
| 本地 `.runtime/**/capture`、Replay 文件、`logs/*.log`、历史 Live 数据库、Git Bundle | 不清除；需独立的文件保留期与离线介质销毁流程 |
| 浏览器 localStorage / 用户本机缓存 | 不受本服务控制 |

---

## 10. 是否已达到真实用户清除功能的上线条件：**否**

必须先完成、并单独审批：

1. **不可变历史例外迁移**：为 `immutable_history` / `m4_snapshot_immutable` /
   `m5_*_guard` / `job_match_*_guard` 增加仅在 `purge_request=CLEANING` 事务上下文下
   允许把指定列写成固定擦除标记的窄例外；并证明正常业务路径的不可变性不受影响。
2. **用户级聚合归属**：给 `capability_memories` / `user_memory_items` / `chat_*` 增加
   资源级来源（或改为账号级 L4 一并清除），否则无法安全按单份资料切分。
3. **外部边界流程**：备份轮转、模型服务端数据留存说明、Capture/Replay/日志保留期。
4. **生产迁移与演练**：`0025 → 0029` 前置预检（0027 的 CHECK 要求三个公共 FK 齐全）、
   隔离演练、故障注入到正式流程。
5. **执行开关与审计**：当前 `purge_execution_enabled=False` + 白名单 + 隔离库三闸必须保留，
   并补齐谁在何时批准清除的审计链。

在这些完成前，**不应对真实外部用户开放永久删除入口**；本轮实现只作为隔离环境里
可验证的最小实现与阻断分析。

---

## 11. 最终报告六问

| 问题 | 回答 |
|---|---|
| 是否实现真正的资源级清除，而非仅软删除？ | **部分实现**：无历史引用的简历/JD 是**物理删行 + 删 MinIO 对象 + 脱敏审计 + 独立验证**，不是 `deleted_at` 软删除。有历史引用的资源被明确 `BLOCKED`，未清除。 |
| 是否存在残留个人内容？ | **是**。BLOCKED 场景原文完整保留（故意、可恢复）；COMPLETED 场景仍有非内容型账本、审计骨架、用户级聚合骨架，以及备份/外部模型服务/Capture 等无法控制的边界。 |
| 被历史 Session 引用的资料如何处理？ | 一律 `BLOCKED`、零写入、围栏立即生效，并给出触发器级诊断；详见 §8。 |
| 清理失败后能否安全恢复？ | **能**。DB 步骤事务化；外部步骤失败记 `FAILED` 并保留 `completed_steps` 断点，重投从断点续做；BLOCKED 完全零写入，无需回滚。 |
| 是否仍需迁移或额外审批？ | **是**：0029 已在隔离库实施并完成升级/回滚专项；但生产库仍是 0025 且未迁移。清除被引用资料还需 §10 的例外迁移与单独审批。 |
| 哪些能力尚不适合真实用户开放？ | 真实永久删除入口、账号级 L4 删除、生产库清除执行、以及任何声称"外部模型服务/备份已清除"的表述。 |

---

## 12. 本轮提交

| 提交 | 内容 |
|---|---|
| `feat(db)` | 0029 迁移 + `purge` 模块（常量/模型/围栏/清除器/验证器/服务/路由）+ 运行器清单 |
| `feat(fence)` | 咽喉点围栏与任务写回防护 |
| `feat(worker)` | `purge_personal_data` Celery 任务 + dispatch |
| `feat(web)` | 前端清除申请与五态展示、诚实文案 |
| `test(purge)` | 13 条 L3 专项 + 迁移专项 + 合成数据构造器 |
| `docs(workbuddy)` | 本文件 |
| `docs(memory)` | 独立记忆 |

未 push、未 merge、未 rebase、未 reset、未 clean；正式库与历史 Live 数据未执行任何清除。
