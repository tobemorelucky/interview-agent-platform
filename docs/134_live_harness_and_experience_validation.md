# R134：真实模型验证 Main Harness、自主面经检索与修复效果

日期：2026-11-18　基线：`d97a260`　模型 HTTP：**15 / 15**　Token：**109,907 / 120,000**　DeepSeek Search/Fetch：**0**

本轮不新增 Skill/Agent，不改 Evaluation 2.3.4，不改历史会话，不自动批准记忆，不 push。所有付费请求统一计入上表；达到 15 次后停止，**未发生自动重试已产生费用的请求**。

## 一、验收结论速览

| 项目 | 结论 | 说明 |
|---|---|---|
| R133 七个失败测试 | **REPLAY_PASS** | 已清理；根因是夹具假设，不是业务校验 |
| R134 独立验收库 | **PASS** | 从 R132 库克隆，百度面经/审核/关联完整；R132 库未写 |
| P0 缺口工具与保底 | **FALLBACK_PASS** | 模型 0 次调用 `read_active_knowledge_gaps`，3 次非法 focus；确定性保底全部安全推进 |
| P1 模型自主调用面经工具 | **LIVE_PASS（调用正确） / FAILED（结果未用于提问）** | 首次调用被主题拼写拒绝；修复后真实返回面经，但最终题仍用 Skill 源 |
| P1 无面经岗位 | **LIVE_PASS** | 工具不可用，面试官基于 Skill/JD/简历正常提问并完成 |
| P1 按需读取改造 | **LIVE_PASS** | 6 份 Main 输入均未出现面经题干；非 Harness 与冻结旧会话保持预加载 |
| P2 面经抽取修复 | **REPLAY_PASS**；Live **NOT_VERIFIED** | 快照 52/53 离线复核通过；预算用尽，未做真实模型抽取 |
| P3 完整 Harness 面试 | **LIVE_PASS** | 2 题完成、评分、报告、2 条 PENDING 提案 |
| 仍未完成 | — | 模型使用检索结果出题、`read_active_knowledge_gaps` 的真实自主调用、面经抽取 Live |

## 二、测试前准备

### 1. HEAD、工作区与服务

- `git log -1` = `d97a260 fix(interview): recover rejected questions and read real 面经 lists`；工作区无已跟踪文件修改，只有历轮遗留的 `data/` 未跟踪产物。
- 8000（PID 205080）与 5173（PID 202984）均在监听；API 返回正常业务错误 `AUTH_MISSING_TOKEN`，说明服务在线。R129 保活任务 `QxmhdS` 到期，但不影响进程。
- R134 脚本直连独立库，不经过 8000，未改动正在运行的 R132 展示环境。

### 2. 独立验收库与数据

- 新建 `interview_agent_showcase_r134_20261118`：由 R132 验收库 `pg_dump -Fc` → `pg_restore` 克隆（2.7 MB dump）。R132 库、正式库、展示库均为只读引用。
- 核对：50 个岗位；`interview_experiences` 仅 id=1，**百度 / Agent Harness 研发工程师 / 一面 / APPROVED / reliability 0.72 / company_id=6 / job_id=31 / reviewed_at 非空 / source_url=nowcoder discuss 92692844920412979**；该经验 17 道题。这就是 R132 已核验的面经，来源、审核记录、公司岗位关联原样保留。
- 无面经对照岗位：job 34（`rag_retrieval`），库内 0 条审核面经。
- 回归库：`interview_agent_wb_r134_test_20261118` / `interview_agent_wb_r134b_test_20261118`，alembic `0030`。

### 3. 路由、密钥与额度（不打印凭据）

- `LLM_PROVIDER=modelscope`，实际路由 `modelscope / deepseek-ai/DeepSeek-V4.1-Flash-1017`，`api_key` 非空；`LLM_MAX_RETRIES=0`。
- 付费前账本 `user:21`：当日 48 次 / 423,882 计费 token，剩余 52 次 / 576,118 token，足以容纳本轮上限。
- DeepSeek Web Search/Fetch 本轮 **0 次**；P2 只读既有快照。

## 三、R133 七个失败测试的清理

原始 7 项：`test_main_harness` 2、`test_gap_scope_contract` 5，均在新库复现。

1. **6 项 StopIteration（夹具问题）**：6 个用例硬编码要求候选来源以 `skill:` 开头。R130 的 `exclude_source_ref` 会故意把当前目标上一题用过的来源从候选里移除，第二次决策起该来源不再存在。生产逻辑是正确的，不能改。
   - 处理：新增 `legal_pair()` 测试辅助函数，**仍然强制同一 knowledge_id、合法 strategy、来源来自冻结候选集**，只把来源选择放宽为"优先 skill，否则同目标其他冻结来源"。
2. **1 项预期 AppError（契约已变）**：`test_original_failure...[scoped]` 断言非法 `focus_gap_ids` 必须在 `answer()` 抛 `INVALID_QUESTION_GAP_SCOPE`；R133 已把它改成"审计 + 保留评价 + 确定性保底"。
   - 处理：改写为断言审计记录、`NEXT` 策略、空 focus、缺口仍保留、无 VERIFIED 记忆、token 不重复计；原两答轨迹由 legacy / multi_focal 变体继续覆盖。
   - 评价夹具从 `parent_status` 驱动改为回答文本驱动，因为保底 NEXT 没有 parent 链接。

改动仅涉及 `apps/api/tests/test_main_harness.py`、`apps/api/tests/test_gap_scope_contract.py`；业务校验一行未放宽。定向复跑 **`46 passed`**。

**干净库整体基准**（官方 `run-workbuddy-tests.py`，全新克隆库 `interview_agent_wb_r134d_test_20261119`，14 个面试核心文件，零模型 HTTP）：**`157 passed / 0 failed / 0 skipped`**。对比 R133 的 `142 passed / 7 failed / 1 skipped`：7 个既有失败全部清零，且本轮新增测试（含 R133 的 decision/fragment、R134 的 on-demand）全部通过。整个 `tests/` 目录（1,325 项）在该项目里依赖多轮累积的种子数据，全新库上大量用例因缺数据失败，不作为本轮基准。

## 四、P1 前置改造：Harness 面经按需读取

检查确认 R132/R133 的 Harness wire 仍把该岗位 17 道面经题全文放在 `snapshot.public_questions` 里，检索工具与预加载提供同一信息。R134 做以下最小改造：

- 新增 `MAIN_HARNESS_CONTEXT_HINTS_V5`（`2.4.0-harness.5`）：明确告知模型"面经题正文不在本次输入里，只能用受限工具读取"。新会话冻结 V5；V3/V4 仍在白名单，历史会话原样读取各自冻结文本。
- `context_projection.on_demand_reviewed_questions()`：仅对 V5 Harness 的模型副本，把冻结作用域内的 `public_question:N` 行压缩为 `id + dimension + ON_DEMAND_VIA_TOOL + tool`；真实快照与工具读取路径不变，ID 仍可作为合法 source ref。
- `context_availability.reviewed_experiences` 继续报告 `count / AVAILABLE_ON_DEMAND / tool`；无面经时报告 `NO_REVIEWED_EXPERIENCE_FOR_SCOPE`。
- 非 Harness 面试和旧 V4 投影保持全量预加载；不扩大公司、岗位、审核范围；不改冻结会话。

零 HTTP 测试 `test_r134_harness_on_demand.py`（3 项）验证：题干和 expected_points 不出现在 wire、ID 保留、scope 不出现在 wire、工具侧仍可读全文、无面经不 stub、非 Harness/旧 V4 不变。

**真实 wire 证据**：本轮 6 份 `MainHarnessAction` 输入（29–32 KB/份）扫描已知面经题干，命中 **0**。说明初始模型输入确实不再重复面经详情。注意：`answer_evaluation` 路由仍会携带当前目标冻结的 source context，因此个别评价输入会出现面经题干；这是评分语境，R134 按约束未改 Evaluation 2.3.4，列为残留项。

## 五、P0：知识缺口与确定性保底（真模型）

场景 A（session 641）与场景 B（session 642）在真实模型下各产生一次缺口决策；定向复测 session 643 再产生一次。

- `experience_agent_step_runs` 全程只有 **2 个工具步骤**，都是 `search_reviewed_interview_experiences`；**`read_active_knowledge_gaps` 真实调用 0 次**。
- 3 次 `interview_main_harness` 运行以 `INVALID_QUESTION_GAP_SCOPE` 结束（run 1277 / 1282 / 1286），且 `terminal=false`。说明模型确实无法在有开放缺口时给出合法 focus（空数组或越界 ID），严格校验没有被放宽。
- 服务侧保底每次都生效：`AuditLog` 写入 2 条 REJECTED（session 641/642），评价结果保留且每题恰好一条（641、642 各 2 条评价 / 2 个不同 answer_id），确定性问题用 `岗位要求里有一条：…` 的 `JOB_REQUIREMENT` NEXT 推进（q586 / q588），会话正常结束。
- 分类：**A（模型自行产生合法题）** = 开场 3 题由模型基于 requirement/candidate fact 直接产出；**B（调工具后合法）** = 0；**C（保底恢复）** = 3 次。不能把 C 说成模型正确使用工具。

## 六、P1：面经工具自主调用（真模型）

### 测试 A：百度 Agent Harness 岗位（job 31，harness on）

首次 session 641：模型**自主**发出 `search_reviewed_interview_experiences(topics=["Agent","多 Agent"], max_items=3)`。返回 `TOPIC_OUT_OF_SCOPE`：冻结作用域里是规范主题 `agent`，而模型写的是自然语言。最终题源是 `requirement:153`，**未使用面经**。

这是真实的功能缺陷，不只是提示词问题。修复：`_reviewed_experiences` 用与冻结作用域相同的 `mentioned_topics()` 归一化请求主题（`Agent` / `多 Agent` → `agent`，`RAG 检索` → `rag_retrieval`），仍只接受本场合法 candidate/target 主题，数据仍只来自冻结的精确公司岗位审核项；补零 HTTP 测试。

定向复测 session 643（同一岗位、同一 V5 Harness）：

- 工具名：`search_reviewed_interview_experiences`
- 真实参数：`{"topics":["Agent 评测","多 Agent 架构"],"max_items":3}`
- 返回：`OK / REVIEWED_EXPERIENCES`，`EXACT_FROZEN_SCOPE`，`experience:1`，`public_question:2 / 3 / 4`，例如"agent 修复代码的效果是怎么评判的？最后修复正确的评判维度有哪些？"，round=一面、reliability=0.72、reviewed_at 非空、来源 nowcoder 原链接，`trust=UNTRUSTED_DATA`。
- 工具后模型最终题：**"岗位要求里有一条：「理解ReAct、CoT、Plan-and-Execute及Agent基本原理」…"**，题源 `skill:ai_agent_application@1.0.1`，`tool_audit.used_tool_result=false`。

判定：**自主调用与受限资料返回 LIVE_PASS；"最终问题合理使用检索结果" FAILED**。没有伪造工具请求，也没有用后端接口代替模型行为；预算到 15 次后停止，未再尝试引导第三次调用。

### 测试 B：无可审面经岗位（job 34，harness on）

session 642 冻结 `scope_items=0`，模型未调用面经工具；基于 `fact:117` 与 JD `requirement:171` 正常出题，2 题完成后结束，评价正常。没有编造该公司历史面试题，也没有尝试付费预览推断。**LIVE_PASS**。

## 七、P2：面经抽取修复复核

只读复用 R134 库里 R132 已获取的两个完整正文快照，未调用 DeepSeek Search/Fetch：

| 快照 | 正文字符 | QUESTION | CONTEXT | ANSWER | 全部题目有逐字原文证据 |
|---|---:|---:|---:|---:|---|
| 52（携程） | 1,846 | 20 | 19 | 0 | 是 |
| 53（快手） | 7,261 | 8 | 2 | 206 | 是 |

- 原被拒的"项目介绍 / spring 的启动流程 / 分库分表"现在作为编号标题中的真实问题被识别，且能在原文精确 span 命中。
- ANSWER 块只作为答案计数，没有被提升为题目；付费预览相关行没有生成题目（0 条推断题）；不支持的行直接丢弃，不重建正文。
- 证据：`data/r134_audit/p2_offline.json`。**REPLAY_PASS**；真实模型抽取 **NOT_VERIFIED**（预算已用尽，明确不在本轮 Live 验收范围）。

## 八、P3：完整 Harness 面试

session 641（job 31 / `ai_agent_application@1.0.1` / harness `MAIN_HARNESS_V1`）：

1. 专业 Skill 与 JD/简历正常加载，MODEL 开场。
2. 2 道题合法提交（其中一题由缺口保底恢复），每题评价一次。
3. 状态 `COMPLETED`；报告 id=19 `COMPLETED`（`SUMMARY_COACH_AGENT`）。
4. 记忆提案 id=18/19 均为 **PENDING**（`PREPARATION_FOCUS`、`SKILL_OBSERVATION`），未自动批准、未写长期记忆。
5. 面经可作为题源的能力在本场只由工具返回验证，最终题没有采用面经题源。

**LIVE_PASS（完整流程）**；"面经作为实际出题来源"仍是 NOT_VERIFIED。

## 九、预算、失败与恢复统计

- 模型 HTTP **15**（Main 8、Evaluation 6〔5 次调用中 1 次结构化修复计 2 次 HTTP〕、报告 Coach 1；另有 2 次零费用工具步骤）；Token **109,907**；Search/Fetch 0；自动重试 0。
- 账本明细：`data/r134_audit/live_evidence.json`（11 次 / 77,780 token）、`data/r134_audit/live_retest.json`（4 次 / 32,127 token）、逐条 step 在 R134 库 `experience_agent_step_runs` id 1276–1291。
- Harness run：SUCCEEDED 3、FAILED 3（全部 `INVALID_QUESTION_GAP_SCOPE`，全部 `terminal=false` 并由系统保底）；工具调用 2 次（1 次 ERROR、1 次 OK）；未解析错误 0。
- 预算在第 15 次调用后停止，没有为了凑测试项继续付费；无法确认费用或状态的情况未出现。

## 十、最终仍未完成的能力

1. **模型使用检索结果出题**：真实工具调用已成功、数据正确，但最终问题仍选 Skill 源；需另行设计提示/候选呈现方式并在新预算下验证。
2. **`read_active_knowledge_gaps` 的真实自主调用**：三种真实决策均未调用；R133 的确定性保底已证明安全，但"模型先读缺口再合法追问"没有 LIVE 证据。
3. **面经抽取的真实模型验证**：已完成离线/回放验证，未做 Live 抽取。
4. **Evaluation wire 中仍存在冻结来源正文**：本轮按约束未修改 2.3.4，仅在报告中记录。

## 十一、本轮代码变更

- `apps/api/src/interview_api/modules/interview/adaptive_prompts.py`：新增 `MAIN_HARNESS_CONTEXT_HINTS_V5`。
- `apps/api/src/interview_api/modules/interview/context.py`：新 Harness 会话冻结 V5。
- `apps/api/src/interview_api/modules/interview/context_projection.py`：新增按需投影 `on_demand_reviewed_questions()`；V3/V4/V5 白名单兼容。
- `apps/api/src/interview_api/modules/interview/main_harness.py`：面经工具请求主题归一化，作用域不变。
- 测试：`test_main_harness.py`、`test_gap_scope_contract.py`（旧夹具清理）、`test_r132_experience_tool.py`（主题别名）、新增 `test_r134_harness_on_demand.py`。
- 脚本/产物：`scripts/r134_live_accept.py`、`data/r134_audit/`。
