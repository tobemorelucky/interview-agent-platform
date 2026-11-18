# R127：自适应出题中断、异常页面布局与真实面试官对话修复

本轮基线是本地最新提交 `2010d81`（R126）。远端 GitHub 不作为运行版本依据；本轮**未 Push**，代码保留为工作区改动。模型 HTTP 预算 0；未修改 Evaluation 2.3.4、未新增 Agent/Skill、未重导岗位库、未改冻结的历史 Prompt、未改正式库、未清理或重建 #627。

---

## 1. #627 的真实故障根因

取证来源：展示库 `interview_agent_showcase_r123_20261117`（alembic 0030）的 Session、问答/评价行、`experience_agent_runs` / `experience_agent_step_runs` / `interview_agent_calls` 账本、冻结快照、`planner_trace_json` 与服务端日志。

调用链状态（三件事必须分开看）：

| 状态 | #627 实际情况 |
|---|---|
| 回答是否持久化 | **是**。`interview_messages` id=2194 保存了「就是各自算各自的，按排名来」，`interview_answers` id=551 存在（幂等键 `ad1438bd-…`），但状态为 `FAILED`、error=`ADAPTIVE_TOPIC_MISMATCH` |
| Evaluation 是否成功返回 | **是**。run 1222 `SUCCEEDED`，Evaluation 2.3.4，五维 2/1/1/3/2，6,395 token；`answer_evaluation` step 1221 成功 |
| Evaluation 是否写入业务表 | **否**。`interview_evaluations` 没有 answer_id=551 的行——Main 失败发生在 `commit_answer` 之前，整个业务事务未提交，已付费的评价产物被丢弃 |
| 下一题 Main 是否调用 | **是**。run 1223，Main 2.6.0，14,868 token，model step 成功产出题目 |
| NEXT 目标 / 来源 | `target_knowledge_id = ai_agent_application:python`，`candidate_id = C_7e2f4ac…`，`question_source_ref = requirement:174`（冻结 JD：「熟悉Java/Python/Go之一，了解Spring Boot或FastAPI」） |
| 校验为何失败 | `validate_decision` 要求 NEXT 的问题文本命中 `TOPICS['python']`（`Python/GIL/协程`）。被拒问题原文未持久化，且 answer 路径没有写 mismatch 审计（只有 start 路径写了），因此**无法从 DB 还原原文**；不能断言一定是模型偏题 |
| 重试会怎样（修复前） | `prepare_answer` 把 FAILED 回答重新置为 RUNNING，`adaptive_answer` 会**再调一次 Evaluation + 再调一次 Main**，双计费且仍可能因同一问题失败 |

判断：这是**两层叠加故障**。

1. **校验层**：来源本身是冻结的 Python 要求，但门禁只认问题里的字面关键词。来源已把题目与目标绑定时，自然中文提问会被误判；真正偏题（例如问题明确在聊 RRF 召回）与「自然但未报关键词」无法区分。
2. **流程层**：Evaluation 与 Main 两个模型调用之后才落业务表；Main 校验失败时把已成功的 Evaluation 一并丢弃，且没有检查点、没有 mismatch 审计，导致不能安全恢复。

已消耗 Token（账本实测）：#627 共 13 次模型调用，**133,024 token**；其中失败这一轮为 Evaluation 6,395 + Main 14,868 = **21,263 token**。5 条已写入的评价 3 题有效计分（第 6 题评价未落库，不计分）。

---

## 2. NEXT 主题校验的具体修复

### 2.1 从「关键词报幕」改为「来源绑定 + 冲突检测」

`apps/api/src/interview_api/modules/interview/adaptive.py`：

- 新增 `frozen_source_text(payload, source)`：读取冻结的 JD 要求 / 简历事实 / 公共题原文，绝不读模型生成文本。
- 新增 `source_grounds_topic(...)`：`build_blueprint` 只会把提到该主题的冻结文本挂到目标上，因此来源本身提到目标主题即证明语义绑定。
- 新增 `conflicting_topics(...)`：问题里若**明确出现另一个技术主题**（如目标 Python、问题却在聊 RAG/召回），仍然拒绝。
- `validate_decision` 的 NEXT 门禁改为：
  - 问题字面命中目标主题 → 通过（原规则保留）；
  - 或来源为冻结证据且该文本提到目标主题，且问题没有点名冲突主题 → 通过；
  - 通用 Skill 来源（无冻结文本）仍必须自然说出自己的目标；
  - 真偏题继续 `ADAPTIVE_TOPIC_MISMATCH`，**不放行**。
- 没有删掉主题校验；`MENTION_TERMS` 仅作为提示，不再要求逐词复述。
- 拒绝信息随 `topic_diagnostic` 记录 `source_ref / source_grounded / conflicting_topics / question_excerpt / sha256`，answer 路径也写审计（此前只有 start 路径写）。
- 中途失败时文案不再提示「改用岗位要求开场」；只有 `opening_slot` 为真（还没有技术题）才保留首题恢复选项。

### 2.2 校验失败后的零模型保底

- `fallback_selection(payload, rejected_target)`：按「其他目标+冻结来源 → 同目标+其他冻结来源 → 通用 Skill 目标」顺序挑合法候选，用冻结证据构造**全新**问题，再走同一个 `validate_decision`。被拒问题原文永远不复用；不会为了过门禁往问题里塞无关「Python」。
- 允许 NEXT / FOLLOW_UP / 项目开场保留各自既有合法性条件；FOLLOW_UP 和项目开场不受 NEXT 字面门禁约束。

---

## 3. 已付费评价的可恢复检查点

`controller.checkpoint_evaluation()` 在 Evaluation 成功后、Main 调用前，把**已校验的 artifact**、provider/model/prompt_version/usage/run_id 写入会话自身的事务性 JSON（`answer_recovery`），不改变 `state_version`、不提前计分：

- Main 校验失败 → 审计 + 记录 `NEXT_REJECTED` 诊断 → 用零模型保底题目继续 `commit_answer`，评价只写一次。
- 重试同一幂等键 → `recovered_evaluation()` 先读检查点；旧会话没有检查点时，只读 `interview_agent_calls` 指向的 SUCCEEDED Evaluation run（#627 run 1222 即属此类），复用产物，**不再调用 Evaluation**。
- 已有 `interview_evaluations` 行时一律不复用，防止重复计分。
- 成功 commit 后清理 `answer_recovery`。

没有伪造评价、没有绕过幂等、没有重复计分；保底不可用时仍明确报错并保留检查点，不会静默换题。

---

## 4. 页面错位的根因与修复前后截图

根因：`InterviewChatPage.vue` 模板把 `pageError`（`<p class="ux-empty">`）和 `streamErrorCode`（`<details class="ux-technical">`）直接放在 `.interview-page` 下，与 `.session-sidebar`、`.chat-main` 同为横向 flex 直接子元素。`ux-empty` 的 `max-width:960px; margin:20px auto` 在 flex 中占据一列宽度，把历史栏推到中间、聊天区挤到最右。

修复：错误区移入 `.chat-main` 顶部（`.chat-error-area`），`flex:none / min-width:0 / overflow:hidden`；聊天列 `overflow-x:hidden`、消息区 `overflow-x:hidden`；错误详情用原生 `<details>` 默认折叠。侧栏仍为固定 260px，聊天区保持 `flex:1`，输入区与结束按钮不参与错误宽度分配。

| 状态 | 截图 |
|---|---|
| 修复前（按旧 DOM 结构在浏览器中复现） | `data/r127_ui/00_1440_before_injected.png` |
| 修复后 1440px | `data/r127_ui/01_1440_recovery.png` |
| 修复后 1024px | `data/r127_ui/02_1024_recovery.png` |
| 修复后 820px | `data/r127_ui/03_820_recovery.png` |
| 修复后 680px（响应式断点） | `data/r127_ui/04_680_recovery.png` |
| SSE/HTTP 422 长错误文本 + 详情展开 | `data/r127_ui/05_422_sse_error.png` |
| 断线恢复文案 | `data/r127_ui/06_disconnect_recovery.png` |

浏览器实测：1440/1024/820/680px 下 `body.scrollWidth - innerWidth = 0`、`.chat-main.scrollWidth - clientWidth = 0`；错误区在聊天列内部；结束按钮与恢复按钮的 `elementFromPoint` 命中自身，未被遮挡；刷新后 recovery 文案与按钮保持。

> SSE 错误、HTTP 422、断线恢复使用页面内 DOM 注入 + 刷新验证**布局与文案**，没有在 #627 上点击恢复按钮，也没有触发任何真实模型请求。

---

## 5. 自我介绍开场未生效的根因

三层默认值不一致：

- 后端 HTTP 路由 `POST /sessions/{id}/start` 默认 `opening=INTRODUCTION`（R126 已改）；
- 但 `core_service.start()` 的默认参数仍是 `MODEL`，内部调用者会得到模型开场；
- **真正导致 #627 的是前端**：`apps/web/src/api/interview.ts` 的 `startInterview()` 默认 `opening='MODEL'`，并在 `handleStart()` 中显式发送 `?opening=MODEL`，覆盖了服务端默认。于是首题走 Main 的项目开场（sq549，`source=CANDIDATE_PROFILE`，`question_kind=NEXT`），R126 的零模型 `INTRODUCTION` 路径根本没进。

修复：前端默认改为 `INTRODUCTION`，类型同步；用户侧统一由路由 + Web 客户端决定开场。`core_service.start()` 的 `MODEL` 默认保留给内部/脚本化调用者——把这个默认值也改掉会让大量既有脚本化回归的轮次语义漂移（实测 10 个用例失败），属于无关改动，故不修改。

展示效果：自我介绍题显示「开场 · 自我介绍（不计技术分）」，正式题从 1 开始编号（`formalRound` 跳过 intro），且该回答走 `introduction_answer`，不评分、不进技术统计，与 `END_REQUEST` 一样被排除。

---

## 6. 旧版与新版面试官策略的具体差异

对照 `interview_interviewer_system_v1.md`、`interview_question_evaluation_v1.md`、当前 `interview_main 2.6.0` 与 Skill 轮次策略：

| 维度 | 旧版 v1 | 本轮新策略（2.7.0 契约文字） |
|---|---|---|
| 开场 | 「自我介绍或简历第一个项目」，一次性问 1-2 个问题 | 零模型自我介绍由系统保证；之后由候选人经历决定方向，首次进入项目先问背景/职责/整体思路 |
| 驱动方式 | 模型自行按 resume + 知识库 + 历史摘要写自然语言 | 保留 Controller + 冻结候选/来源对 + 独立 Evaluation + 快照；模型只选合法目标/来源并写自然语言 |
| 深入方式 | 「由浅入深追问」，容易连着挖同一项目 | 一问一考察点；由本轮回答决定 0/1/2 次追问，充分回答直接换题 |
| 语气 | 「先简要评价，再追问」，容易每句复述 | 不逐句复述；短承接即可；禁止固定句式「这块先放一放」「那最后简单聊一句」「请说清楚具体机制」等 |
| 覆盖范围 | 依赖知识库题目填充 | 可从项目切到岗位 Skill 覆盖的基础/场景题，但不为覆盖打断合理技术讨论 |
| 事实边界 | 依赖模型自觉 | 明确不得编造候选人没说过的实施细节；来源与目标由服务端冻结，不因措辞改变 |

没有回滚状态机，也没有把旧 Prompt 整段复制进新版本。

连续对话用例（脚本化，非真实模型效果）在 `tests/test_r127_adaptive_recovery.py`：自我介绍后进入项目 → 模糊回答澄清 → 充分回答直接换题 → 切到 Python 基础；同时验证自然来源绑定 NEXT 通过、真偏题 NEXT 拒绝、拒绝后零模型恢复。R125 的三组 ≥5 轮连续脚本对话也全部通过。

---

## 7. 新 Prompt 版本与兼容性

- 新增 `MAIN_CONVERSATIONAL_V3`（`interview_main` **2.7.0**）= 冻结的 2.6.0 原文 + `NATURAL_DIALOGUE_CONTRACT`；**没有改写 2.6.0 的任何文字**。
- `freeze_prompts(context_projection=True, gap_scope=True, evaluation_boundaries=True)` 对新会话冻结 2.7.0；`answer_evaluation` 仍是 **2.3.4**，未修改。
- `context_projection.expected_main` 白名单扩为 `(2.7.0, 2.6.0, 2.5.0, 2.3.3)`：#627 等冻结在 2.6.0 的旧会话继续按原指纹运行，不会被升级或拒绝。
- 回归中 `test_v211_director_contract` / `test_director_reference_repair` / `test_r125_conversation` 的「新会话版本」断言更新为 2.7.0；旧版本用例保持不变。

---

## 8. 当前 #627 的安全恢复方式（本轮未执行）

#627 保持 `IN_PROGRESS`，回答、消息、5 条已落库评价、历史记忆与账本均未改动；**本轮没有点击恢复按钮，没有发起任何模型调用**。

服务端现在把 #627 识别为：
- `status=FAILED`，`error=ADAPTIVE_TOPIC_MISMATCH`；
- `recovery_stage=NEXT_REJECTED`，`evaluation_reusable=true`（通过只读账本探测 run 1222 得到，不写业务表）。

页面显示「评价已完成，下一题生成失败；回答与评价已保存」，主按钮为**继续处理本轮**，并沿用原幂等键 `ad1438bd-…`。用户自行点击后会：复用 run 1222 的评价 artifact（**不再计费 Evaluation**）→ 用零模型保底题目生成下一题 → 一次事务写入评价与题目。**不会**把「发送新回答」当成主操作。

如果将来该目标已无合法保底候选，会保留检查点并明确报错，用户可决定是否重新生成下一题；不会静默跳过。

---

## 9. 自动化与浏览器测试结果

- R127 新测试 `apps/api/tests/test_r127_adaptive_recovery.py`：**5 passed**（Prompt 版本与冻结、自然 NEXT 放行、真偏题拒绝、保底与不重复计费、账本评价恢复、连续对话脚本）。
- 相关回归（R125 对话、R126 结束/自我介绍、项目开场、Director 契约、主题策略、R124 契约、V2.1 adaptive、R127）：**111 passed**，专用新库 `interview_agent_v21_r127_full_20261117`，`APP_ENV=test`，`DATABASE_URL=M1_TEST_DATABASE_URL`，0 模型 HTTP。
- 前端 `vue-tsc --noEmit -p tsconfig.json`：0 error。
- Python `py_compile`：0 error。
- 浏览器：1440/1024/820/680px、刷新后 pending_answer、长错误文本展开详情、断线文案，均无横向溢出、控件不遮挡；截图见上表。

---

## 10. 还需要哪一步真实模型验证

本轮严格 0 模型 HTTP，因此以下仍需用户自行验证（不在本轮授权内）：

1. 用**新会话**跑一场真实面试（默认自我介绍开场），确认 2.7.0 的追问节奏：0/1/2 次追问由回答决定，没有固定句式、没有机械覆盖。
2. 在真实 Main 输出上验证：来源绑定的自然 NEXT（不写关键词）不会再触发 `ADAPTIVE_TOPIC_MISMATCH`；真正偏题仍被拒绝且审计含 `source_grounded / conflicting_topics`。
3. 用户自行在 #627 点「继续处理本轮」：确认账本**没有新增 Evaluation 调用**，评价只出现一次，下一题为保底题，会话可继续。
4. 如观察到新的误判，用审计里的 `question_excerpt + sha256 + source_ref` 回放，而不是凭前端文案判断。

---

### 变更文件

后端：`adaptive.py`、`adaptive_prompts.py`、`context_projection.py`、`controller.py`、`core_service.py`、`topics.py`；新增测试 `tests/test_r127_adaptive_recovery.py`；测试夹具/版本断言四处。
前端：`api/interview.ts`、`pages/InterviewChatPage.vue`、`types/interview.ts`。
文档与截图：本文件、`data/r127_ui/`。
