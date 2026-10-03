# F2：Real Model Acceptance

日期：2026-10-03。结论：**未通过；按失败处理规则停止在 ResumeProfileAgent，不进入 F3。**

本次六个角色中，PublicDataResearchAgent 真实模型成功；ResumeProfileAgent 通过 JSON/Schema，
但 candidate-side Evidence validator 拒绝技能证据状态。其余四个角色未运行，不宣称真实模型成功。
没有修改模型请求、Prompt、Schema、validator 或 deterministic policy，也没有替换成 Replay Model。

## 1. 环境、冻结与模式

- 独立 smoke 库：`interview_agent_f2_real_model_smoke`，新建后正常 Alembic upgrade 到 `0024`，保留供排查。
- 独立回归库：`interview_agent_f2_regression_20261003`，migration downgrade/upgrade 测试只使用此库。
- 正式 `interview_agent` 仍为 `0024`。与 F1 最终只读快照比较，61 个业务表全部 count/hash 不变；无无效 FK/constraint/index。
- F2 开始前 391 个 tracked/untracked 文件逐文件冻结核对；未覆盖任何已有工作。旧 migration `0017–0024` 不变；无新 migration/依赖。
- `.env` 继续 Git ignore，内容 hash 不变；F0/F1 原生 dump backup 内容 hash 不变。未提交 Git commit。
- **model_mode=LIVE**；**tool_mode=REPLAY**；**data_mode=LOCAL_FIXTURE / TEST / DEMO**。
- 不运行 SearXNG Search，不请求 fixture URL 的真实公网正文，不声称 Live Search 成功。

## 2. 实际 Provider / Model / Prompt

所有路线沿用现有配置：Provider `openai_compatible`，模型 `doubao-seed-2-0-pro-260215`，
火山方舟端点 host `ark.cn-beijing.volces.com`。本次真实发送了三次模型请求，均 HTTP 200。

复用 CompatibleModelProvider / ModelRouter / Prompt Registry / Structured Output / constrained repair /
concurrency guard / ExperienceAgentRun、StepRun；没有在 domain 中硬编码或替换模型。
测试 observer 只检查既有最终 structured content 并记录 numeric usage/status/latency，不改变请求或返回值。
不保存原始模型响应或隐藏推理，不输出密钥。cost 未配置，保持 NULL。

## 3. 六角色验收结果

| Agent | 实际结果 | Structured Output / 持久化 |
| --- | --- | --- |
| PublicDataResearchAgent | 成功 | extraction、quality 均通过，RefreshTask=COMPLETED，JD/Requirement/Claim/Evidence 持久化 |
| ResumeProfileAgent | 失败 | JSON/Schema 通过，Evidence semantic validation 失败，generation=FAILED，无 ProfileVersion |
| JobMatchingAgent | 未执行 | 因上游失败停止 |
| InterviewMainAgent | 未执行 | 因上游失败停止 |
| AnswerEvaluationAgent | 未执行 | 因上游失败停止 |
| SummaryCoachAgent | 未执行 | 因上游失败停止 |

### Research：Fixture 正文 + LIVE 模型

`f2_jd_fixture.txt` 明确声明 TEST/LOCAL_FIXTURE、非真实招聘岗位。
ReplayFetchProvider 经现有 ToolGateway 对 `https://f2-fixture.example/careers/demo-ai` 两次 Fetch。
两条 ToolInvocation 均 `provider_mode=REPLAY`、SUCCEEDED，属于同一 RefreshTask。
未执行 Search；URL 不作为 Live 抓取结果。

落库结果：Source=1、SourceSnapshot=1，重复 Fetch 保持去重；Claim=4、ClaimEvidence=4、
JobDescriptionVersion=1、JobRequirement=3。四条 ClaimEvidence 的 evidence_key 均能被既有
EvidenceCatalog 精确解析，excerpt 等于真实 snapshot 的对应 offsets，不进行 fuzzy 匹配。
`independent_content_count=1` 由 deterministic grouping 产生。

该 fixture 并非官方 Live 来源，JD=`WAITING_REVIEW`，没有冒充官方来源或绕过发布 gate。
PublicDataRefreshTask=`COMPLETED` 表示本次受控 extraction/persistence 完成，不表示通过正式发布审核。

### Profile：实际失败节点

TXT 内容明确 TEST/DEMO、非真实求职者，实际执行 ResumeParser，保存 MinIO scoped fixture 文件及 Resume。
ResumeReport 输入明确为 `LOCAL_FIXTURE_DEMO`，**不声称旧 ResumeReport LLM 生成链路已真实模型验收**。
当前 F2 验收对象是六个正式 Agent，Profile 输入使用该已标记材料。

Profile 模型返回 HTTP 200 / `finish_reason=stop`，最终 JSON 成功通过 CandidateProfileDelta Schema。
但既有 `candidate_profile/evidence.py:77` 的 `resolve_and_validate()` 返回：

`UNSUPPORTED_SKILL_STATE`

这是 candidate-side Evidence 语义校验失败，不是 HTTP 请求格式、JSON parse 或 Pydantic Schema 问题。
对于非 MENTIONED 技能，validator 要求技能 label 出现在被引用的项目/工作/实习具体使用 excerpt 中：
DEMONSTRATED 至少一条；STRONG_EVIDENCE 至少两条不同证据。本次至少一个技能未满足对应条件。
Prompt `resume_profile@1.0.0` 已明确相同规则，未发现可据此认定的明显 provider/request compatibility bug。

没有保存原始输出，不能据失败代码猜测具体哪个技能/引用；也不能宣称整份 Profile 已通过全部 Evidence 校验。
StepRun=SUCCEEDED 表示 structured model call 成功；AgentRun=FAILED / ProfileGeneration=FAILED 表示业务验收失败。
未把 step 成功误报为 Agent 成功。

失败后数据库：Resume=1、ResumeReport=1、ResumeEvidence=11，均保留；
CandidateProfile / CandidateProfileVersion / normalized project/education/skill=0。
失败 generation 的 `version_id=NULL`，不会切换 current_version，也不影响已经保存的 ResumeReport。
InterviewSession=0、UserMemoryItem=0，未执行下游链路。

## 4. Structured repair、Token 与 Latency

| Role / Route | Prompt version | Latency ms | Input tokens | Output tokens | Reasoning tokens | Repair | 业务结果 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| Research / public_research_extract | job_jd 1.0.0 | 12335 | 747 | 608 | 309 | 0 | 成功 |
| Research / public_research_quality | quality 1.0.0 | 11626 | 605 | 551 | 521 | 0 | 成功 |
| Profile / resume_profile | resume_profile 1.0.0 | 47631 | 2096 | 3326 | 2160 | 0 | Evidence validator 失败 |

三次 structured output 都通过 Schema，因此没有触发 constrained repair。
现有 repair 只修复结构问题，不用于降低 Evidence 语义约束或反复刷成功。

实际返回总 input=3448、output=4485、total=7933 tokens；reasoning=2990 是 provider 返回的
completion usage detail，**不额外叠加到 total**。本次三次调用 latency 合计 71592 ms，不是完整 E2E wall time。
Reasoning 只记录数值，不保存内容；所有 cost=NULL，不自行估价。
未执行角色的 usage/latency 没有数据，不用 0 冒充真实调用。

## 5. Real / Replay / Live Search 边界

- **Real Model 已验证**：Research 两条真实模型路线的 Structured Output、Evidence resolver、quality gate、公共实体持久化；
  Profile 模型实际可调用且输出 Schema 合格，但 candidate-side semantic validator 未通过。
- **Replay 已验证**：M1–M6 原有 pytest 全量回归，包括 Replay、structured repair/hard fail 和既有两场/三岗位 E2E；
  这些不是本次下游四 Agent 的 Real Model 验收。
- **Live Search**：继续冻结，CAPTCHA/rate limit 已知限制仍在；本阶段没有运行，不宣称稳定可用。
- 核心 Real Model Resume→Profile→Match→Interview→Coach E2E **未完成**，止于 Profile validator。
- F2 未重新执行浏览器/SSE 全流程；现有 frontend SSE parser 回归通过，不夸称本次真实模型 SSE 已验收。

## 6. 回归与数据安全

- backend：`uv run --frozen --no-sync pytest tests -q`，**178 passed**，0 failed/skip，107.87 秒。
- frontend：`node --test tests/*.test.mjs`，**7/7 passed**。
- production build：`pnpm build`，vue-tsc / Vite 成功，150 modules。
- Python：`python -m compileall -q src scripts/smoke_real_model_acceptance.py` 成功。
- `git diff --check` 成功；既有 CRLF 提示不是 whitespace error。
- 唯一 pytest warning 仍是既有 `.pytest_cache` 权限问题，未趁本阶段修改业务/依赖。
- 正式库全部业务表哈希不变，F1 已有业务数据、管理员密码及 DEMO_F1 用户不变。
- API key / JWT / 管理员密钥未进入 git diff、docs 或验收日志；`.env` 不纳入版本控制。

## 7. 文件与复现记录

本次仅新增四个可跟踪文件：

1. `apps/api/scripts/smoke_real_model_acceptance.py`
2. `apps/api/tests/fixtures/f2_jd_fixture.txt`
3. `apps/api/tests/fixtures/f2_resume_demo.txt`
4. `docs/25_real_model_acceptance.md`

本地忽略的记录：`.runtime/finalization/f2-freeze-manifest.json`、`f2-result.json`、
`f2_db_audit.py`。失败与成功的真实 Agent/Step 审计保留在独立 smoke DB 中。
该数据库含 F2 DEMO/Fixture 数据，不作为正式用户库；脚本拒绝在正式库运行，也拒绝自动重复生成刷成功。

可复现命令通过子进程覆写 DATABASE_URL 为隔离 F2 库，模型配置/密钥仍来自当前环境。
面试 fixture 预设 3 题、per-question follow-up budget=1；该下游步骤本次未到达，不能算已验证。
回归进程同时覆写 DATABASE_URL 与 M1_TEST_DATABASE_URL，避免迁移测试触碰正式库或 F2 审计库。

## 8. 清理与遗留

F2 runner 已退出并关闭临时 HTTP clients；没有启动新的 API/Worker/Web smoke 服务或投递共享 Celery 任务。
F1 既有 Runtime 不受影响；独立 smoke/regression DB 和 scoped MinIO fixture 文件保留，方便复现排查。
正式库/volume/F0/F1 backup 均未删除。

阻塞项是 Profile 的 `UNSUPPORTED_SKILL_STATE`。按要求先报告，不放宽 validator、不继续下游、不进入 F3。
若后续授权进一步诊断，需保留现有失败审计，明确记录新尝试；不能将本轮改写成“六 Agent 全部成功”。

---

## Attempt 2：DeepSeek Primary + deterministic skill-state resolver

日期：2026-10-03。**本次六个正式 Agent 全部真实模型成功，核心 Real Model E2E 通过。**
以上 Attempt 1 的失败事实原样保留；旧库 `interview_agent_f2_real_model_smoke`、FAILED generation、
原结果文件及 ResumeReport/Research 成果没有删除、覆盖或改写。新轮使用
`interview_agent_f2_real_model_smoke_attempt2`，正常迁移到既有 `0024`；无新增 migration。

### A2.1 模型配置与可达性

- `selected model = deepseek-v4-pro-ga-260813`；Provider `openai_compatible`，火山方舟真实端点。
- Primary 使用统一 `LLM_MODEL`；七条逻辑 route（Research extract/quality 和另外五个角色）默认继承它。
- 配置 `LLM_FALLBACK_MODEL=doubao-seed-2-1-turbo-260628`，Router 统一构造备用 provider，不在 Agent 中硬编码。
- 本次 **没有 fallback**，`fallback reason=NULL`，没有调用备用模型，也没有继续使用旧 Doubao 模型。
- 最小连接测试：HTTP 成功、严格 `{"ok":true}` Structured Output 成功、repair=0、latency=3040 ms；
  provider 返回 input=285、output=45、total=330、reasoning=39 tokens。备用模型的真实可达性未测试，因为 Primary 可用。

Fallback 仅在明确 provider/model availability 信号发生：model/endpoint not found/unavailable、
quota/rate-limit、明确 retriable availability HTTP/code。认证/请求格式错误、client timeout、
Schema/Evidence validator、业务规则失败不会切换模型。最多切换一次；constrained repair 仍最多一次，
且原事实字符串与 Evidence ID 关联必须保持。没有“失败后用备用模型刷成功”的业务路径。

Agent Step audit 的 `model_invocations` 逐次保存实际 provider/model、status/error、fallback reason、
latency/usage；Step 的 provider/model 及后续持久化 metadata 采用实际选中模型。记录 primary 失败后
fallback 的两次调用，而不是用最终模型掩盖历史。复用既有 JSON 审计字段，不另建表、不保存隐藏推理。
availability fallback、失败不换模型与实际模型审计有 MockTransport/Replay 正式测试覆盖。

### A2.2 Profile 状态由程序收敛，validator 不放宽

Model Schema 将技能状态表达为 `proposed_evidence_state`；兼容接受旧 fixture 的 `evidence_state`。
持久化 snapshot/UserSkill 仍保存最终 `evidence_state`，不建立第二套长期技能真相源。
新增 Profile prompt `1.0.1`，历史 `1.0.0` 仍可查询，没有冒用旧版本号。

Resolver 只使用本次实际引用的合法 ResumeEvidence，不追加引用、不制造 Evidence：

- 无具体使用证据：上限 MENTIONED。
- 至少一条包含 skill label 的项目/工作/实习 excerpt：上限 DEMONSTRATED。
- 至少两条不同的有效具体使用 excerpt：上限 STRONG_EVIDENCE。
- `final_state=min(proposed_state, maximum_supported_state)`；绝不向上提升。
- 重复引用同一 Evidence 不增加支持量；未引用的 Evidence 不计入本技能支持量。
- 未知 ID、无合法引用、label/statement 不受原文支持仍拒绝；unsupported strength 规则完全保留。
- 最后仍运行原 `resolve_and_validate()`；仅将可计算的字段先确定性收敛，并未删除其最后防线。

真实结果：Python STRONG_EVIDENCE→STRONG_EVIDENCE；Redis STRONG_EVIDENCE→DEMONSTRATED；
SQL MENTIONED→MENTIONED。仅 Redis 发生一次向下收敛，没有上调任何状态。
生成 ProfileVersion 1，normalized project=1、work/internship=1、education=1、skill=3；
11 条确定性 ResumeEvidence 保留，重试复用同一 ProfileVersion，没有额外模型调用。

审计保存 skill label、proposed/max/final state、referenced evidence IDs、validation rule、是否 normalized。
技能 ID/支持失败可记录同类结构化诊断，不保存原始模型响应或 chain-of-thought。
画像发布/normalized records/current_version 原子切换、历史不可变和 Resume 删除保护均未改动。

### A2.3 六 Agent 真实结果

| Agent | 结果 | 真实持久化与校验 |
| --- | --- | --- |
| PublicDataResearchAgent | 成功 | Fixture Snapshot→LIVE extraction/quality→Claim/Evidence/JD/Requirement，task COMPLETED |
| ResumeProfileAgent | 成功 | LIVE Delta→state resolver→原 Evidence validator→ProfileVersion/normalized records |
| JobMatchingAgent | 成功 | 三个 DEMO 岗位：SATISFIED 正常推荐，UNKNOWN 保持未知，NOT_SATISFIED 不进入 rerank |
| InterviewMainAgent | 成功 | pinned PUBLIC_QUESTION/FOLLOW_UP 来源有效，动作全部由原 Controller 提交 |
| AnswerEvaluationAgent | 成功 | Answer/Evaluation 对应正确，五维 1–5，未使用 default score |
| SummaryCoachAgent | 成功 | 原 resolver/quality 规则通过，Report current、Observation、PENDING Proposal 原子持久化 |

JobMatchRun 1 的 AI Application Engineer：pre/agent/final rank=1/1/1，STRONG_MATCH；
Java Backend Engineer：2/2/2，EXPLORATORY，eligibility=UNKNOWN；Data/Risk Analyst 因明确地区约束
NOT_SATISFIED 被排除，未被模型翻转。Matching policy、Evidence Matrix、资格 Schema 均未修改。

### A2.4 中途异常与明确继续：不隐去失败

1. Matching 首次请求达到原 60 秒上限，AgentRun 3 / Step 4 / MatchAttempt 1=`FAILED:model_timeout`。
   没有响应 usage，不以 0 估算。没有 fallback。保留失败审计，在同一个冻结 MatchRun 上进行一次明确 retry。
2. 将 `JOB_MATCHING_TIMEOUT_SECONDS`、`INTERVIEW_MODEL_TIMEOUT_SECONDS`、`INTERVIEW_COACH_TIMEOUT_SECONDS`
   配置为 180 秒，仅延长等待，不改变模型、Schema 或业务 validator。Matching 第二次用同一 DeepSeek 成功，
   latency=112755 ms，证明不能因响应超过 60 秒就切换模型。失败 MatchAttempt 1 与成功 MatchAttempt 2 均保留。
3. 第一场 Session 1 达到原 conservative reservation budget：已使用 reservation=177506，下一次调用预留会
   超过 200000，原 Controller 正确 COMPLETE，2 个有效 Evaluation、1 个 SKIPPED；第三个已保存 Answer 没有
   虚构 Evaluation。Smoke runner 对每轮都有 evaluation event 的断言失败；这不是 Agent/validator 失败。
4. 不修改 Session 1 的 immutable Snapshot 或 Controller；保留该预算结束场次。仅为 F2 子进程设置
   `INTERVIEW_MAX_MODEL_TOKENS=500000`，创建新的 Session 2。正式配置的 max-model budget 仍为 200000。
   500000 是保守 **reservation 上限**，不是实际 token 消耗；题数仍是 3 次固定 TEST 回答、最多一次 FOLLOW_UP。
5. 成功 Research/Profile/Match 均从已持久化结果继续，不重新生成来刷结果。所有异常与继续记录均留在本地 artifact/DB。

### A2.5 完整 Real Model E2E

`TEST TXT→真实 Parser→明确 Fixture ResumeReport→LIVE Profile→CandidateProfileVersion 1`
`→LIVE Matching→Job 2 / JD 2→新 M4 Snapshot 2→LIVE Main/Evaluation→Session 2 COMPLETE`
`→数据库 statistics→LIVE Coach→FinalInterviewReport 1→Observation / PENDING MemoryProposal`

- Session 2：COMPLETED、active_question_id=NULL、三题均 COMPLETED，无 PENDING/ASKED/ANSWERED 泄漏。
- 3 Answer / 3 有效 Evaluation，1 FOLLOW_UP、0 DEEP_DIVE、0 SKIPPED；两道主问题。
- SSE service 实际事件：status→evaluation→follow_up→done；status→evaluation→question→done；
  status→evaluation→complete→done。生产 service 在 Controller commit 后发结果；本轮未做浏览器长流程。
- 每轮 snapshot 内容相同，固定 Profile/JD、公共题内容/fingerprint 未漂移；不修改 M4。
- Report=COMPLETED/current，关联 Session 2 / Snapshot 2；statistics 与数据库有效 Evaluation 重算完全一致。
- 五维均值：technical=3.6667、coverage=2.6667、depth=2.3333、relevance=4.6667、communication=4.3333；
  overall_average=3.5333，来自 deterministic code，非 Coach 生成。
- supporting evaluation IDs 全部来自本场有效 Evaluation；recurring issue 至少两个不同有效引用，occurrence 由 resolver 计算。
- 7 条 PerformanceObservation、2 条 Proposal 均 PENDING；UserMemoryItem=0，旧 UserSkillProfile=0。
  没有 approve/correct/reject、自动 Memory commit 或任何 Profile 更新。

### A2.6 Usage / latency / repair

下表包含 Attempt 2 的完整历史：Matching 的一次超时，以及预算场次 Session 1 和成功场次 Session 2。
它不是只挑选成功请求；首次连通性 Probe 独立列在 A2.1，不计入此表。所有实际模型都是 DeepSeek，cost=NULL。

| Role | 请求数（成功/失败） | Prompt version | 累计 latency ms | 已返回 input | 已返回 output | 已返回 reasoning | Repair |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| Research | 2（2/0） | job_jd/quality 1.0.0 | 39967 | 1618 | 2578 | 2360 | 0 |
| Profile | 1（1/0） | resume_profile 1.0.1 | 49231 | 2106 | 3622 | 2863 | 0 |
| Matching | 2（1/1） | job_matching 1.0.0 | 172761 | 6971 | 9189 | 6283 | 0 |
| Interview Main | 6（6/0） | interview_main 1.0.0 | 86008 | 28677 | 5559 | 4598 | 0 |
| Evaluation | 5（5/0） | answer_evaluation 1.0.0 | 140046 | 23128 | 8885 | 7082 | 0 |
| Coach | 1（1/0） | interview_coach 1.0.0 | 73893 | 6574 | 4891 | 3570 | 0 |

已返回 usage 合计 input=69074、output=34724、total=103798；reasoning=26756 属于 provider 返回的 usage detail，
不重复叠加到 total。超时调用的 usage/cost 未返回，保持未知/NULL，不能声称以上是包括它的完整账单。
所有 JSON/Schema 均首轮合格，真实调用未触发 constrained repair；repair/hard-fail/fact preservation 由回归测试覆盖。

### A2.7 回归、安全与文件

- Real Model 完成后重新运行 backend：**201 passed / 116.41 秒**，0 fail/skip。
  原有 178 项全部保留，新增 23 项 fallback/state resolver/audit 测试，不减少原覆盖。
- frontend：**7/7 passed**；production build/vue-tsc 成功，150 modules；Python compile/static 和标准 git diff --check 成功。
- 唯一既有 pytest cache 权限 warning 保留；未升级依赖、未修改 uv.lock 或历史 migration。
- 正式库仍 `0024`，与 F1 最终快照全部 61 个业务表 count/hash 不变，无无效约束/index。
- `.env` 继续 Git ignore，仅按授权改 model/fallback 和等待预算；没有改 endpoint、API key 或管理员密码。
  secret 扫描未在 git diff、docs/logs/artifacts 发现真实 API key/管理员密钥或新增凭据泄漏。
  另确认 JWT 当前仍为既有公开开发默认值（与模板/default 相同）；这是既有安全收尾项，不是本次新增泄漏。
  F2 未擅自轮换 JWT 密钥或使已有登录失效，应在后续明确授权的安全收尾中处理。
- 原文前 9713 bytes 与 Attempt 1 文档 hash 一致，本节仅追加；Attempt 1 DB 与结果文件保持。
- 空闲 Worker 受控重启以加载新的配置/Prompt/resolver，ready 且既有 ping→pong；未投递业务任务/Agent。

本次增量：`.env`（ignored）、`.env.example`、core/config、infrastructure/models/{provider,router,structured}、
research/audit、candidate_profile/{schemas,evidence,prompts,agent}、F2 runner；
新增 `scripts/smoke_model_connectivity.py`、`tests/test_f2_model_state_resolution.py`；本文仅追加。
未改 Search/Fetch、Interview Controller、Report/Memory 业务规则、UI、旧 migrations、依赖或 JobPilot。

本地忽略记录：`f2-attempt2-connectivity.json`、`f2-attempt2-first-run.json`、
`f2-attempt2-before-budget-retry.json`、`f2-attempt2-final-result.json`、`f2-attempt2-final-db-audit.json`，
均在 `.runtime/finalization/`。失败/成功 Agent/Step、MatchAttempt 与两场 Session 全部保留在 Attempt 2 DB。
这些是 DEMO/Fixture 数据库，不是正式用户历史；正式库/volume/backup 未删除。

### A2.8 边界与停止

这是 **LIVE Model + LOCAL_FIXTURE/REPLAY data** 验收，不是“全部 Live Search E2E”。
Research fixture 发布仍 WAITING_REVIEW；三岗位与 approved 题库是明确 DEMO seed；简历报告是明确 Fixture 输入，
用户回答是固定 TEST answers。没有把它们称为真实招聘、真实个人简历或真人互动。

Live Search 继续冻结，未修改 Search Provider，也未测试备用模型的真实调用。
Default 60 秒对该 DeepSeek Matching/Coach 实测不够，等待配置已明确调整；既有保守 reservation budget 也可能
在很短的面试中提前结束，本轮只调整 smoke 子进程预算，不改冻结的 M4 policy。
六 Agent 全部成功后不再进行模型调用或扩展功能；临时 clients 已关闭，无临时 smoke API/Worker/Web 服务。
F2 结束，不进入 F3。
