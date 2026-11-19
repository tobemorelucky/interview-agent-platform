# R136：真实面经检索、选择参考题源与前端接入 — 完成报告

日期：2026-11-19　前置：R135（`79bd706`，CONDITIONAL GO）　本轮提交：见文末（本地，不 Push）

## 结论：GO

R135 的两个 CONDITIONAL GO 阻断项在本轮均解决：

- **P0 面经检索结果未用于提问** → 定位为**呈现分离**（不是 Prompt 不足），改数据结构后真实模型自主选择面经题源成功，`LIVE_PASS`。
- **P1 前端从不传 main_harness** → 增加用户可见的「参考真实面经（如有）」开关并打通创建链路，`LIVE_PASS`（真实 HTTP 创建/开始/完成）。

完成标准逐条核对：

| 最终成功标准 | 结果 | 证据 |
|---|---|---|
| 前端可选择有面经岗位并开始面试 | LIVE_PASS | 前端开关→`main_harness` 参数；review 场景经真实 API/service 创建 session 650 |
| 面试官自主查询同岗位面经 | LIVE_PASS | tool step 1323 `search_reviewed_interview_experiences` → `REVIEWED_EXPERIENCES`，返回 3 题 |
| 自主选择合法面经题源并提问 | LIVE_PASS | run 1312 `reviewed_usage=REVIEWED_SELECTED_AS_SOURCE`、`question_source_ref=public_question:3`；Q601 `source_question_id=3` |
| 正常回答、评分、追问 | LIVE_PASS | session 650 两题（Q601 面经题 + Q602 合法 FOLLOW_UP），2 答 2 评，COMPLETED |
| 报告可追溯真实面经来源 | LIVE_PASS | Q601 → interview_questions#3 → experience#1（百度），`source_question_id` 落地；full 场景报告 #21 |
| 无面经岗位正常回退 | LIVE_PASS | session 652（job 34）`count=0`、未调用工具、基于项目事实出题、COMPLETED |
| 不重复评分、不超预算 | LIVE_PASS | 各会话 eval 数=答数；本轮 19/23 HTTP、162,318/182,824 token |

---

## 1. 搜索 / 抽取 / 审核 / 入库 完成度

| 环节 | 状态 | 说明 |
|---|---|---|
| DeepSeek Web Search/Fetch | 已有真实数据（R132） | 本轮 0 次，复用快照 |
| WebCollectionService / PublicDataResearchAgent | 已接通 | R132 采集链，未改动 |
| Extraction / Routing / Reliability / Quality Gate | 已接通 | R135 真实抽取 2 次（快照 52/53），本轮复用 |
| 管理员审核发布 | 已接通 | `ExperienceReviewService.approve` + `_match_company_job` 精确关联 |
| 已审核面经入库 | 已接通 | `InterviewExperience` APPROVED + 题目落 `interview_questions` |

**当前已审核可用面经覆盖：1 家公司 / 1 个岗位**（百度 / Agent Harness 研发工程师 job 31，17 题，reliability 0.72，reviewed_at 2026-11-18）。

携程、快手保持**待审核候选，未发布**，依据既有审核决定，不是遗漏：

- **携程**（快照 52，20 题，正文完整 1846 字）：题目"Java后端开发"与库中唯一携程岗位 job 49「Java开发工程师（2027届秋招）」标题不一致，`_match_company_job` 返回 `job_id=None`。按"不强行挂载岗位"原则不发布。
- **快手**（快照 53，8 题）：正文仅约 40%，含「剩余60%内容订阅专栏后可查看」付费墙。按"付费预览不推断隐藏正文"原则不发布。

R135 真实抽取证据复核（本轮零 HTTP 读取产物）：快照 52 → 20 题、快照 53 → 8 题，28/28 逐字命中原文，CONTEXT/ANSWER 未冒充题。抽取成功 **≠** 审核通过，二者严格区分。

---

## 2. P0 根因与修复：检索结果 → 实际题源

### 2.1 R135 真实会话复盘（零 HTTP）

复盘 R135 的两次 `search_reviewed_interview_experiences` 调用（p01/p02，session 644/645）：

| 检查项 | 结论 |
|---|---|
| 工具返回 experience_id / question_id / source_ref / candidate_id | 全部具备：`experience:1`、`public_question:2/3/4`、合法 `C_052…/C_8a2…/C_33f…` |
| Main 第二次收到的 tool_feedback | 完整（含题干 + candidate_id）；R135 wire_tap 证据里显示为空是**证据解析漏读 `result` 层**，非注入丢失 |
| Director 合法候选 / 来源绑定 | `public_question:2/3/4` 均为本场合法候选且带 NEXT；`resolve_selection` 会把 candidate_id 解析成 `question_source_ref=public_question:N` |
| 模型最终选 Skill 的真实原因 | **呈现分离**：V5 按需投影把面经题正文从 `snapshot.public_questions` 和 `adaptive.candidates` 中抹掉，面经候选在决策点只有 `candidate_id + FUNDAMENTALS`、无题干无审核标记；而 skill 候选有 `FUNDAMENTALS + SCENARIO` 且被 Skill 指令反复强调。模型做 selection 时看不到面经候选题目，退回熟悉的 skill 链 |
| `used_tool_result` 计算 | 准确：`source_ref ∈ 工具返回 refs`，R135 选 skill 故 false，无"实际参考但漏记" |

**根因是候选呈现/信息投影，不是 Prompt 不够，也不是题源绑定缺失。**

### 2.2 修复（数据结构，不新增 Prompt）

`apps/api/src/interview_api/modules/interview/main_harness.py`：

1. **新增纯函数 `bind_reviewed_questions_to_candidates(wire, feedback)`**：在工具返回后，把**已由工具真实返回**的面经题正文，按 `(source.ref, candidate_id)` 精确匹配，回填到本轮模型 wire 的候选项上（`reported_question` / `reported_dimension` / `reported_source_ref` / `reported_source_url` / `review_evidence`）。这样模型选 candidate_id 时题干与审核证据就在同一候选项上可见。
   - 防御性：只认成功的 `REVIEWED_EXPERIENCES`；候选必须已存在且 ref/candidate_id 精确匹配；只加呈现字段，不改身份/来源/目标；不改冻结 payload；返回新结构，不改输入。
2. **`_loop` 构造 packet 时应用绑定**（仅 `feedback` 非空时），只覆盖模型 wire 的 `adaptive`，冻结真相不动。
3. **四分类审计**：`_tool_audit` 增加 `reviewed_retrieved_refs/count` 与 `reviewed_usage ∈ {REVIEWED_SELECTED_AS_SOURCE, REVIEWED_BACKGROUND_ONLY, PRELOADED_WITHOUT_TOOL, NO_REVIEWED_QUESTION}`，把"检索到 / 选择为题源 / 仅背景参考 / 预加载未检索"分开。`used_tool_result` 语义保持不变（兼容 R133/R135 契约）。

零 HTTP 测试：`apps/api/tests/test_r136_candidate_answerability.py`（5 项：正确绑定、错误/空反馈不绑定、id 不匹配/stale ref 不绑定、只增强已有候选、四分类审计）全绿。

### 2.3 真实模型证明（LIVE_PASS）

| 项 | 值 |
|---|---|
| 会话 / 场景 | session **650**，`review` 场景 |
| 主 Run（采用面经） | run **1312**，`interview_main_harness`，SUCCEEDED |
| 工具调用 | step **1323** `search_reviewed_interview_experiences` → `REVIEWED_EXPERIENCES`，返回 3 题 |
| 审计分类 | `reviewed_retrieved_count=3`、`used_tool_result=true`、`reviewed_usage=REVIEWED_SELECTED_AS_SOURCE`、`selection_source_ref=public_question:3` |
| 实际题目 | Q**601**：`source=PUBLIC_QUESTION`、`source_question_id=3`；原题「项目里除了修复 agent 以外还有其他 agent 吗？这是一个多 agent 架构的项目吗？」→ interview_questions#3 → experience#1（百度） |
| 绑定可见证据 | wire1：3 个面经候选 `reported_question` 非空，`review_evidence={reviewed_at, reliability:0.72}` |
| 后续 | Q**602** 合法 FOLLOW_UP（基于回答追问），run 1314 `NO_REVIEWED_QUESTION`（未再调用工具，正常） |
| 评分 | 2 答 2 评，无重复；session COMPLETED |

模型在看到带题干与审核证据的面经候选后，**自主选择**了与回答主题契合的 `public_question:3` 作为题源。没有伪造来源、没有强制采用、没有人为改写 source_ref。

---

## 3. P1：Harness 接入普通前端入口

文件：`apps/web/src/types/interview.ts`、`apps/web/src/pages/InterviewChatPage.vue`

- 创建向导第 6 步新增用户可见开关 **「参考真实面经（如有）」**，仅在"是否自适应"开启时显示；关闭自适应或开启系统设计验证时自动关闭（与后端 `main_harness` 必须配 `adaptive_interview` 的约束一致）。
- 面面向用户语言，不出现 Harness / Tool Schema / Run ID 等内部词；明确提示"可能带来额外的模型调用"、"没有面经时按专业方向与岗位要求正常提问"。
- `InterviewerSelection` 加 `main_harness?`，经既有 `...interviewerSelection` 展开进创建请求 body；计算属性里 `main_harness = adaptiveInterview && reviewExperienceRef`。
- 默认关闭，不影响旧会话；不新增 Agent；不改变评测/评分/报告/记忆体系。
- 校验：`vue-tsc --noEmit` 通过，`npm run build` 通过。

端到端：review / full / noharness 三个场景均通过 service 层走真实创建→开始→回答→评价→报告，session 650/651/652 全部 COMPLETED。前端创建参数路径与验收脚本一致（同一 `CreateSessionRequest.main_harness`）。

---

## 4. 完整面试与报告（full 场景）

- 会话：**651**，4 题（Q603–Q606），4 答 4 评，状态 COMPLETED。
- 报告：**#21**，`generation_status=COMPLETED`，`is_current=true`。
- 成长记忆提案：**#23/#24/#25** 全部 `PENDING`，未自动批准（符合约束）。
- 该场模型未调用面经工具（`NO_REVIEWED_QUESTION`），属正常——不强制每轮检索/采用。
- 来源追溯能力已由 review 场景的 Q601 证明（面经题落地到 `source_question_id` + experience_id）。

---

## 5. 模型 HTTP / Token 账本

沿用 R135 统一累计预算，不重置：

| 项 | 历史(R135) | 本轮(R136) | 上限 | 结论 |
|---|---:|---:|---:|---|
| 模型 HTTP | 27 | **19** | 本轮 +23 | 未超（余 4） |
| Token | 217,176 | **162,318** | 本轮 +182,824 | 未超（余 20,506） |
| DeepSeek Search/Fetch | 0 | **0** | 0 | 符合 |
| 自动重试 | 0 | **0** | 0 | 符合 |

本轮 19 HTTP 构成：`interview_main` 10 + `answer_evaluation` 8 + `interview_coach` 1；另有 2 次零费用工具步骤（search 1、read gaps 1）。按时间基线 `created_at >= 2026-11-19 04:52:00 UTC` 精确核算，排除了克隆库从 R135 继承的抽取 run（step 1320/1321）。费用无法从本地核实，明确标注**未知**，未以估算冒充实测。

工具调用：面经检索 1 次（成功，返回 3 题，被采用）、缺口读取 1 次（成功）。四分类统计见 §2.3 与数据文件 `data/r136_audit/r136_*.json`。

---

## 6. 零 HTTP 与真实调用测试

| 测试 | 结果 |
|---|---|
| 后端回归（16 个面试核心文件，R136 测试库 `interview_agent_wb_r136_test_20261119`） | **164 passed / 0 failed** |
| R136 新增测试 `test_r136_candidate_answerability.py` | 5 passed（含在 164 内） |
| 前端 `vue-tsc --noEmit` / `npm run build` | 通过 |
| 真实模型 3 场景（review / full / noharness） | 全部完成，无错误，无重试 |
| confirm_unknown_retry | **REPLAY_PASS**：链路完整（onError meta → 确认框 → 同一 idempotency_key 重试），tsc/build 通过；未做真实故障注入（遵循"不人为破坏真实会话"约束） |

真实调用无失败、无需保底恢复。本轮未触发确定性保底（gap-scope 严格校验保持原样，未放宽）。

---

## 7. 仍未完成 / 边界

1. **已审核面经仅 1 公司 1 岗位**（百度 job 31）。携程因岗位标题不明确、快手因付费墙不完整，保持待审核候选。扩大覆盖需要补充真实完整面经并走人工审核，非本轮范围（用户要求优先少量真实案例）。
2. **面经采用非每次必然**：模型只在检索结果与当前回答/岗位高度契合时才采用（本轮 review 场景 2 题中采用 1 题）。这是正确行为，不强制。
3. **confirm_unknown_retry 未做真实故障注入验收**（REPLAY_PASS），需预发环境触发一次 `INTERVIEW_OUTCOME_UNKNOWN` 做 LIVE 确认。
4. **费用**无法本地核实，账本只记 HTTP/Token。
5. Evaluation 2.3.4、历史 Prompt、已有会话记录均未改动；正式库（`interview_agent`）未触碰；所有验收在独立库 `interview_agent_showcase_r136_20261119`。

## 8. 工程交付

- 验收库：`interview_agent_showcase_r136_20261119`（克隆自 R135，0030，百度面经完整）。
- 测试库：`interview_agent_wb_r136_test_20261119`。
- 证据：`data/r136_audit/`（`r136_review.json` / `r136_full.json` / `r136_noharness.json`、运行日志）。
- 改动文件：`main_harness.py`（绑定 + 四分类审计）、`test_r136_candidate_answerability.py`（新）、`interview.ts` 与 `InterviewChatPage.vue`（前端开关）、`scripts/r136_live_accept.py`（验收脚本）。
- 本地提交：见下。**未 Push。**
