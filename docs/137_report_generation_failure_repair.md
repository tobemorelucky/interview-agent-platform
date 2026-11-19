# R137：真实面试报告生成失败 — 定位与修复报告

日期：2026-11-19　前置 HEAD：`4943aab`（R136）　本轮提交：见文末（本地，不 Push）

## 结论：真正修复（LIVE_PASS）

报告 #19 同类输入已能正确生成报告。根因是 Coach 数字校验的**语义误判（B 类）**，不是模型编造统计。修复精确限定在"中文数字+次"的技术习语豁免，评分/百分比/阿拉伯统计/本场作用域统计校验一律保留。真实验收：一次受控安全重试即成功，报告 COMPLETED、评分未变、历史失败审计保留、无重复提案。

---

## 1. P0 真实失败证据（零 HTTP）

### 环境与关联

| 项 | 值 |
|---|---|
| Git HEAD | `4943aab`，工作区干净（仅历史 `data/` 未跟踪） |
| API 进程 | PID 328732 / 325104（`delivery.py api`）；worker 323588 / 284272；web 300964 |
| API 实际连接库 | `interview_agent_showcase_r132_20261118`（启动日志 `DB=...`，报告 #19 仅此库为 FAILED，其余克隆库为 COMPLETED，交叉印证） |
| 报告 #19 | session 641，`generation_number=1`，`generation_status=FAILED`，`is_current=false`，`attempt_version=3`，`prompt_version=1.0.3`，`producer=SUMMARY_COACH_AGENT` |
| 岗位 | 万兴科技 · AI应用开发工程师（Agent应用与平台方向） |
| 会话 | 641 = COMPLETED；3 条有效评价（id 570/571/572），综合 2.6667/5 |

### 三次失败尝试

| attempt | agent_run | 状态 | error |
|---|---|---|---|
| 1 | 1282 | FAILED | COACH_COMPUTED_NUMBER |
| 2 | 1283 | FAILED | COACH_COMPUTED_NUMBER |
| 3 | 1284 | FAILED | COACH_COMPUTED_NUMBER |

三次是用户连续点击"安全重试"产生的独立 attempt，非服务端自动重试。

### 模型调用与诊断

- 三次 Coach 模型调用**全部 HTTP 成功**：step 1285/1286/1287，`status=SUCCEEDED`，token 7104/7068/7111，`schema=CoachArtifact`，`usage_complete=true`。失败发生在返回后的 Pydantic/语义校验阶段，**不是模型失败**。
- 从 `experience_agent_runs.output_json.validation` 提取到完整结构化诊断：

| 字段 | 值 |
|---|---|
| error code | `COACH_COMPUTED_NUMBER` |
| rule | `FABRICATED_STATISTIC` |
| field_path | `strengths.2.statement` |
| matched_number | `二次` |
| unit | `次` |
| value | `2.0` |

- 报告 #19 保存的 `statistics_json`：counts = {0,1,3}（answered/evaluated/total_questions=3，follow_up/deep_dive/dynamic=1）。2.0 不在其中 → 校验拒绝。
- **触发错误的完整原句未被保存**（step output 只留调用元数据，run output 只留匹配短语）。按约束不编造原文；本次新增脱敏 `context_hint` 留痕，供未来同类失败定位。

---

## 2. P1 根因判断：B 类误判

`resolver._unsupported()` 的中文数字校验：

- `ONE_TIME_IDIOM = [至最多少再第首每]?一次(?!性)` —— 只豁免"一次"（至少一次投递、一次调用、第一次、一次性）。
- 注释明确："Every larger Chinese count must be a server-provided number" —— "二次/三次/两次"一律当统计，只要值不在服务端 counts 就拒绝。
- "二次"在 Agent/工程语境是高频**技术术语**（二次确认、二次校验、二次重试、二次开发、二次消费、三次握手），并非"本场回答了 2 次"这类统计声明。
- 失败字段是 `strengths.2.statement`（优点陈述），不是 overall_summary 等统计字段；Prompt 1.0.3 已明确禁止写统计数字，模型正常会写技术术语而非明目张胆的统计。

判定：**B（正常技术表述被统计校验误判）为主**。不是 A（无依据统计），不是 C（合法统计未识别），不是 D（结构异常）。

Prompt 1.0.3（`COACH_ACTION_PROMPT`）与 `_qualitative()/_unsupported()` 的一致性：Prompt 要求"不写任何次数/评分/百分比"，校验器却把技术术语里的"二次"也当数字异常——两者在"数字=统计"的假设上过严对齐，缺一个"技术习语"出口。

---

## 3. 修复（精确、有界、可审计）

文件：`apps/api/src/interview_api/modules/interview_report/resolver.py`

1. `ONE_TIME_IDIOM` → `CHINESE_COUNT_IDIOM = [至最多少再第首每]?[一二两三四五六七八九十]+次(?!性)`：把习语豁免从"一次"扩展到"二次/三次/两次/十次"等中文数字+次。
2. 中文数字分支的豁免条件保持严格作用域约束：`unit == "次" and CHINESE_COUNT_IDIOM.fullmatch(phrase) and not _scoped_once(...)`。即：
   - "二次确认 / 三次握手 / 二次重试 / 二次开发" → 无本场作用域 → 豁免 ✓
   - "本场回答二次 / 累计二次 / 只答了二次" → `_scoped_once` 命中（SESSION_SCOPE 或 ONLY_COUNT_VERB）→ 不豁免 → 仍按服务端 counts 严格校验 ✓
3. 评分单位（分//5/out of 5）、百分号、阿拉伯数字统计、英文 `score/count is N` 校验**完全未动**。
4. 新增脱敏 `context_hint`：只保留命中短语之后的≤4字技术名词后缀（如"确认"/"握手"），不保留前文（避免泄露"本场"等用户原文），随 diagnostics 存入 run output。

未改：Prompt（仍 1.0.3）、Evaluation 2.3.4、五维评价、历史会话/报告/审核记录、生产数字校验强度。

---

## 4. P3 零 HTTP 回归

测试库：`interview_agent_wb_r137_test_20261119`（独立，不改报告 #19 原始记录）。

| 测试 | 结果 |
|---|---|
| `test_r137_coach_number_guard.py`（新增，用报告 #19 真实 statistics 重建）：技术"二次/三次"习语通过、本场作用域统计仍拒绝、虚构评分/%/阿拉伯统计仍拒绝、服务端计数声明仍接受、脱敏留痕、resolver 集成 | 17 passed |
| `test_coach_number_guard.py`（真实历史 fixture coach_live_18，防回归） | 全过 |
| `test_autonomous_acceptance.py`（数字守卫不可削弱 + 诊断字段） | 更新断言纳入 context_hint，核心"不含本场"保留，通过 |
| 报告链路聚合（m5_report / generation_key / harness / tool_value / structured_memory / evidence_budget / report_session_identity） | **116 passed / 0 failed** |
| 含"二次确认"的 strengths 走完整 resolve_coach_artifact | 通过（对应原失败字段） |

---

## 5. P4 真实验收（1 次受控调用）

### 预算（先核算再调用）

| 项 | 值 |
|---|---|
| 配额默认 | 100 calls / 1,000,000 token（`config.py` 默认值） |
| 今日已用（真人操作库 user:21 全链路） | 11 calls / 123,733 token |
| 报告 #19 历史失败 | 3 Coach HTTP / 21,283 token（已计入） |
| 本轮新增上限 | **1 次 Coach HTTP**（用户约束），远低于剩余 89 calls/876k token |

### 做法与结果

为避免触碰真人操作的 R132 库和报告 #19 的 FAILED 历史记录，用 `pg_dump/pg_restore` 建独立验收库 `interview_agent_showcase_r137_20261119`（报告 #19 = FAILED/attempt3，session 641 = COMPLETED，3 评价，数据一致）。脚本 `scripts/r137_report_live.py` 对 session 641 调用一次 `generate(regenerate=False)`（安全重试，复用相同 generation_key）。

| 检查项 | 结果 |
|---|---|
| 报告 #19 状态 | FAILED → **COMPLETED**，`is_current=true`，`attempt_version=4`，有分析正文 |
| 模型 | **1 次 Coach HTTP**（step 1288，7,109 token，SUCCEEDED） |
| 原评分未变 | 3 条评价 id 570/571/572 与时间戳完全一致 |
| 历史失败审计 | attempt 1/2/3 仍为 FAILED（COACH_COMPUTED_NUMBER），attempt 4 SUCCEEDED |
| 会话 | 641 仍 COMPLETED |
| 记忆提案 | 首次合法生成 2 条 PENDING（id 18/19，report_id=19），对应分析里的 2 条 memory_proposals，**非重复** |
| 幂等复检 | 第二次 `generate()` `reused=true`，**0 新 HTTP / 0 新提案**（proposals 2→2，coach_steps 20→20） |
| 页面展示 | 分析正文含总结/改进方向（strengths 3 / weaknesses 4），报告 current 指针正确，提案 PENDING 待用户确认 |

证据：`data/r137_audit/r137_report_live.json`、`live_run.log`。

### 非确定性说明

原失败三次复现同一"二次"诊断，说明是**确定性校验误判**，不是偶发模型行为——修复针对语义规则本身，不依赖重试碰运气。根治机制：习语豁免 + 作用域严格校验 + 脱敏留痕；未来若出现新的误判类别，可从 `context_hint` 定位具体短语，按同样方式精确扩展，而不是放宽整体校验。

---

## 6. 预算总账（本轮）

| 项 | 值 |
|---|---|
| 本轮新增模型 HTTP | **1**（Coach 安全重试，7,109 token） |
| 零 HTTP 检查/回归 | Git/进程/库定位、116 项 pytest、幂等复检、证据核对 |
| DeepSeek Search/Fetch | 0 |
| 自动重试 | 0（仅用户触发的一次安全重试） |
| 费用 | 无法本地核实，标注未知；token 已记 |

---

## 7. 边界与未完成

1. 原句未保存，无法 100% 证明"二次"是技术术语而非本场统计；但从字段（strengths）、Prompt 约束、确定性复现三方面，B 类判断证据充分。已用"作用域约束"保证：即便真是统计声明（本场回答二次），仍严格拒绝。
2. 阿拉伯数字的技术术语（如"做2次重试"）仍会按统计校验（2 不在 counts 则拒绝）——本轮不扩大到阿拉伯数字，避免过度放宽；若后续出现此类真实失败，可用 `context_hint` 定位后同类精确处理。
3. R132 真人库的报告 #19 保持 FAILED 原样未动；修复效果在独立验收库验证。真人库需用户再次点击"安全重试"即会按新代码成功生成（同一 generation_key、attempt+1）。
4. 未触碰正式库（interview_agent）、历史评价、审核记录、Prompt 版本。

## 8. 工程交付

- 修复文件：`apps/api/src/interview_api/modules/interview_report/resolver.py`。
- 测试：`test_r137_coach_number_guard.py`（新）、`test_autonomous_acceptance.py`（断言更新）。
- 脚本：`scripts/r137_report_live.py`（一次受控真实验收）。
- 报告：本文档；证据 `data/r137_audit/`。
- 本地提交：见下，未 Push。
