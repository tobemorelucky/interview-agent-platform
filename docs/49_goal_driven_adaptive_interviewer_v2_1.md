# V2.1：目标驱动的自适应面试官

日期：2026-10-22。状态：**实现、隔离 PostgreSQL、Replay、真实浏览器验收通过；直接授权后的真实 DeepSeek 批次在题源业务校验处失败停止，真实模型完整验收未通过。**

本文保留 V2.1 历史事实；后续题源契约修复及 Docker 集成阻塞见 [V2.1.1 记录](50_director_source_contract_v2_1_1.md)，不以新修复覆盖旧失败。

首次真实批次命令曾被执行环境安全审查拦截，彼时进程未启动、HTTP=0、Token=0。审查未将附件中的 V2.1 预算识别为直接费用授权。随后用户直接确认授权，才启动下文记录的唯一真实批次；保留首次拦截事实，不将其与后续实际调用混淆。

## 1. 代码基线及范围

进入时 HEAD=c2cb7ac，17 个 tracked 修改、7 个 untracked 文件属于已有 V1.4.2 工作，全部保留。本次未改 Profile、Matching、Research 抽取/发布、Memory 的领域实现，未新增 Agent、依赖、表或 migration，未修改 .env、模型路由或密钥，未 commit/push。

V1.4.2 的真实牛客 Run3/task4 仍是 FAILED；本次没有将其导入蓝图，也没有把它当作成功发布。原文锚点与规范化题目的审核要求继续有效。

### 新增文件

- `apps/api/src/interview_api/modules/interview/adaptive.py`：蓝图、观察、Director 决策契约及确定性解析。
- `apps/api/src/interview_api/modules/interview/adaptive_prompts.py`：独立、完整、可冻结的 2.1.0 Prompt。
- `apps/api/tests/test_v21_adaptive.py`、`test_v21_budget.py`、`v21_support.py`、`fixtures/v21_behavior_cases.json`。
- `apps/api/scripts/v21_acceptance_runtime.py`、`accept_deepseek_v21.py`。
- `scripts/run-v21-tests.py`、`v21_acceptance_web.mjs`。
- `apps/web/src/types/adaptive-interview.ts`、`apps/web/tests/adaptive-interview.test.mjs`。
- 本文档。

### 修改文件

- Interview：`schemas.py`、`context.py`、`agents.py`、`controller.py`、`core_service.py`、`router.py`、`skill_registry.py`。
- Report：`modules/interview_report/service.py`。
- Vue：`pages/InterviewChatPage.vue`、`InterviewReportPage.vue`、`types/interview.ts`、`interview-report.ts`。
- `docs/README.md`：仅追加本文索引，保留之前未提交的索引。

## 2. Interview Blueprint

创建请求新增可选 `adaptive_interview: false`。启用须选 Skill，不能与 `design_interview` 同时启用；非法组合返回 422。

蓝图在创建阶段冻结，不额外调用模型。目标来自本场 Skill.priority_topics，采用稳定的 `skill_id:topic`；保留 Skill/version、类别、目的、scope、source refs 和 Blueprint fingerprint。上限 min(8, question_count, 当前 Skill 主题数量)，内置三个 Skill 通常提供约五个目标，短面试可更少。

排序信号：JD 要求、已审核公共问题、候选事实、已批准记忆/近期观察中的相关主题。它们影响考察优先级，不作为掌握证明，不改变五维评分。Skill 能力目录决定可用目标，不由 LLM 新造知识点 ID。

V2 公司岗位题源比 V1 更保守：使用精确 job_id 关联、通过审核的问题及其已审核面经父记录；只有公司名、岗位未知的记录不猜关联。REPORTED_TOPIC 不作为原始题目进入，已审核面经中的有锚点 reported_topics 只提供方向性来源。

每个题源保留实际内容/指纹；公开问题可附 source_anchor。蓝图无资料时回退通用专业 Skill，不能推断某公司必考。每轮不再查询最新 JD/面经或付费搜索。

## 3. Main 内部 Director

没有第七个 Agent。沿用 InterviewMainAgent/route，V2 选择独立 AdaptiveDecision Schema；正常每轮一次 Evaluation、一次 Main，最后一轮程序结束时无需额外 Main。

决策字段：decision_version、strategy、target_knowledge_id、supporting_refs、reason_tags、proposed_question、question_source_ref、fallback_action。支持 FOLLOW_UP、REQUEST_EXAMPLE、DEEP_DIVE、NEXT。

程序提供当前合法目标和策略集合，不预先指定唯一目标、题源或动作。Main 根据实际回答、Evaluation、旧缺口、历史问题及蓝图选择。充分展示的目标排除；当前目标补问受现有追问额度和主题冷却限制。NEXT 问题必须明确涉及所选技术主题，避免只改日志标签而实际问另一主题。

REQUEST_EXAMPLE 映射为 FOLLOW_UP，消费同一根题的追问额度，沿用 follow_up SSE；不额外增加无限通道。DEEP_DIVE 保留原有 parent/provenance 与计数。

引用只接受本场来源及实际 `answer:<id>`、`evaluation_run:<id>` 等。当前评价尚未提交时引用真实成功的 AgentRun，而不是伪造未来 Evaluation ID；提交观察时由程序补真实 Evaluation ID。

非法目标/引用/题源/策略一律拒绝，记录 FAILED audit，Answer 保持可重试，不生成假下一题或默认评分。业务校验失败不自动再叫模型；只有既有 JSON/Schema constrained repair，最多一次。安全回退是 SAFE_RETRY，不是编造一个问题。

## 4. Evaluation → Director → Controller

1. 原短事务保存 Answer/Message，递增 state_version，保留幂等键。
2. 独立 Evaluation 输出五维评分和本轮 KnowledgeObservation。
3. 程序精确解析回答引用，计算尚未解决的 gap 集合，构造未持久化的本轮投影。
4. Main 在合法空间中选择策略和问题。
5. Controller 锁定 Session，重验 state_version、active_question、预算、追问及决策空间。
6. 同一事务持久化 Evaluation、观察、决策审计和下一题/完成状态。
7. commit 后才发送 evaluation/question/follow_up/complete。

V2 不再在提交时用 Evaluation 的建议重新推导 Main 动作。只重验合法性；提交期间硬预算耗尽时明确记录 NOT_EXECUTED_BUDGET，而不是无声替换策略。

程序结束条件保留原题数/轮次/时间/Token 上限。此外，为避免目标全部验证后重复提问，V2 在所有冻结目标充分展示且无旧缺口，或合法目标空间因既有冷却/追问额度耗尽时确定性结束。completion_reason 分别为 BUDGET_LIMIT、BLUEPRINT_COVERED、NO_LEGAL_TARGET_WITHIN_POLICY。最后一类不代表全部掌握，未解决项照常进复盘；Main Schema 没有 COMPLETE 权限。

## 5. 知识观察和持久化

状态：尚未观察由不存在的记录表达；有效观察为 INSUFFICIENT_EVIDENCE、PARTIALLY_DEMONSTRATED、DEMONSTRATED_IN_SESSION、NEEDS_CLARIFICATION。

- 每轮仅评价当前知识 ID，不能随意宣称其他能力。
- answer_quotes 必须是本轮实际回答中的连续原文；程序计算真实 offset。
- 正向/部分展示必须有回答引用。
- missing_mechanisms 形成确定性 gap ID；解决旧 gap 必须显式引用已有 ID，并有本轮回答依据。
- 有旧缺口未解决时，不允许标记 DEMONSTRATED_IN_SESSION。
- 状态变化保留 question/answer/evaluation IDs，不反写 Profile/UserSkill，也不批准 Memory。

复用：Snapshot.context_json.adaptive_policy / adaptive_blueprint；Session.interview_plan_json.adaptive_knowledge；planner_trace_json.adaptive_decisions；Evaluation.artifact_json；Question.candidate_refs_json.adaptive_decision；现有 AgentRun/StepRun。

JSON 使用 strict/versioned Pydantic Schema；最多八个目标、四十条观察，每轮一条、最多三个回答片段和五个缺口，文本有长度限制，总 JSON 默认 96KB 防护。超限明确失败，不静默删除证据。无 migration，head=0025；0018 SHA256 仍为 `3f4b951021427fbc3b629609897d3fa7cd732f400e00d8192a9223a90abdcd47`。

## 6. Prompt、上下文、旧模式

V2 Main/Evaluation 为独立 2.1.0 完整定义，正文、版本和 SHA256 固定在 Snapshot。后续模块常量更新不重写历史 Prompt。调用时校验指纹；发送给模型的 user context 只保留 Prompt fingerprint，避免重复发送两份系统指令。只投影蓝图用到的题源、候选事实和真实 Evidence。

原 Session 没有 adaptive_policy 就不走 V2，不回填历史知识状态。M4 和 V1.2 的 Schema、工具执行及旧 Prompt 继续使用。严格程度不改变评分和难度；Evaluation 沿用 style-blind 投影，温和模式的程序表达提示保留。

只读检查七个 route 均为当前配置的 `openai_compatible / deepseek-v4-pro-ga-260813 / thinking disabled`。这只是配置检查，不是本轮真实调用结果。

## 7. API/UI

API 路径不变：创建、start、messages/stream、详情、questions、skip、report。创建增加布尔字段，题目响应可增加 assessment_topic，报告详情增加 adaptive_review。

普通响应移除规划 Prompt、内部决策和知识账本；V2 公共题目录不作为未来题目预览。前端创建开关与系统设计模式互斥，显示本场固定范围和当前主题，沿用 loading、断连提示、原回答幂等重试。

报告按实际考察目标展示本场观察、待练习问题、真实回答片段及缺口补充过程。没有技能百分比，不将本场状态包装成永久掌握。Coach 的统计与 Memory 审批边界不变。

## 8. 两个实际流程实例（明确 Replay）

### 实例一：泛化回答 → 案例补问 → 解决缺口 → 切题

隔离库 `interview_agent_v21_acceptance_20261022_a`，Session75，四轮，标准风格。

- 开场考察缓存工程；输入“我主要用过缓存，但没有说清实现”。
- Replay Evaluation 记录回答依据不足及具体实现/恢复缺口；Replay Main 选择 REQUEST_EXAMPLE。
- 实际下一题要求用缓存请求为例，讲失败恢复；不是继续泛泛介绍项目。
- 第二答补充业务唯一键、数据库事务提交边界、失败恢复和结果确认。观察精确引用真实回答，解决原 gap；后续 NEXT 到数据库事务，再到 Java。
- 4 Answer /4 Evaluation，COMPLETED，报告 current 成功；刷新恢复。实际三个目标被考察，缓存补问不重复计算为新知识点。

### 实例二：失败/中断 → 原回答重试 → 正常完成

最终浏览器库 `interview_agent_v21_browser_20261022_a`：

- Session2：Replay 单次 model_timeout；页面仍是第1/2题，没有 Evaluation/下一题，显示已保存与安全重试；刷新、原键重试后完成。最终2 Answer /2 Evaluation，无开放题。失败 StepRun=model_timeout。
- Session3：温和风格，在 Replay 三秒延迟内真实刷新。取消审计 interview_request_cancelled，回答恢复、无重复；原键重试后完成2轮及 Report，刷新恢复。取消 StepRun 延迟260ms；这不是 DeepSeek latency。

截图与真实 DB 核对保留 ignored `output/playwright/v21-*.png`、`v21-final-db-results.json`。UI/API 没有 route.fulfill，也没有静态页面冒充业务。唯一被替换的边界是显式 Replay Model。

## 9. V1/V2 对照及指标边界

固定输入见 `fixtures/v21_behavior_cases.json`，四例：泛化、ACK机制缺口、补齐事务/幂等、Agent 权限/恢复缺口。

在相同 NEXT 评价建议下，V1 Controller 的受控动作均为 NEXT；V2 Replay 决策依次为 REQUEST_EXAMPLE、DEEP_DIVE、NEXT、DEEP_DIVE，已展示目标从合法空间排除。这证明新契约/执行能改变行为，不是证明 LLM 理解能力提高。Replay 选择规则是测试替身，不能用它声称真实模型质量优于 V1。

已核对：合法策略提交、补问相关输入、同知识点不重复计数、后续缺口解决、幂等、失败恢复、数据库完成状态、实际 StepRun。尚无同场景真实 V1/V2 模型质量统计；没有宣称提升百分比、掌握率或真实模型完成率。

Replay usage=0 是离线替身标识，不是实际模型 Token 测量。成功步骤模型替身本身常为0ms，不作为产品真实延迟指标。真实 latency/Token/策略质量仍待授权批次。

## 10. 测试与修复

- 最终全量后端分两批：134 passed/68.62s，340 passed/93.86s，合计 **474 passed，无 skip**，包含原455项+新增19项。保留真实 V1.4.2 Snapshot 的只读测试指定原隔离库，未减少旧覆盖。
- 前端 **41/41**（单元与原有静态契约混合），vue-tsc+production build 通过；不把这些项目全部称作浏览器 E2E。
- 真实浏览器完成四轮到报告、后续超时重试、断连刷新、温和提示、报告刷新。切换隔离数据库时旧 JWT 返回一次预期401，重新正常登录；不是绕过认证，不宣称控制台从未出现 HTTP 错误。
- Python compile、git diff --check 通过；历史 migration 没有变化。git 的 LF/CRLF 提示不等同 whitespace 错误。
- 单项测试命令设175秒上限；隔离数据不覆盖/删除，正式库不写入。

开发中保留的 RED→GREEN：缺失蓝图模块；假引用/非法目标拒绝；真实整轮 timeout 原先成为裸 TimeoutError 且误记取消，改为 agent 内整体 deadline，统一补齐 FAILED StepRun/Run 的 model_timeout；非法 Main 不提交评价并在原键重试复用已验证 Evaluation。测试曾用 Redis 题干假装切到其他目标，冷却正确拒绝；修正测试替身，不删除冷却断言。

最后校正了 Replay Coach 替身：不复用系统设计场景的“并发名额验证”文案，避免假装普通面试执行过工具。经现有明确 regenerate UI 生成 Session3 的第二版 current，第一版仍保留；页面及回答依据截图重新核对，最后页面 console errors/warnings=0。相关真实服务定向回归40 passed，增加断言后报告专项再次1 passed；没有把测试替身文案当作真实模型分析。

## 11. 真实模型预算及直接授权后的验收

已实现单批脚本 `accept_deepseek_v21.py` 和四项无网预算测试；最多12逻辑/24HTTP/100000Token/720秒，拒绝覆盖 attempt、单批文件锁、未知 usage 保守计入、收费/权限异常停止，不 fallback 到其他模型。

计划两场各三轮：Java Kafka ACK/事务/幂等，AI Agent 工具权限/状态/恢复。优先决策链，不额外生成 Coach。首次命令被安全审查拦截时请求为0；随后用户在对话中明确授权相同上限，才执行一次真实批次。不能将上述 Replay 实例换名成真实 DeepSeek 案例。

### 唯一真实批次（失败保留，不刷成功）

- 模型 `deepseek-v4-pro-ga-260813`，实际请求 `thinking.type=disabled`；model_mode=LIVE，data_mode=LOCAL_FIXTURE_DEMO，没有 Live Search、Replay Model 或 fallback。
- 独立库 `interview_agent_v21_live_20261022_a`；场景 A 的 DEMO user_id=18、Session33。不写正式库。
- 实际 **3 次逻辑 / 3 次 HTTP**，均200、finish_reason=stop、reasoning_tokens=0；**0 次 structured repair**。
- 实际 input_tokens=15,199，output_tokens=1,040，合计 **16,239 Token**；返回缓存命中合计2,048 Token（输入的子集，不能再次相加）。无价格结算配置，本次实际货币成本未核实，不自行估价。
- 整批记录耗时19.920秒，未突破12逻辑/24HTTP/100,000Token/720秒上限。失败后未继续消耗剩余预算。

| 阶段 | Schema | HTTP | 模型耗时 | 输入 Token | 输出 Token | 业务结果 |
| --- | --- | --- | --- | --- | --- | --- |
| A 开场 Main | AdaptiveDecision | 200 | 5,477ms | 4,660 | 323 | 通过并生成开场问题 |
| A 第一答 Evaluation | AdaptiveEvaluation | 200 | 6,222ms | 4,414 | 442 | 完成结构化调用，进入后续决策流程 |
| A 第一答后的 Main | AdaptiveDecision | 200 | 4,880ms | 6,125 | 275 | UNKNOWN_DIRECTOR_SOURCE，业务校验拒绝 |

实际开场要求解释 Kafka 消费成功、ACK 前崩溃时的业务幂等及数据库事务边界，未使用固定 Replay 文案。第一条测试回答明确没有考虑提交失败和重试。

失败位于 `adaptive.validate_decision()`：`question_source_ref` 不属于所选 `target_knowledge_id` 的 `sources`。这不是 HTTP、JSON、超时或 reasoning 问题；引用可以存在于其他上下文，但仍不能跨目标冒用。没有放宽校验、自动替换引用或重试模型。

当前审计不保存失败模型的完整正文；批次 JSON 仅有错误码，因此不能据此确定模型究竟填入哪个错误 ref、选择何种策略，不能臆测其完整后续追问。

恢复任务后的只读数据库核对受环境阻塞：PostgreSQL 连接拒绝，Docker Desktop Linux engine pipe 不存在；尝试启动已有 Docker Desktop 后仍不可用，未重建容器或修改数据。因而本次没有复核 Session33 最终 Answer/Evaluation 行数和状态、AgentRun/StepRun 状态及配额账本。源码路径是 Answer 先保存、评价先留审计，Main 验证通过后才整轮提交，异常经 `failed_answer()` 收敛；这是源码保证与先前回归覆盖，不等于本批次的数据库实测结果。没有把尚未核实的行数写成事实。

B_AGENT 未执行；两场三轮、实际缺口解决/切题质量、真实完整完成和报告效果均 **未通过本批次验证**。原有 Replay/浏览器通过不代替这些结果。原始批次记录保留 ignored `.runtime/acceptance/deepseek_v21_live_20261022_attempt1.json`，拒绝覆盖。

恢复任务后仅重跑无网络预算测试：4 passed；没有再启动真实请求批次。

## 12. 人工体验及局限

当前隔离 Replay Web：http://127.0.0.1:5177；API：http://127.0.0.1:8007。仅该库 DEMO 账号 `v21.demo@example.com` / `DEMO-v21-local-only`，不是正式凭证。

重新启动已准备的隔离环境：

```powershell
apps/api/.venv/Scripts/python.exe -X utf8 apps/api/scripts/v21_acceptance_runtime.py --database interview_agent_v21_browser_20261022_a
node scripts/v21_acceptance_web.mjs
```

登录 → 模拟面试 → 选岗位、Skill、轮次/风格 → 勾选自适应 → 新建4轮 → 泛化回答 → 查看补问 → 用具体机制补充 → 看主题切换 → 完成 → 生成报告 → 展开本场考察与回答依据 → 刷新。该专用 Replay runtime 只面向 V2.1，不用于声称普通/系统设计 LIVE 效果；旧模式兼容由真实数据库回归测试验证。

普通 LIVE 启动仍使用原 `scripts/start-interview-platform.ps1`；代码更新后需重启本项目 API。V2 需显式勾选，旧 Session 不升级。真实批次已执行但失败，不将此阶段标记为完整真实模型验收通过。

剩余限制：知识语义诊断仍依赖模型，程序验证引用和状态规则，不是语义正确性的完备证明；只支持当前 Skill 声明/已有主题识别覆盖；严格岗位FK会牺牲部分公司级资料召回；有限JSON可超预算明确失败；进程崩溃极小窗口仍依赖原恢复lease，不提供持久后台Agent任务。没有跨场次技能评分、通用工具循环、CHALLENGE/SCENARIO_CHANGE，不扩展 V2.2。
