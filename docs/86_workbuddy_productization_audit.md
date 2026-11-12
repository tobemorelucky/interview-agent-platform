# 86 · 请求安全收口与产品化路径审查（WorkBuddy 第七轮）

> 范围：只读审查 + 两处最小代码修复 + 离线回归。本轮**真实模型 HTTP = 0**，未改 `.env` / Provider 配置，未迁移正式库，未回写任何历史验收数据。
> 基线：HEAD `fce0882`（开工时核实），收工提交见 §0。
> 证据标注：`事实` = 有代码/命令/测试输出；`推断` = 未验证。

## 0. 本轮提交

| Hash | 内容 | 文件 |
|---|---|---|
| `804d385` | `fix(workbuddy): stop reporting a local pre-send failure as a provider fault` | `provider.py`、`governance.py`、`tests/test_request_safety.py` |
| `2273cb7` | `fix(workbuddy): require an explicit accept before replaying an unknown answer` | `controller.py`、`core_service.py`、`main_harness.py`、`schemas.py`、`tests/test_answer_retry_fence.py`、`tests/test_interview_recovery_performance.py` |
| （本文） | `docs(workbuddy): audit the request-safety chain and the self-service path` | `docs/86…` |

无数据库迁移、无 API 破坏性变更（`SendMessageRequest` 只新增可选字段）、无 Provider 配置变更。

---

## 1. 请求安全问题的真实根因与修复

### 1.1 事实链（第六轮 `wb_coach_attempt1_20261111`，0 次 HTTP）

1. 批次夹具 `capture.request` 是 httpx 的 **request event hook**，读取 `scope["category"]`；该次 scope 未带 `category` → `KeyError`。
2. httpx 在 `_send_single_request` 之前执行 request hooks，异常原样抛出 `client.post(...)`。
3. `CompatibleModelProvider._generate` 的兜底 `except (httpx.HTTPError, KeyError, ValueError, IndexError, TypeError, AttributeError)` 把 `KeyError` 记成 `model_request_failed`，`usage={}`。
4. `governed_call` 里 `sent` 在进入 `operation()` 前已被置 `True`，于是 `usage = getattr(exc,"usage") = {}`；`ModelGovernance.settle` 因为 `total` 不是 int 而落 `UNKNOWN`，`charged_tokens = reserved_tokens`（105,103）。

**结论**：一个纯本地错误被同时错记成「供应商网络故障」和「可能已发送」，两个错误叠在一起。

### 1.2 修复（`804d385`）

`_generate` 拆成「发送」「解析」两段，异常按阶段归位：

| 触发点 | 代码 | `request_sent` | 账本 |
|---|---|---|---|
| 连接未建立（`ConnectError` / `ConnectTimeout` / `PoolTimeout`） | `model_request_not_sent` | `False` | `SETTLED`，actual=charged=0 |
| 进 `client.post` 后抛出的非 httpx 异常（本地参数/客户端 hook） | `model_request_not_sent` | `False` | `SETTLED`，0 |
| 读/写超时（无法判定归属） | `model_timeout` | `None`→ 入口置 `True` | `UNKNOWN` |
| 其它传输错误 | `model_request_failed` | `True` | `UNKNOWN` |
| 收到响应但状态码失败 | `model_request_failed` + `availability_reason` | `True` | `UNKNOWN`（响应体不含 usage） |
| 收到响应但信封不可用（`choices` 缺失等） | `model_request_failed` | `True` | `UNKNOWN` |
| 截断 / 拒答 / 业务 Schema 拒绝 | `model_output_truncated` / `model_refusal` / `structured_output_failed` | `True` | `SETTLED`，按真实 usage |

`governed_call` 现在以「异常自报的 `request_sent`」优先于它自己的粗粒度 `sent` 标志：

```python
claimed = getattr(exc, "request_sent", None)
sent = sent if claimed is None else claimed
if sent is False:
    usage = {"total_tokens": 0}
elif not (isinstance(usage, dict) and type(usage.get("total_tokens")) is int and usage["total_tokens"] >= 0):
    usage = None
```

**没有新增账本状态**：`SETTLED + actual=0 + charged=0` 表示「已确证未发送」。这不是新发明的口径——`tests/test_v13_shared_governance.py::test_interrupted_http_budget_recovers_without_resending[before_send]` 早已断言未发送时 `SETTLED` 且 `actual=charged=0`。也没有直接改审计字段：结算仍走 `ModelGovernance.settle()` 的加锁、租约校验与状态迁移。

### 1.3 为什么不用新状态 `NOT_SENT`

`model_admissions` 上有 `ck_model_admission_status IN ('RESERVED','SETTLED','UNKNOWN')`，新增状态需要迁移和约束重建。本轮不改 schema；语义差异由**业务层错误码**（`model_request_not_sent`）+ `ExperienceAgentStepRun` 的 `request_sent` 记录承载。若后续要账本自带该语义，需单独立项（见 §10）。

---

## 2. UNKNOWN / 已发送 / 未发送：三类审计语义

| 语义 | 触发 | 用户级账本 | 会话级预算 | 业务层 | 自动重试 |
|---|---|---|---|---|---|
| 未发送（已确证） | 连接未建立、本地异常、准入被拒 | `SETTLED`，`charged=0` | 名额归还（`usage_reported=True`，known=0） | `model_request_not_sent`，`request_sent=false` | **允许** |
| 已发送并收到响应 | 任何 200/4xx/5xx 响应 | `SETTLED`（有 usage）或 `UNKNOWN`（响应体无 usage） | 按已知用量结算 | 真实错误码 | 视错误码而定 |
| 已发送、结果未知 | 读写超时、传输中断、进程消失 | `UNKNOWN`，按预留全额计费 | 预留转 `unreported_model_tokens` 或滞留 | `model_timeout` / 中断 | **禁止**，需显式确认 |
| 响应收到但业务拒绝 | `structured_output_failed` | `SETTLED`，按真实 usage | 按已知用量结算 | `structured_output_failed` + `validation_errors` | 允许（受控一次修复） |

**关键不变量**：只有「确实无法确认是否发送」才保留 UNKNOWN 并禁止自动重试；已确证未发送必须释放名额，且释放通过 `settle()` 完成。

---

## 3. 生产端跨进程重试：现在是否安全

### 3.1 已补上的部分（答题路径，`2273cb7`）

事实：`prepare_answer` 在租约过期（`lease.expires_at` 已过）时，原先只把旧 `ExperienceAgentRun` 标 `FAILED`、把预留转入 `unreported_model_tokens`，然后**用同一幂等键重新发一次真实 HTTP**。租约过期只证明进程消失，不证明请求没发出去。

现状：

1. `settle_model_call(..., request_sent=None, answer_id=...)` 在「没有 usage 回执且未被判定为未发送」时，把该 `call_key` 写入 `interview_plan_json.unknown_model_calls`（按 `answer_id` 归属，最多保留 20 条）。
2. `prepare_answer` 在租约过期后若发现 `model_call_reservations` 非空，或 `unknown_model_calls` 命中本答案 → 抛 `INTERVIEW_OUTCOME_UNKNOWN`（409），**不发任何 HTTP**，预留保持不动。
3. 同一题换新幂等键仍被既有规则拒绝（`该题已有回答，请使用原幂等键重试`），所以不存在「绕开围栏另起一次」的旁路。
4. 用户可在明确知情后提交 `confirm_unknown_retry=true`（`SendMessageRequest` 新增可选字段）放行一次，事件写入 `unknown_model_retries`（有界 20 条）留痕；SSE 错误事件新增 `requires_confirmation`。

已确证未发送（`request_sent=False`）与已知失败（有 usage）都不写 `unknown_model_calls`，因此仍然可以正常重试——这是分类修复与围栏修复的接缝。

### 3.2 仍然开放的部分（事实/待验证）

- **用户级账本没有业务幂等键**：`governed_call` 每次 `uuid4().hex`，`ModelAdmission` 既无 `business_key` 也无 `provider_request_id`，无法与供应商账单对账，也无法阻止「同一业务操作经另一条代码路径再次发起」。
- **worker 路径未覆盖**：Celery 的 `resume_tasks` / `kb_tasks` 若发生重投递，本轮没有验证其幂等键与 `ModelAdmission` 的关系（`推断：未围栏`，需实测）。
- **进程在模型返回后、结算前消失**：预留会滞留 → 触发围栏 → 该答案需显式确认。安全，但代价是用户被迫做选择。
- **无运维对账入口**：UNKNOWN 一旦产生，只能靠 `confirm_unknown_retry` 或新建面试，没有管理员「核对后释放」的操作。

---

## 4. 回归结果（真实计数）

- 隔离库：`interview_agent_wb_r7_20261112`（本轮新建，Alembic `0026`，66 张表）。
- 命令前缀：`DATABASE_URL` 与 `M1_TEST_DATABASE_URL` 同时指向该库，`apps/api/.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`。
- 本轮真实模型 HTTP：**0**（全部为 `MockTransport` + Replay）。

| 范围 | 结果 |
|---|---|
| `tests/test_request_safety.py`（新增，5 例） | **5 passed** |
| `tests/test_answer_retry_fence.py`（新增，3 例） | **3 passed** |
| 上述 8 例 + `test_interview_recovery_performance.py` | **16 passed / 0 failed / 0 skipped** |
| 全量（排除 `test_0[0-9]_*` 迁移用例，含上述新增） | **721 passed / 5 failed / 1 skipped**（165 s） |
| 早前一次全量（Redis 未启动时） | 718 passed / 10 failed / 1 skipped |

`5 failed` 全部为**既有**环境依赖或陈旧断言，与本轮改动无因果关系：

| 用例 | 根因 | 证据 |
|---|---|---|
| `test_modelscope_provider.py::…[200/401/429/503]`（4 例） | 断言请求体 `"thinking" not in body`；当前 `ANSWER_EVALUATION_THINKING=disabled` 会显式发送 `thinking={"type":"disabled"}` | `provider.py` 组装请求体的分支由 `c2cb7ac` 引入、`67a39b1` 起对 interview 路由生效；本轮 diff 未触及该行 |
| `test_product_polish.py::test_live_routes_inherit_unified_model_and_low_latency_policy` | 断言所有路由统一为 `llm_model`；本机 `USE_MODELSCOPE=true` 时全部走 ModelScope | 失败断言只为 `provider.model_name`，与 `provider.py` 异常分支无关 |

`test_08_v13_migration.py` / `test_09_v22_migration.py` 需要在空库上逐步 upgrade/downgrade；在有 700+ 用例数据的库上返回非零（库版本仍为 `0026`、66 表，未受损），故未计入上表。

早期 10 failed 中的另外 5 例已解释：2 例 Redis 未启动（启动后通过），1 例为本轮围栏改变既有行为（已按新语义更新断言并保留原意：旧结果仍不能覆盖已提交答案、只产生 1 条 Answer），2 例即上表迁移用例。

---

## 5. 普通新用户完整操作路径现状矩阵

判断口径：**普通用户能否只通过前端完成**；不把 Replay 页面、演示脚本、管理员预置数据算作自助路径。

| # | 步骤 | 结论 | 关键证据 |
|---|---|---|---|
| 1 | 注册并登录 | **完整实现** | `apps/web/src/pages/RegisterPage.vue`、`LoginPage.vue`；`modules/auth/router.py`；注册固定 `USER`，无邮箱验证/邀请码 |
| 2 | 上传自己的简历 | **完整实现**（依赖 Worker/MinIO/模型） | `pages/ResumeListPage.vue`、`modules/resume/router.py`（MinIO + `raw_text`）、`processor.py` 派发 Celery 并触发画像 |
| 3 | 粘贴真实 JD | **完全缺失** | 前端无 JD 文本框；后端无接收用户自由文本 JD 的端点（`public_career/router.py` 只有只读投影与来源申请） |
| 4 | 保存为私有目标岗位 | **只有管理员/夹具数据** | `public_career/models.py`：`Company`(28–49)、`Job`(82–112)、`JobDescriptionVersion`(237–289) 均无 `user_id/visibility/source`；`uq_companies_normalized_name`、`uq_jobs_company_title` 全局唯一 |
| 5 | 提取 JD 要求并允许核对 | **只有管理员路径** | `public_career/research/agent.py` 写入 `structured_data_json` + `job_requirements`；审核在 `web_router.py`(134–152) 与 `review.py`，用户无编辑/确认接口 |
| 6 | 运行 Job Matching | **完整实现**（全库推荐，不支持指定岗位） | `job_matching/router.py`、`retrieval.py::retrieve` 只召回 `PUBLISHED` + 有 requirements 的岗位；`MatchRequest` 无 `job_id` |
| 7 | 用该岗位 + 简历启动面试 | **部分实现，前端只能选管理员已发布岗位** | `schemas.py::CreateSessionRequest` 必填 `company_id/job_id`；`context.py::build_context` 要求 `ACTIVE` 公司/岗位 + `PUBLISHED` JD + 至少 1 条 requirement + 画像；前端下拉来自 `GET /interview/targets` |
| 8 | 查看正式评价与 Coach | **完整实现** | 每轮回答落 `InterviewEvaluation`；报告需 `session.status == "COMPLETED"`（`interview_report/service.py:51`）后显式生成，前端有展示 |
| 9 | 按 Memory 专项训练 | **完整实现**（需历史场次） | `context.py` 生成 `MemoryGuidedPlan`，Main 消费；`pages/TrainingPage.vue` 调用 `/training` |

**卡点根因**：岗位/JD 实体没有「归属」概念，所有读取入口都要求 `PUBLISHED`，而 `PUBLISHED` 只能由管理员审核或演示夹具产生。空库里普通用户注册、上传简历后，`/interview`、`/jobs/matches`、`/training`、`/career` 全部为空。

---

## 6. 用户私有 JD 入口缺失的功能

1. 接收自由文本 JD 的 API（含长度、来源、去重）。
2. 私有岗位的归属与可见性（`user_id` / 私有可见性 / 来源标记）。
3. 面向用户的解析与**可编辑确认**（不是管理员审核）。
4. 用私有岗位创建 Session 的合法分支（`build_context` 目前硬性要求 `PUBLISHED`）。
5. 私有岗位进入 Skill 选择的合法来源（`recommend_jobs()` 只读公共表；`recommend()` 本身接受内存 dict，可复用）。
6. 私有数据不得进入公共投影：`published_jobs()`、`retrieve()`、`/public-career/jobs` 目前都以 `PUBLISHED` 过滤，天然隔离——但新增表后必须加回归测试固化这一点。

---

## 7. 下一轮最小产品功能：用户私有 JD Workbench V1

### 7.1 方案选择（新表 vs 改旧表）

| 方案 | 内容 | 代价 |
|---|---|---|
| A. 复用 `companies/jobs/job_description_versions` | 加 `owner_user_id/visibility/source/confirmation_status` + 重建 3 个唯一约束为部分索引 | 需要改 4 张核心公共表、3 个唯一约束；私有「字节跳动」会与公共公司争抢全局唯一名；公共投影需要逐处加 `visibility` 过滤，漏一处即泄露 |
| **B. 新增 `user_job_targets`（推荐）** | 一张用户私有表承载「粘贴原文 → 解析结果 → 用户确认」，公共表零改动 | 需要 1 张新表 + 1 个唯一索引；`build_context` 增加一个分支 |

推荐 B：私有数据的生命周期、可见性与公共资料完全不同，混用一张表会让「不泄露」依赖每一处查询都记得加条件。B 的风险面是**新增代码**而不是**修改既有查询**。

### 7.2 最小迁移（等待批准，本轮不实施）

新表 `user_job_targets`（Alembic 版本名 `0027_user_job_targets`）：

```text
id                    BIGSERIAL PK
user_id               BIGINT NOT NULL FK users(id) ON DELETE CASCADE
company_name          VARCHAR(255) NOT NULL            -- 用户填写的公司名，可为「未提供」
job_title             VARCHAR(255) NOT NULL
raw_text              TEXT NOT NULL                    -- 用户粘贴原文，原样保存
source_type           VARCHAR(32) NOT NULL             -- USER_PASTED（本轮唯一取值）
source_reference      VARCHAR(500) NULL                -- 可选：Boss 直聘/官网链接
content_fingerprint   CHAR(64) NOT NULL                -- sha256(raw_text)，用于同用户去重
requirements_json     JSONB NOT NULL DEFAULT '[]'      -- [{idx, statement, importance, source}]
structured_json       JSONB NOT NULL DEFAULT '{}'      -- 公司/岗位/职责等解析结果
parse_status          VARCHAR(20) NOT NULL DEFAULT 'DRAFT'  -- DRAFT/PARSING/PARSED/FAILED/USER_CONFIRMED
parse_error           VARCHAR(500) NULL
parse_agent_run_id    BIGINT NULL                      -- 复用 ExperienceAgentRun 审计
confirmed_at          TIMESTAMPTZ NULL
created_at/updated_at TIMESTAMPTZ NOT NULL
UNIQUE (user_id, content_fingerprint)
INDEX  (user_id, created_at DESC)
```

改动范围：**新增 1 张表、0 个既有表、0 个既有约束**。不触碰 `model_admissions`、`interview_*`、`public_career` 表。

### 7.3 后端文件清单（精确）

| 文件 | 类型 | 改动 |
|---|---|---|
| `apps/api/src/interview_api/modules/job_target/__init__.py` | 新增 | 空包 |
| `apps/api/src/interview_api/modules/job_target/models.py` | 新增 | `UserJobTarget`（上表字段） |
| `apps/api/src/interview_api/modules/job_target/schemas.py` | 新增 | `CreateTargetRequest(raw_text 200–20000, company_name, job_title, source_reference)`、`TargetResponse`、`RequirementPatch`、`ConfirmRequest` |
| `apps/api/src/interview_api/modules/job_target/service.py` | 新增 | 创建（去重/归属）、解析（`generate_structured`，走既有 `EXTRACT_ROUTE`，不新建 route/Agent）、编辑、确认、删除；全部按 `user_id` 过滤 |
| `apps/api/src/interview_api/modules/job_target/router.py` | 新增 | `POST /api/v1/job-targets`、`GET /api/v1/job-targets`、`GET/PATCH/DELETE /{id}`、`POST /{id}/parse`、`POST /{id}/confirm` |
| `apps/api/src/interview_api/main.py` | 改 | `include_router(job_target_router)` |
| `apps/api/src/interview_api/modules/interview/schemas.py` | 改 | `job_target_id: int \| None`；`company_id/job_id` 改为可选 + 模型级校验「公共岗位与私有目标二选一」 |
| `apps/api/src/interview_api/modules/interview/context.py` | 改 | 私有分支：读 `UserJobTarget`（`user_id` 匹配、`parse_status='USER_CONFIRMED'`），requirements 来自 `requirements_json`（`id` 用 `idx`），`job_description_version_id=None`；公共分支完全不动 |
| `apps/api/src/interview_api/modules/interview/skill_engine.py` | 改 | `freeze()` 增加第三种来源模式 `USER_PROVIDED_JD_CONTEXT`（现只有 `SOURCED_JOB_CONTEXT` / `GENERIC_SKILL_FALLBACK`）；**不得**把用户粘贴的 JD 标成「已发布 JD」 |
| `apps/api/src/interview_api/modules/interview/skill_recommendation.py` | 改 | 新增 `recommend_for_target(target)`：用 `recommend({title, job_family, requirements, tags})` 的内存路径；`recommend_jobs()` 不动 |
| `apps/api/src/interview_api/modules/job_target/__init__.py` 之外 | — | `public_career/library.py`、`job_matching/retrieval.py` **不改**，仅新增回归测试证明私有行不会出现 |
| `apps/api/alembic/versions/0027_user_job_targets.py` | 新增 | 建表 + 唯一索引 |

### 7.4 前端文件清单（精确）

| 文件 | 类型 | 改动 |
|---|---|---|
| `apps/web/src/types/job-target.ts` | 新增 | 目标岗位、解析结果、requirement 校对的类型 |
| `apps/web/src/api/job-target.ts` | 新增 | 创建/解析/查询/修改/确认/删除 |
| `apps/web/src/pages/JobTargetPage.vue` | 新增 | 粘贴框、公司/岗位名、来源链接、解析、逐条校对、「确认并用于面试」 |
| `apps/web/src/router/index.ts` | 改 | 新增 `/jobs/targets`、`/jobs/targets/:id`（`requiresAuth`） |
| `apps/web/src/i18n/zh-CN.ts` | 改 | 导航新增「我的目标岗位」 |
| `apps/web/src/pages/InterviewChatPage.vue` | 改 | 岗位下拉合并「已发布岗位」与「我的目标岗位」，创建时传 `job_target_id` |
| `apps/web/src/api/interview.ts`、`types/interview.ts` | 改 | `createSession` 支持 `job_target_id`；目标项带 `source` |
| `apps/web/src/pages/TrainingPage.vue` | 改 | 训练入口同样支持私有目标（沿用同一选择器组件） |

明确**不做**：不新增 Agent、不重写前端、不实现「私有岗位加入公共推荐」、不做单岗位匹配报告。

### 7.5 最小验收标准

1. 用户 A 粘贴一段真实 JD → 创建目标 → `parse_status` 从 `DRAFT` 到 `PARSED`，requirements ≥ 3 条，每条带 `source=LLM`；解析失败时 `FAILED` 且 `parse_error` 可读，不产生半个岗位。
2. 用户 A 可编辑/删除 requirement，确认后 `USER_CONFIRMED` + `confirmed_at`；未确认的目标不能创建面试（`JOB_TARGET_NOT_CONFIRMED`）。
3. 用该目标 + 自己的画像创建 Session → 首题生成成功；`context_snapshot` 中 `company_background.mode == "USER_PROVIDED_JD_CONTEXT"`，`source_type == "USER_PASTED"`，`requirement_ids` 来自该目标。
4. 用户 B 访问 A 的目标（GET/PATCH/DELETE/parse/confirm）→ 403/404，且不泄露字段内容。
5. `GET /public-career/jobs`、`job_matching` 召回结果均不含该目标（回归测试固化）。
6. 全流程不依赖任何 seed/演示数据：空库 + 新注册用户即可完成 1–3。
7. 未配置模型时解析失败但目标与原文仍可用（不丢用户输入）。

---

## 8. 是否需要数据库迁移与权限变更

- **需要迁移**：是，且仅 1 张新表 + 1 个唯一索引 + 1 个普通索引（§7.2）。本轮**不实施**，等待批准。
- **需要权限变更**：不需要新的角色或权限位。沿用现有模式：`get_current_user` + 查询条件带 `user_id`（与 `resume`、`job_matching`、`interview` 的 `owned()` 一致）。私有目标只在 `job_target` 模块与创建面试时被读取。
- **不需要**：不改 `require_admin`，不改公共审核流，不改 `published_jobs()` 的 `PUBLISHED` 语义，不改 `ModelGovernance` 结构。

---

## 9. 如何证明「不是只有 Demo 数据才能跑」

以下为**可执行验证方案**（本轮 0 HTTP，未执行；下一轮实施后按此验收）：

1. 新建空库 `interview_agent_verify_empty_<date>`，`alembic upgrade head`，确认 `alembic_version=0027`、表数 67。
2. 只走 HTTP：`POST /auth/register` → `POST /auth/login` → `POST /resumes/upload`（真实 PDF）→ 轮询 `GET /resumes` 至 `COMPLETED`（这一步仍需 Worker + 真实模型，可用隔离模型配置）。
3. `GET /interview/targets` 期望 `[]`；`GET /public-career/jobs` 期望 `[]` —— 证明没有预置演示数据。
4. 当前 code base（未实施 §7）下 `POST /interview/sessions` 期望 `INVALID_COMPANY_JOB`（422），用来固化「现状必须依赖管理员岗位」这一事实。
5. 实施后同样条件：`POST /job-targets` → `POST /{id}/parse` → `PATCH` 校对 → `POST /{id}/confirm` → `POST /interview/sessions {job_target_id}` 期望 201 且返回首题；用 Replay/离线 provider 即可验证，不需要真实模型。
6. 交叉验证越权：用户 B 对 A 的目标发起上述 4 个写操作，期望 403/404；`GET /public-career/jobs` 与 `job_match_runs` 的候选列表都不含该目标。
7. 数据核查：`select count(*) from user_job_targets` 与创建次数一致；`companies/jobs/job_description_versions` 行数在过程中**不变**（证明私有路径没有污染公共库）。

---

## 10. 仍存在的高风险问题

| 级别 | 问题 | 说明 / 建议 |
|---|---|---|
| P1 | UNKNOWN 会话不可恢复（只能显式确认或新建） | 本轮有意选择的安全代价；缺少管理员对账/释放入口，建议下一轮补一个只读诊断 + 手工释放端点 |
| P1 | 用户级账本无 `provider_request_id` / `business_key` | 无法与供应商账单对账，也无法对 worker 重投递做幂等；需要一次小迁移（加两列）与 `governed_call` 传入业务键 |
| P1 | 5 个测试断言已陈旧 | `test_modelscope_provider`（4 例）与 `test_product_polish`（1 例）在有 `.env` 的环境必失败，CI 绿灯会失真；建议单独一次 `test(workbuddy)` 提交修正断言（本轮未动，避免混入产品改动） |
| P1 | 私有 JD 的隐私与留存 | 用户粘贴文本会进入模型上下文与数据库；需要明确留存期、脱敏与删除级联（`ON DELETE CASCADE` 已在方案内） |
| P2 | `model_admissions` 状态语义受限 | 只有 3 个状态，`NOT_SENT` 需迁移；当前用「SETTLED + 0」表达，可读性依赖文档 |
| P2 | 迁移用例依赖空库 | `test_08/test_09` 在有数据时失败；建议给它们独立库名或显式建库 |
| P2 | 20 轮 Live 未跑 | 已按要求停止以 Live 为中心的开发方式；预算/题量匹配仍是部署决策 |
| P2 | `.workbuddy/` 未跟踪 | 仍是唯一未跟踪目录，是否忽略或关闭工作区记忆待定 |
