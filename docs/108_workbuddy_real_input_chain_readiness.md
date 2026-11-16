# 第 29 轮：真实 JD × 真实简历全链路准入预检（真实模型 HTTP = 0）

日期：2026-11-16　交接基线：`aac83f9`　**真实模型 HTTP = 0**

本轮只做三件事：**核对材料准入**、**在现有沙箱里做零 HTTP 预检**、**按真实调用链重算 3 轮面试的 HTTP 与 Token**。
不执行任何真实模型调用；不改 Prompt / Schema / 评分 / 迁移；不新建数据库；不启用 Coach Harness；不删除、不归档、不 push/reset/clean。

---

## 0. 基线核查

| 项 | 实测 |
|---|---|
| `git rev-parse HEAD` | `aac83f9`（`docs(memory): record round 28…`） |
| 已跟踪改动 | 0；工作区仅 `.workbuddy/` 与 `data/sandbox_test_pkg_20261114/` 未跟踪文件 |
| 正式库 `interview_agent` | 迁移 `0025`；5 users / **38 resumes**（28 COMPLETED、**5 PROCESSING 且 `raw_text` 为空**、5 FAILED）/ 28 resume_reports / 19 sessions / 41 evaluations / 5 reports |
| 预检沙箱 | `interview_agent_wb_r26_alt_20261115`，迁移 `0029`、68 表、业务表全 0 行 |
| 数据库总量 | `pg_database` **245**（242 业务 + 3 系统模板）；最大 OID 仍是 `interview_agent_wb_r27_ledger_20261115`（11-15 16:08 本地），**本轮未建库** |
| MinIO | `interview-agent` bucket 存在，**48 对象**（与第 27/28 轮一致） |
| 闸门 | `purge_execution_enabled=false`、`interview_coach_harness=false` |
| Provider | `modelscope` / `deepseek-ai/DeepSeek-V4.1-Flash-1017` / Key 前缀 `ms-`（未读取全文、未写入任何文件） |

> **口径差异（如实记录，不追溯改写历史文档）**：`docs/107` 记「库总数 243」，本轮实测 `pg_database=245`（业务库 242）。两者统计口径至少差一次计数；本轮唯一能坐实的是**没有新建任何库**（最大 OID 与库目录时间都停在 11-15 的 r27_ledger），所以差异不属于本轮产生。下一轮起统一按「业务库数 + 系统库数」两个数同时报。

---

## 1. 结论摘要

1. **环境已就绪，材料只差一类**：在正确的沙箱环境下，预检只剩 **`REAL_RESUME_MISSING`** 一条阻断项（见 §4.3）。
2. **4 份真实 JD 里有 1 份在主题层面完全无法驱动技术出题**（`JD03` 工行科技菁英，0 个主题命中），另 2 份只命中 1–2 个主题；主题命中是**必要不充分**条件，实际蓝图 target 仍未验证。
3. **真实简历 0 份**：`data/` 下无任何简历文件，正式库 38 份全是测试简历且 5 份 `PROCESSING` 为空文本。这是 Job Matching 与「真实用户链路」的硬前提。
4. **上一轮的 12 HTTP / 70,000 token 预算偏紧**：按实际调用链重算，基线 **10 HTTP / ≈63,100 token**（已含 2.3.4 提示词增量），只剩约 6.8K 余量 ≈ **一次引用修复**。建议改为 **13 HTTP / 90,000 token**（见 §5）。
5. **`docs/107` 的预算分解有一处事实错误**：它把「1 次 Skill 推荐」算进 HTTP，但 Skill 识别是规则匹配、**0 次调用**；同时漏掉了**简历结构化抽取 `resume_report` 的 1 次调用**。总数碰巧仍是 10，但分解不成立，按它做容量规划会错位。

---

## 2. 材料准入

### 2.1 已有：真实 JD / 面经原件（9 份，全部 sha256 通过）

来源：`data/manual_sources/2026-10-05/manifest.json`，`acquisition_mode=MANUAL_SOURCE_IMPORT`、`live_search_used=false`。本轮逐份重算 sha256，**9/9 与 manifest 一致**。

| ID | 公司 / 岗位 | 字符 | 命中主题（`topics.py` 规则） | 命中技能 | 主题层面可出题 |
|---|---|---|---|---|---|
| JD01 | 华为 / AI应用工程师（official） | 1,093 | rag_retrieval, mysql_consistency, llm, java, python, system_design | 3 个技能 | ✅ |
| JD02 | 中国移动嘉兴 / AI工程师（community） | 989 | llm, system_design | ai_agent_application 等 | ✅ |
| JD03 | 工商银行广东 / 科技菁英（community） | 947 | **（无）** | — | ❌ |
| JD04 | 中国移动湖北 / AI研发工程师（community） | 1,114 | data_security | bank_it | ✅ |
| EXP01–05 | 华为 / 字节 / 上海银行 / 移动研究院 / 工行 面经 | 313–2,276 | EXP04、EXP05 亦为 0 命中 | — | 仅作语料 |

- **技能侧口径**：`ai_agent_application=[rag_retrieval, agent, llm, python, system_design]`、`java_backend=[java, mysql_consistency, redis, kafka_reliability, system_design]`、`bank_it=[data_security, mysql_consistency, system_design, java, kafka_reliability]`。是否真能产出蓝图 target 还取决于 PUBLISHED JD + requirements + 检索，本轮**没有**验证这一步，不能把「主题命中」写成「已可出题」。
- `JD03` 通篇是银行科技岗的通用职责描述，没有任何 `topics.py` 认得的技术词。它作为「真实 JD」是合格的，但**无法驱动技术题**——这是内容属性，不是代码缺陷。

### 2.2 缺失：真实简历（0 份）

- `data/` 递归扫描无任何简历文件（`candidate_paths=[]`）。
- 正式库 38 份简历中：28 份 COMPLETED（名称含「测试简历」）、5 份 PROCESSING 且 `raw_text` 为空、5 份 FAILED。**没有一份可核验的真实候选人材料。**
- 因此本轮**不构造任何「看起来像真实简历」的替代物**。合成简历可以跑通链路，但它证明不了「真实输入可用」，不能拿来当 R28-REAL-JD-MVP 的准入证据。

### 2.3 准入所需材料清单（与上一轮一致，未变）

| # | 材料 | 现状 | 准入要求 |
|---|---|---|---|
| 1 | 脱敏真实简历 1–2 份 | **缺** | 保留教育/项目/技能结构，去除姓名/电话/邮箱/证件号；提供候选人授权与岗位意向 |
| 2 | 真实 JD 原件 | 4 份 | 使用授权与范围；确认岗位仍开放；`JD01–JD04` 中至少 1 份与候选人意向匹配 |
| 3 | 人工参考答案 / 金标 | `evaluation_calibration_v1.json` 仍 `DRAFT_UNREVIEWED`、`independent_gold_review_complete=false` | 人工复核签字后方可作准绳；在此之前**不得用分数带做通过判据** |
| 4 | 真实逐字回答 3–5 份 | 只有 V2/V3 合成回答 | 与真实 JD 对应，含优劣混淆；用于检验「没说的知识被算成已回答」 |
| 5 | 合法输入条件 | `MODEL_LEDGER_DATABASE` **未声明**；进程 `DATABASE_URL` 仍指正式库 | 见 §7 检查清单第 1–3 项 |

---

## 3. 逐环节输入 → 输出 → 数据来源（是否必须真实模型）

判定口径：**必须真实** = 该环节的结论只能由模型产生；**规则** = 纯代码/正则/统计；**Replay 合法** = 可以脚本化而不影响被验证的结论。

| # | 环节 | 输入 | 输出（落库） | 数据来源 | 模型调用 | HTTP |
|---|---|---|---|---|---|---|
| 1 | 来源导入 | 公开页正文 + `source_url` + sha256 | `sources` / `source_snapshots` / `job_description_versions` | 人工策展原件（`MANUAL_SOURCE_IMPORT`） | 无 | **0** |
| 2 | JD 解析 → Requirement | JD `raw_text` | `job_requirements`（逐字 evidence） | 上一步 JD version | **必须真实**（`public_research_extract`） | **1** |
| 3 | Requirement → Skill | requirements + `skill.json` | `user_job_targets` / skill 指纹 | 规则：`skill_recommendation.recommend()` 词表匹配 | 无（**不是模型**） | **0** |
| 4 | 简历结构化 | 简历文件文本 | `resume_reports.summary_json` | 上传文件 → MinIO + 解析 | **必须真实**（`resume_report`，走全局 `async_session_factory`） | **1** |
| 5 | 候选人画像 | `summary_json` + evidence catalog | `candidate_profile_versions` | 上一步 report | **必须真实**（`resume_profile`） | **1** |
| 6 | Job Matching | 画像 + JD | `job_match_runs/candidates` | — | 必须真实（`job_matching`），**MVP 不做** | 0（本轮范围外） |
| 7 | 建会话 + 冻结快照 | 画像 + JD + skill | `interview_sessions` / `interview_context_snapshots` | 上两步 | 无（Controller 纯状态机） | **0** |
| 8 | Main 出题（每轮） | 快照 + 历史 + 动作 | `interview_questions` / `interview_session_questions` | 快照内 JD/画像/题库 | **必须真实**（`interview_main`） | **3**（3 题） |
| 9 | Evaluation（每轮） | 题 + rubric + 回答 | `interview_evaluations` + `performance_observations` | 同上 | **必须真实**（`answer_evaluation`，冻结 2.3.4） | **3** |
| 10 | Coach 报告 | 全部评价 + 统计 | `final_interview_reports` | 上一步 | **必须真实**（`interview_coach`，Harness OFF 时 1 次） | **1** |
| 11 | CapabilityMemory | 评价 + 引用 + issues | `capability_memories` | 规则：`state=NEEDS_CLARIFICATION` 且 issues 非空 → disputed | 无 | **0** |
| 12 | Training 工作台 | 已落库 session/evaluation | 派生视图（无独立表） | 规则 | 无 | **0** |

**结论**：一条「1 JD → 1 简历 → 3 题 → 评价 → 报告」的闭环，**必须真实的模型调用只有 6 个环节、10 次**；其中 Skill 识别、记忆、训练都可完全离线复算。

### 3.1 合法数据映射（哪份材料能进哪个字段，以及不能怎么用）

| 材料 | 合法落点 | 明确禁止 |
|---|---|---|
| 公开 JD 原文（9 份原件） | `job_description_versions.raw_text`（`PUBLISHED`）→ `job_requirements.statement`（逐字 evidence） | 把原文没有的要求补写进 `statement`；把 `READY` 当成「岗位仍开放」 |
| 面经原件（5 份） | `interview_experiences.content_text`、`interview_questions.question`（`APPROVED`） | 当成候选人的真实经历或真实回答 |
| 脱敏真实简历 | `resumes.raw_text` → `resume_reports.summary_json` → `candidate_profile_versions.snapshot_json` | 把模型推断的「掌握程度」写成 `fact`；保留姓名/电话/邮箱/证件号 |
| 真实逐字回答 | `interview_answers.content`（原样） | 先改写再评分（会直接污染 `covered_points` 判定） |
| 人工参考答案 / 金标 | 只在离线文件里，作事后对照 | 进入 `blind_context`、写入 Prompt、写进数据库 |
| 合成 V2 / V3 样本 | 只作评测输入与回归夹具 | 标记为「真实样本」、计入真实样本量 |


---

## 4. 零 HTTP 预检

脚本：`apps/api/scripts/r29_input_readiness_precheck.py`（不发送任何模型请求，只做配置断言 + 只读 SQL + MinIO 列举 + 本地文件校验）。

### 4.1 检查项

| 维度 | 检查内容 | 通过条件 |
|---|---|---|
| Provider | `llm_provider` / 模型名 / Key 是否存在（只打前缀） | `modelscope` + 指定模型 + 存在 |
| 闸门 | `purge_execution_enabled` / `interview_coach_harness` | 均 false |
| 进程账本 | 全局 `async_session_factory` 的库名、`expected_ledger_database()` | 均等于沙箱 |
| 沙箱 Schema | `alembic_version`、`information_schema` 表数、13 张关键表行数 | `0029` / 68 / 全 0 |
| 文件存储 | bucket 存在 + 对象数 | 存在（48） |
| 材料 | 9 份原件 sha256、主题命中、真实简历计数 | 9/9 一致；简历 ≥1 |

### 4.2 默认环境（`.env`，`DATABASE_URL` 指正式库）

```
ready: false
blocking:
  - LEDGER_TARGET_MISMATCH:None
  - PROCESS_DATABASE_NOT_SANDBOX:interview_agent
  - EXPECTED_LEDGER_NOT_SANDBOX:interview_agent
  - REAL_RESUME_MISSING
```

这条结果本身就是一条**真实风险证据**：默认环境下进程的会话工厂指向正式库 `interview_agent`，而 `MODEL_LEDGER_DATABASE` 为空。

### 4.3 沙箱环境（`DATABASE_URL` 与 `MODEL_LEDGER_DATABASE` 同时指向沙箱）

```
ready: false
blocking: [REAL_RESUME_MISSING]
sandbox: interview_agent_wb_r26_alt_20261115  (0029 / 68 表 / 全 0 行)
runnable_jd: JD01, JD02, JD04
```

**除真实简历外，全部就绪。**

### 4.4 离线回归（0 HTTP）

`test_r26_model_scope_and_ledger.py` + `test_r26_quote_subset_offline.py` + `test_r28_v3_dataset_contract.py` → **19 passed / 0 failed / 0 skipped**（复用 `interview_agent_wb_r26_test_20261115`，未新建库）。

---

## 5. 预算重算（按实际调用链）

### 5.1 单次实测锚点（全部为真实模型结算值）

| 调用 | Token 实测 | 来源 |
|---|---|---|
| `resume_report` ×1 | 1,711 / 1,822 / 2,259（一次截断失败 6,852 不计入基线） | `docs/97` §7、`docs/88` |
| `resume_profile` ×1 | 2,289 / 2,332 / 2,764 | 同上 |
| JD 抽取 ×1 | 1,102 / 1,345 / 1,367 | `docs/97` §7 |
| `interview_main` 首题 | 7,817 / 7,933 | `docs/88`（3 题场） |
| `interview_main` 第 2 题 | 9,691 / 9,794 | 同上 |
| `interview_main` 第 3 题 | 9,722 / 11,114（推算区间 9.8–11.1K） | 18 轮原件库同序号 |
| `answer_evaluation` ×3 | 7,353 / 7,353 / 7,532 | `docs/88`（3 题场） |
| `interview_coach` ×1 | **5,833**（3 题场）；18 题场为 68,248 | `docs/88`、18 轮原件库 |

> 注意：`answer_evaluation` 的单次成本随场次上下文增长（3 题场 ≈7.4K；18 题场 5.9–13.1K；单题场 4.5–5.0K）。**不要用 r27 的 4,673 去估 3 题场**，那会把评价成本低估约 37%。

### 5.2 基线（1 场 × 3 题）

| 步骤 | HTTP | Token |
|---|---|---|
| 简历结构化 `resume_report` | 1 | 1,700–2,300 |
| 画像 `resume_profile` | 1 | 2,300–2,800 |
| JD 解析 `public_research_extract` | 1 | 1,100–1,400 |
| 出题 ×3（`interview_main`） | 3 | 27,300–28,850 |
| 评价 ×3（`answer_evaluation`，2.3.4 含 +893/次） | 3 | 24,900–25,500 |
| Coach ×1 | 1 | 5,800 |
| **合计** | **10** | **≈63,100**（区间 63.1–66.6K） |

- 说明：第 3 题回答后 `choose_action` 直接判 `COMPLETE`，**不再请求主面试官**（`core_service.py::answer` 的 `if action != "COMPLETE"` 分支），所以是 3 次出题而不是 4 次。这一点与「完成任务还要再问一次模型」的直觉相反，已按代码核对。
- 2.3.4 提示词 3,748→7,452 字符只影响评价调用（每次 +≈893），已计入。

### 5.3 三档预算

| 档位 | HTTP | Token | 说明 |
|---|---|---|---|
| 基线（0 修复） | 10 | ≈63,100 | 最可能发生的情况 |
| **建议硬上限** | **13** | **90,000** | 基线 + ≤3 处结构化修复 |
| 全修复（每调用 1 次修复） | 20 | ≈130,000 | 理论最坏，**不允许发生**，触发即停 |

**对 `docs/107` 的修正**：原定 12 HTTP / 70,000 token。70,000 对基线只剩 ≈6.8K 余量，等于「一次引用修复就吃掉一半、两次就超」。建议改为 **13 / 90,000**；若要维持 12，则必须把 Coach 移出第一轮闭环（省 1 HTTP / 5.8K）。

### 5.4 3 场（9 题）的量级参考

| 项 | HTTP | Token |
|---|---|---|
| 输入 3 套（简历/画像/JD） | 9 | ≈15,300 |
| 出题 9 次 | 9 | ≈84,000 |
| 评价 9 次 | 9 | ≈66,000 |
| Coach 3 次 | 3 | ≈17,500 |
| **合计** | **30** | **≈183,000** |

结论：**MVP 只做 1 场 3 题**。3 场不增加结论强度（同一份简历、同一个 JD），只把成本和失败面放大 3 倍。

### 5.5 修复、失败停止与幂等边界

| 机制 | 行为 | 成本 | 证据 |
|---|---|---|---|
| 结构化修复 | 每次调用**最多 1 次**，且只能做「保事实的约束修复」（引用子集、结构字段） | +1 HTTP / +3.5–9K | `infrastructure/models/structured.py` 模块注释与 `bounded_repair_only` |
| 引用超限修复 | `answer_quotes` > 3 时触发 `EVALUATION_QUOTE_SUBSET_V1`，只截取原文中真实存在的 3 条 | +1 HTTP（≈3.5K） | 第 25 轮 2/8 次、第 27 轮 0/8 次 |
| 幂等重复提交 | 同 `answer_id` + 同幂等键 → 0 次新调用、0 条新评价 | 0 | 第 25/27 轮实测「0 新账本」 |
| 评价已成功后重试下一题 | 复用已存评价，不重评（`reusable`） | 0 | `AnswerEvaluationAgent.reusable` |
| 失败停止 | `UNKNOWN` / `MODEL_USER_QUOTA` / 连续 2 次失败 → 停；不换 Provider、不自动充值 | — | 第 27 轮 0 UNKNOWN |
| Coach 输入预算 | 超过 `interview_coach_max_input_bytes` 在**发送前**失败，不静默删证据 | 0（未发送） | `interview_report/agent.py::_check_budget` |

---

## 6. 阻断风险清单

| # | 风险 | 级别 | 现状 / 证据 | 处置 |
|---|---|---|---|---|
| R1 | **真实简历缺失** | 阻断 | 预检唯一阻断项；`data/` 0 份，正式库 38 份全为测试简历 | 由你提供 1–2 份脱敏简历 + 授权 + 岗位意向 |
| R2 | 账本目标未声明 / 进程库指正式库 | 阻断 | `MODEL_LEDGER_DATABASE` 空；`async_session_factory` = `interview_agent` | 启动前同时设置 `DATABASE_URL` 与 `MODEL_LEDGER_DATABASE` 指向同一沙箱 |
| R3 | **简历抽取走全局 factory** | 高 | `infrastructure/llm/provider.py:60` 用 `async_session_factory`，**不接受注入 factory** | 只改 harness 的 factory 不够：进程 `DATABASE_URL` 必须指向沙箱，否则要么 409，要么把准入写进正式库 |
| R4 | JD03 无法驱动技术出题 | 中 | `topics.py` 对 JD03 命中 0 个主题 | 换 JD02/JD04，或接受「本场题目来自画像/面经」并在结论里写明 |
| R5 | Coach 历史失败链 | 中 | 同一报告 5 次尝试：`COACH_INPUT_BUDGET` → `COACH_COMPUTED_NUMBER` → `CAPTURE_GUARD` → `MODEL_USER_QUOTA` → `model_request_failed` | 3 题短场实测 5,833 tokens 成功过；仍按「失败 2 次即停」执行，不重试到配额耗尽 |
| R6 | `covered_points` 过度给分 | 中 | `RAG-A2` 把候选人没说的正确命题写进 `covered_points`（`docs/107` §2 缺陷 1） | MVP 验收**不得**把 `covered_points` 当「已掌握」证据；每条必须回原文逐字核对 |
| R7 | 评分金标未复核 | 中 | `evaluation_calibration_v1.json` 仍 `DRAFT_UNREVIEWED` | 分数只记录、不判通过；判据用「逐跳可核对」而非分数带 |
| R8 | 数据库计数口径不一致 | 低 | 245 vs `docs/107` 的 243；本轮未建库 | 下轮起同时报「业务库 / 系统库」两个数，不追溯改写历史文档 |

---

## 7. 启动前检查清单（真实模型调用前逐条执行）

1. `export DATABASE_URL=postgresql+asyncpg://interview_agent:interview_agent@localhost:5432/<沙箱>`
2. `export MODEL_LEDGER_DATABASE=<沙箱>`
3. `python scripts/r29_input_readiness_precheck.py --sandbox <沙箱>` → `blocking` 必须只有 `REAL_RESUME_MISSING`（提供简历后应为空）
4. 确认沙箱 `alembic_version=0029`、68 表、业务表全 0 行
5. 确认真实简历已脱敏（无姓名/电话/邮箱/证件号），并记录 sha256
6. 确认使用的 JD 为 `PUBLISHED` 版本，且 `topics.py` 至少命中 1 个技能 priority topic
7. 确认 `purge_execution_enabled=false`、`interview_coach_harness=false`
8. 确认 `question_count=3`、`interview_round` 与目标岗位一致、冻结 Prompt 版本 = `2.3.4`
9. 确认预算闸：HTTP ≤13、Token ≤90,000、时长 ≤25 min
10. 跑完立刻核对：10 条 admission 全 `SETTLED`、`admission_tokens == evaluation tokens`、0 UNKNOWN、0 条 `legacy_llm`

---

## 8. Git 与资产状态

- 基线 `aac83f9`；本轮新增 `apps/api/scripts/r29_input_readiness_precheck.py`、两份预检结果 JSON、`docs/108`，按关注点分别提交。
- **未新建数据库**（最大 OID 仍是 r27_ledger）；未向正式库、18 轮原件库、快照库写入；MinIO 仍 48 对象；三闸未改动。
- 未修改 Prompt / Schema / 五维字段 / 迁移 / 评分逻辑；未重标任何历史评价；未 push / reset / clean；未改 `AGENTS.md`、`CLAUDE.md`、`.claude/`。
- 真实模型 HTTP = **0**：本轮全部为只读 SQL、本地文件校验、配置断言与离线回归（19 passed）。
