# R126：修复真实面试流程、主动结束、Skill 覆盖和岗位 AI 解读

> 基线 `022a9a4`（R125）；展示库 `interview_agent_showcase_r123_20261117`（alembic **0030**）。
> **本轮模型 HTTP 增量 = 0**：展示库账本最新一条为用户 10:18:34Z 的 `job_matching`（run 5），
> R126 全部工作在其后完成，账本无新增行。
> 未新增 Agent、未扩 JD、未改 Evaluation 2.3.4、未改正式库、未删除真实会话、未 push。

## 一、P0-1 用户主动结束面试 + #625 结算

### 1.1 根因

结束面试此前没有业务入口：唯一的结束路径是 Controller 的预算耗尽。于是用户在
第 4 题输入"能提前结束面试吗"后，这句话被当成技术回答持久化、按 2.3.4 全维度评 1 分，
随后系统又生成了一条 RAG 追问——用户"被失败"了一道从未作答的题。

### 1.2 修复

| 改动 | 内容 |
|---|---|
| 结束接口 | `POST /api/v1/interview/sessions/{id}/end`：归属校验 + 行锁 + 幂等（已结束直接返回已提交结果）+ 事务内结算；回答评价进行中返回 409 `INTERVIEW_ANSWER_RUNNING`（不产生半份评价，不重复扣费） |
| 结算语义 | `completion_json.completion_reason=USER_ENDED`、`ended_by=USER`、`early_terminated=true`；未答题置 SKIPPED，绝不伪造为已回答 |
| 结束意图识别 | 新模块 `end_intent.py`（纯规则、零模型）：整场结束措辞才触发；"不想继续这个话题"= 换题、"不知道/这题不会"= 正常回答；命中后回答请求以 `INTERVIEW_END_INTENT` 拒绝，**不落消息、不进评价、不扣费**，前端转为确认弹窗 |
| 显式排除机制 | 迁移 0030：`interview_session_questions` / `interview_evaluations` 增加 `excluded_reason`/`excluded_at`（可空，NULL=计入）。结束接口在结算时对已存储且被识别为结束请求的回答补 `END_REQUEST` 排除——分数与模型 artifact 原样保留，只是退出所有技术统计 |
| 审计 | 每次结束写 `audit_logs(action=INTERVIEW_USER_ENDED)`，含排除明细 |
| 统计链路 | `interview_records` 过滤排除行；`deterministic_statistics` 新增 `excluded_count`/`excluded_reasons`；报告 completion 事实携带 `ended_by`，`USER_ENDED` 直接判 `early_terminated` |

### 1.3 #625 结算结果（通过业务接口，非手工改库）

```
首次 POST /end → excluded=[{question_id:546, answer_id:545, evaluation_id:545, reason:END_REQUEST}]
completion: USER_ENDED · ended_by=USER · early_terminated=true
answered/evaluated = 3（sq543/544/545 三次真实技术回答）· skipped = 1（sq547 未答追问）
第二次 POST /end → already_completed=true（幂等）
```

- sq546（"能提前结束面试吗"）：题目与评价均标 `END_REQUEST`，1/1/1/1/1 分保留在库但**不进任何平均**；
  消息原文保留在 transcript 中，未删除未改写。
- sq547（错误生成的追问）：未答，置 SKIPPED，不计有效作答。
- 报告只读统计：`evaluated=3 · excluded=1 · skipped=1 · overall=2.2667`（只来自 3 份真实评价）。
- 审计两行：首次结束（含排除明细）与幂等重入。
- 报告页（`/interview/reports?session_id=625`）顶部显示：
  "本场由你主动提前结束（未答满 20 题）：已回答的 3 题正常计分；结束请求与未回答的题目均未计入任何能力统计。"

## 二、P0-2 恢复真实面试节奏

### 2.1 自我介绍开场（合法 INTRODUCTION 类型）

- `start?opening=INTRODUCTION`（现为默认）：零模型模板题「你好，我们先简单认识一下……」，
  `question_kind=INTRODUCTION`、`source=INTRODUCTION`，创建即带 `excluded_reason=INTRODUCTION`——
  **它不是技术评分题**，任何聚合都不会计入；不需要绕过主题校验，因为背景题校验本身允许首题
  BACKGROUND（`target_category=BACKGROUND` + 首题 background_allowed）。
- 自我介绍的回答**不调用评价**：`commit_introduction` 分支直接由 Main Agent 依据真实回答做一次
  完全合法的 NEXT 决策（走同一 `validate_decision`），切入第一个技术目标。没有评分就没有假证据。
- 开场豁免从"第一题"改为"开场槽"：`planning_payload.adaptive.opening_slot` = 尚无任何已评分
  技术题。自我介绍不消耗开场槽，所以介绍之后的第一问仍可从简历项目自然切入（不必点名词）。
  其后所有 NEXT 仍必须点明主题——R125 的边界测试全部保留并适配新 payload 形状。

### 2.2 追问不是配额 + 简历外考察（Main 2.6.0，仅新会话）

- 新 Prompt 版本 `interview_main 2.6.0`（= 2.5.0 + PACING 契约）：追问上限是安全上限不是任务量，
  回答充分可直接换题（0 次追问合法）；回答模糊先澄清；明显不懂可停深挖；同一知识点两次"不知道"
  停止换措辞纠缠；**明确允许简历外考察**（语言基础、OS/网络、数据库/Redis、消息队列、RAG/Agent/LLM
  机制、算法与场景题），但必须仍是冻结目标内的合法 NEXT；每轮一个主问题。
- Evaluation 2.3.4 未改动（`git diff` 为空）；旧会话继续使用各自冻结的 Prompt。

### 2.3 简历外考察的词表基础

`TOPICS` 新增 6 个可校验主题（frontend/testing/data_engineering/recommendation/network_os/algorithm），
`TOPIC_TERMS` 同步发布（R124 契约测试保证"发布词 = 校验器真正接受的词"）；词表刻意用具体短语，
避免"网络/测试/排序"这类宽泛误命中。

## 三、P0-3 岗位 AI 解读：这次的真实错误是输出截断

### 3.1 事实（run 5，2026-11-17T10:18:34Z）

```
attempt#1 FAILED error=model_output_truncated（finish_reason=length）
step: input=54064 tok, output=6000 tok（恰好 = job_matching_max_output_tokens 上限）, 22.5s
状态 READY · 基础排序 20 条完整可用（天翼云 Java 后端第一）
```

不是上一次的 `MATCH_INPUT_BUDGET`：输入 127KB 在 200KB 预算内。

### 3.2 根因：输出契约要求模型复写服务器拥有的文本

用 run 5 的真实冻结快照实测：最小合规 v1 输出（20 岗 × 3 关联，复制 evidence key 与 explanation 原文）
约 **34KB ≈ 1.7 万 token**，6000 token 上限物理不可能——必截断。且 R125 投影丢掉了关联引用所需
的 `fact_ref` 之外的支撑标识，模型连"指向一条已证实支持关系"都做不到。

### 3.3 修复（契约 v2：模型只选择，不复制）

- 投影 2.1.0：每条支撑带确定性 `support_ref = support:<digest12>`（由冻结矩阵 row+fact 推导），
  **从输入中删除逐条复制的 64 位 evidence key**（输入 150KB → 127KB）；每条要求行仍全保留。
- `MatchArtifactV2`：关联从"复制 id+fact+requirement+evidence+explanation"变成"只填 `support_ref`"。
  resolver 用同一算法在冻结矩阵上重算 ref，模型只能指向真实存在的关联；`matched_strengths`
  仍只允许 SUPPORTED；gaps/unknowns 仍必须完整回显（不可隐藏）。
- Prompt 1.1.0 记录新契约；1.0.0/1.0.1/1.0.2 历史版本与 v1 resolver 原样保留，老 run 不受影响。
- 实测（run 5 真实快照）：v2 最小合规输出 **7.7KB ≈ 2600 token**，6000 上限内有 2 倍以上余量；
  20 个岗位全部可解析，explanation/evidence 由服务器回填，内容零丢失。
- 错误状态：`model_output_truncated`/`model_refusal` 前端文案已映射为"基础排序完整可用 + 未自动重试 + 无重复扣费"。

> 说明：这仍是零模型环境下的契约修复 + 真实快照回放验证，**不是 Live 证明**。
> 若要真实模型确认，预算约 1 次 `job_matching` 调用（输入 5.4 万 + 输出 ≤6000 token），等授权。

## 四、P1-1 七个新专业 Skill

`python_backend / rag_retrieval / recommendation_ml / frontend_web / testing_qa / data_engineering / general_cs`
各含完整 `manifest.json + skill.json + SKILL.md`（1.0.0），注册表全部通过冻结校验（现有 3 个 + 新 7 个 = 10）。
每个 Skill 有真实考察范围、适用岗位、优先主题、项目追问策略与五维评价锚点，非占位文案。
岗位方向→Skill 推荐沿用既有规则自动纳入新 Skill（实测：AI Agent 岗推荐 ai_agent_application，
frontend_web/rag_retrieval 为次选；测试岗推荐 testing_qa，已用自动化测试固定）。

已知边界：`ReAct` 会被 frontend_web 的 `React` 词表命中（次选 9 分，不影响首选）；
已记录为后续改进，不在本轮强改匹配规则。

## 五、P1-2 面试页信息清理 + 结束入口

- 进行中（`interviewFinished=false`）：题目卡片不再显示"查看候选人依据"、维度、难度、深挖标记、
  参考答案与逐题评分（R125 已隐藏评分；本轮把"查看候选人依据"也补进了同一门后）。
  顶部"当前题型/考察主题/自适应考察范围"仍只在结束后出现。
- 结束按钮：会话进行中始终可用 → 确认弹窗「现在结束并生成已完成部分的面试报告？」→ 调用 /end。
- 自然语言结束：SSE 错误码 `INTERVIEW_END_INTENT`（`retryable=false`）→ 前端清空草稿并直接弹出同一确认框。
- 报告页新增完成原因说明与排除轮次说明（见 1.3）。

## 六、P0-4 报告与成长记录验收（#625）

| 检查项 | 结果 |
|---|---|
| 报告显示"用户主动提前结束" | ✅ 页面横幅（ended-note），由 completion 事实驱动 |
| 统计排除结束请求 | ✅ evaluated=3，excluded=1（END_REQUEST），原始评价保留 |
| 未答题不判能力不足 | ✅ sq547=SKIPPED，不在任何评价/平均中 |
| 真实五维评价 | ✅ 3 份 2.3.4 评价（1.6/5、3/5、2.4/5 → 综合 2.2667） |
| Coach 报告 | ⏸ 未生成：需要 1 次真实模型调用，页面上"生成面试报告"按钮等待用户点击授权 |
| 记忆提案 | ✅ 0 条（未生成报告前不会创建；生成后为 PENDING 待审核） |
| 成长记录观察 | ✅ 0 条（随报告生成才创建，且只来自真实评价） |
| 重复打开不重复生成 | ✅ 复用 `generation_key`（session+评价指纹+prompt），重复加载只读；报告未生成时无任何写入 |

## 七、自动化验证（零模型）

新增 3 个测试文件共 17 例（`test_r126_end_and_intro.py` 6、`test_r126_matching_v2.py` 6、
`test_r126_skills.py` 4 + 契约补充），覆盖：结束意图检测窄边界、自我介绍开场→项目切入、
用户结束幂等结算、#625 形态的排除与保留、评价中不重复扣费、回答进行中结束冲突、
support_ref 确定性、v2 解析与三种非法选择拒绝、20 岗最小输出 < 12KB、旧契约仍可解析、
新 Skill 冻结与主题合法性、方向→Skill 路由。

**全量回归：1283 passed / 1 skipped**（全新测试库 `interview_agent_v21_wb_r126_test_20261117`，alembic 0030）。
`vue-tsc` 与 `vite build` 通过。

浏览器截图（`data/r126_ui/`）：
1. `01_report_session_625.png` — #625 报告页：提前结束说明 + 排除说明 + 2.2667 统计；
2. `02_interview_transcript_625.png` — 结束后完整对话（此时才显示点评与依据）；
3. `03_interview_end_button.png` — 进行中会话：自我介绍第一题 + "结束面试"按钮，无评分/依据泄漏。

## 八、剩余问题

1. **Coach 报告未生成**（预算 0）：#625 打开报告页即有全部统计，点"生成面试报告"需 1 次
   `interview_coach` 调用（约 2 万 token 预算），由用户亲自点击即视为授权。
2. **岗位解读的 Live 验证未做**：v2 契约已用 run 5 真实冻结快照回放验证；如需真实模型确认，
   1 次 `job_matching` 调用（输入 5.4 万 + 输出 ≤6000 token）。
3. `ReAct`→`React` 的匹配误命中（frontend_web 次选），待后续细化排除规则。
4. 迁移测试（test_00…test_11）在**非全新**测试库上会因 0027 的数据保护护栏失败
   （"154 个私有 JD 快照"拒绝降级）——这是设计行为；CI 请始终用全新 `interview_agent_v21_*` 库。
5. 主导航仍保留 R125 的 8 项精简结构；本轮未再改动。

**本轮到此停止开发，等待用户亲自查看 #625 报告与成长记录。**
