# R128：真实体验、题量计数与记忆展示核查报告

基线：本地工作区（R127 改动未提交），本地 HEAD 仍 `2010d81`，**未 Push**。核查对象为展示库 `interview_agent_showcase_r123_20261117` 的 Session #628 / Report #13 / Proposal #11。全程 **0 模型 HTTP**，未重跑面试、未改真实评价、未删记忆、未新增 Skill、未修改 Evaluation 2.3.4。

## 1. 真正的问题

### 1.1 对话仍像连续技术问答

- #628 冻结 `interview_main 2.7.0`；Director 决策链为 `NEXT(agent, OPENING/COVERAGE_SWITCH)` → `FOLLOW_UP(MISSING_MECHANISM/ANSWER_TOO_GENERAL)`。
- Skill 指令写着「入门检查输入输出和链路理解；中阶追问实际机制及选型」，与 2.7.0 已有的「先了解背景/职责」原则叠加后，**首题直接进入技术流程**仍合法；评价缺口又推动连续两次同一项目 Mechanism 追问。
- 2.7.0 没有把「回答充分就换题」「换题必须由上一句回答自然引出」「临近结束收束当前话题」写成硬节奏约束；结束时前端直接把统计结果与「面试结束」一起显示，缺少收尾。

**判断**：不是状态机或评价错误，是 Main 对话节奏约束不够具体。旧版 v1 的「根据简历自然交流、逐层深入」原则值得吸收，但不复制旧 Prompt、不改变 Controller / 证据 / 独立评价 / 快照。

### 1.2 选 3 题只评价 2 题

真实数据：3 行题目中 `id=555` 是 `question_kind=INTRODUCTION` 且 `excluded_reason=INTRODUCTION`；另有 1 道 NEXT、1 道 FOLLOW_UP，对应 2 条有效评价。`completion_reason=QUESTION_COUNT_REACHED`。

根因：`controller.stop_reason()` 用 `len(questions) >= question_count`，把自我介绍也算进题量。所以「1 次开场 + 2 道技术题」就触发结束。代码里的 `question_count` 实际表示**所有面试官提问**，与用户理解的技术考察题数不一致。

### 1.3 进行中评分/答案可见性

只读核对前端：`EVALUATION` 消息在 `!interviewFinished` 时只显示「面试官已记下这段回答」；「展开参考答案 / 查看候选人依据」均要求 `interviewFinished`。**进行中已经隐藏，无需修复**。报告页仅在会话完成后可访问。

### 1.4 记忆保存后难找

Proposal #11 已 `APPROVED`，`memory_item_id` 指向 `user_memory_items.id=1`，数据完整。问题是体验：

- 报告页批准后的链接只是 `to="/memory"`，不带记录标识；
- 成长记录页把「能力记忆（系统推断）」面板放在「记忆列表」之前，1 条本人确认记录落在下方，缺少顶部入口与视觉定位；
- 批准成功提示没有说明保存位置，也没有直接定位链接。

## 2. 最小修复

| 文件 | 修改 |
|---|---|
| `apps/api/src/interview_api/modules/interview/adaptive_prompts.py` | 新增 `CONVERSATION_RHYTHM_CONTRACT` 与 `MAIN_CONVERSATIONAL_V4`（`interview_main` **2.8.0**）= 冻结的 2.7.0 原文 + 节奏约束：进入项目先背景/职责再机制；追问只针对上一回答的一个未解点；充分回答直接换题；自然承接、不照 rubric 点名；临近结束收束当前话题，不自行播报分数。没有写死示例问题，2.7.0 / 2.6.0 原文未动。 |
| `apps/api/src/interview_api/modules/interview/context_projection.py` | `expected_main` 白名单加入 2.8.0，保留 2.7.0/2.6.0/2.5.0/2.3.3，旧会话仍按原指纹运行。 |
| `apps/api/src/interview_api/modules/interview/controller.py` | `stop_reason()` 只统计非 `INTRODUCTION` 题目；完成摘要新增 `introduction_count` / `technical_question_count`，`question_budget` 保持用户选择的正式题数。 |
| `apps/web/src/pages/InterviewChatPage.vue` | 完成消息先自然收尾，统计数字移入默认折叠的「本场复盘」；复盘明确显示正式题数并说明自我介绍不计入。进行中隐藏规则未改。 |
| `apps/web/src/types/interview.ts` | 补齐完成摘要字段类型。 |
| `apps/web/src/pages/InterviewReportPage.vue` | 批准/修正成功后提示保存位置并给出带 `memory_id` 的直达链接；提案卡片链接也带 `memory_item_id`。 |
| `apps/web/src/pages/MemoryPage.vue` | 顶部新增「本人确认的记忆」摘要与直达入口；从 `?memory_id=` 进入时清空筛选、滚动并高亮对应记录；与系统推断能力记忆明确分开。 |
| `apps/web/src/components/CapabilityMemoryPanel.vue` | 标题改为「能力记忆（系统推断）」，说明与本人确认记忆分开存储。 |
| `apps/api/tests/test_r127_adaptive_recovery.py` | 新增 0 模型回归：3 题预算下自我介绍不占名额；版本断言更新到 2.8.0 且保留 2.7.0 冻结校验。 |

未改：Evaluation 2.3.4、真实评价/报告/记忆数据、权限与来源关联、Skill 数量、岗位库、其他页面。

## 3. 回归结果

- 新库 `interview_agent_v21_r128_20261118`，`APP_ENV=test`，`DATABASE_URL=M1_TEST_DATABASE_URL`，**112 passed**（含 R125/R126/R127 相关回归与新增题量用例），0 模型 HTTP。
- `vue-tsc --noEmit -p tsconfig.json`：0 error。
- 浏览器只读验证（0 模型调用）：Report #13 的提案链接为 `/memory?memory_id=1`；点击后进入成长记录，记录 `memory-1` 被滚动定位并高亮；顶部「本人确认的记忆」摘要与「能力记忆（系统推断）」标签均存在。截图：`data/r128_memory_jump.png`。
- 服务已用一键脚本 `start-showcase.bat` 重启，展示库与 revision 0030 不变。

## 4. 需要用户再次验证

1. **新开一场面试**：自我介绍后是否先了解项目背景/个人职责，再进入机制；充分回答后是否直接换题；两次追问是否仍连续发生；结束前是否有自然收尾。2.8.0 的具体对话质量只能由真实模型验证，本轮未调用模型。
2. **选择 3 题**：应得到 1 次开场 + 3 道正式题（其中可含追问），结束时复盘显示正式题数 3，自我介绍不计入。
3. **进行中页面**：确认仍看不到逐题分数与参考答案；结束后才显示。
4. **记忆定位**：在报告页批准一条提案后点击「前往成长记录并定位这条记忆」，应直接滚动并高亮该条；也可在成长记录顶部「本人确认的记忆」找到它，并与「能力记忆（系统推断）」区分。
