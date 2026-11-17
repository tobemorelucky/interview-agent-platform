# R121：展示版真实 UI 验收与第一场完整 Live 面试

> 口径：**【事实】**= 本轮命令 / 浏览器 / 数据库实测；**【推断】**= 由事实推导；**【建议】**= 后续安排。
> Git HEAD 全程 `1d87a0b`，**未提交 / 未 push / 未 reset / 未 clean**；`docs/120` 与其产物保持未提交原样。
> 本轮真实模型消费 **8 HTTP / 42,614 token**（首场 Live）；UI 验收与 D1 定位均为 **0 模型 HTTP**。

---

## 1. 阶段一：只读前置检查

产物：`data/r63_r121_preflight/preflight.json`，结论 **PASS**。

- Git HEAD `1d87a0b`；工作区仅有 R120 留下的未提交 `docs/120`、脚本与数据产物，未清理、未覆盖。
- **两个沙箱各有 9 份 PUBLISHED，且版本 ID 集合完全相同**（1/2/3/5/7/16/17/18/19）——这不是假设，是分别查询后比对的结果；两沙箱均 alembic `0029`。
- 通过**应用自身的 `async_session_factory`** 反查：看到 8 个 R120 合成测试用户、8 个历史会话、**0 条模型账本行**，证明进程实际指向 `interview_agent_showcase_r1_20261116`，而不是 `.env` 里默认的正式库。
- `LLM_PROVIDER=modelscope`、`MODEL_LEDGER_DATABASE=showcase`；Main/Coach Harness 均 OFF，Evaluation 仍为 2.3.4。
- R120 三场 Replay（会话 6/7/8、20 条评价、6 份报告）**未重跑、未覆盖**；本轮所有 UI / Live 记录都是新增会话。

---

## 2. 阶段二：真实浏览器 UI 验收（0 模型 HTTP）

### 2.1 运行方式

真实 FastAPI（`r64_showcase_ui_runtime.py`）+ Vite dev server + 无头 Chrome 157（CDP `Page/Runtime` 域）三进程并行；API 日志写到 `data/r64_showcase_ui/api_requests.jsonl`。

- 只替换 `interview_main` / `answer_evaluation` / `interview_coach` 为显式 Replay fixture；**每个出站 HTTP 在客户端 seam 被拒绝**，因此 UI 阶段模型 HTTP 严格为 0。
- 唯一一次"修复"发生在**验收夹具**而非产品代码：初版 Coach fixture 返回空 `memory_proposals`，导致"批准合法提案"无法被真实点击；改为引用**本场真实评价 ID** 的合法 fixture 后重跑全链。产品 Prompt、Evaluation、Service 均未改。

### 2.2 完整操作结果（11/11 通过）

| # | 实际操作 | 实测结果 |
|---|---|---|
| 1 | 登录 R120 合成测试用户 | `/dashboard`，`access_token` 写入 |
| 2 | Dashboard | 业务链路文案与入口正常（658 字符） |
| 3 | 公共职业资料页 | **9 个已发布岗位**；点击"查看岗位要求"显示要求、原文节选与"查看原始来源"说明 |
| 4 | 简历 / 画像页 | 合成简历文件名与画像版本正常显示 |
| 5 | 选择 AI Agent 岗位 | 选中"沐瞳科技 · Agent开发工程师（平台）"，页面显示确定性规则推荐与 `ai_agent_application` 匹配依据 |
| 6 | 创建 FULL 自适应面试、3 题逐题作答 | 新会话创建、3 轮问答与评价均完成 |
| 7 | 题目 / 追问 / 评价 / 完成状态 | 追问与评价气泡、复盘统计、COMPLETED 均可见 |
| 8 | 报告页生成 Coach 分析 | 统计与 Coach 分析可见；**合法记忆提案出现** |
| 9 | 批准记忆提案 | "批准写入长期记忆"→"确认批准"完成，成长记录页可见 |
| 10 | WEAKNESS 训练 | 训练页出现"选择此弱项练习"，创建会话并冻结 `memory_guided_plan`，页面显示历史来源面试链接 |
| 11 | 跳转 / 加载 / 失败提示 / 刷新恢复 / 历史 | 报告列表→详情→刷新恢复均正常，未见错误横幅 |
| 12 | 标识审计 | Replay 会话显示 **"DEMO · Replay 回放模式（不是 Live 模型验收）"**；私人 JD 与已发布 JD 分列；无"Live 通过"类误标 |

**截图 15 张**：`data/r64_showcase_ui/01_login.png` ～ `13_label_audit.png`（含 3 张逐题图 `07_turn_1/2/3.png`）。
**API 对账**：186 条请求，**200×182 / 201×4，0 条 4xx/5xx**，43 个不同路径。
**业务对账**：新增 UI 会话 4 个（9/10/11/12）、评价 6 条、报告 2 份（均 `REPLAY_FIXTURE`）、记忆提案已批准；**UI 阶段 `model_admissions` 仍为 0**。

### 2.3 观察到的非阻断行为

- 第一轮验收在批准记忆前执行，WEAKNESS 按钮可见并创建了会话 10（`mode=WEAKNESS`、计划 1 条）；第二轮批准提案后该用户的状态升级，训练页只给出 FULL，于是创建的会话 12 为 FULL。这是**能力状态驱动的合法模式变化**，不是 UI 缺陷；两种路径都留下了截图与数据库记录。
- 新建会话后"开始 / 重试首题"按钮有时已被前端自动跳过（首题随创建流程就绪），不影响实际出题；已以题目气泡和 3 条评价为验收依据。

**结论：UI 主链完整通过，无需要修的产品阻断缺陷。**

---

## 3. 阶段三：首场真实模型完整 Live 面试

### 3.1 前置与边界

- 用 `r67_r121_first_live.py` 的 `guard()` 实测：`interview_main` / `answer_evaluation` 均 `modelscope / deepseek-ai/DeepSeek-V4.1-Flash-1017`、`governed=True`、无 fallback；Coach Harness OFF；账本目标为展示沙箱。
- 岗位选 **jd_version 2「沐瞳科技 · Agent开发工程师（平台）」**：12 条要求全部逐字、Evidence 完整、当前数据下 **0 条 D1 行**。
- 合成简历文件名 `SHOWCASE_R1_LIVE_SYNTHETIC_RESUME.txt`，正文首行显式写 `SYNTHETIC TEST MATERIAL`；账号 `showcase-r1-live-synthetic-*`。
- 未发生 Provider 切换、Repair 循环或额外会话；第一次运行因漏导入 `job_target.models` 在创建会话前抛错（**0 个模型调用用于该步骤**），修正后复用已结算的画像与用户续跑，没有重复购买画像输出。

### 3.2 逐跳审计

| 跳 | 结果 | 证据 |
|---|---|---|
| 简历解析 | 成功（规则解析器，0 HTTP） | resume_id 7，明确标注合成 |
| 画像抽取 | 真实模型 `resume_profile`，1 HTTP / 1,550 token，`COMPLETED` | profile version 7 |
| Main 出题 | 真实模型 `interview_main`，首题问工具调用的校验与错误回填 | question_id 29 |
| 3 轮作答 | 每题都产生 `evaluation` 事件；answer 27/28/29 | 会话 14，3 题 3 答 |
| Evaluation | 3 条全部落库；prompt **2.3.4**（未改） | 分数 4/2/3/3/4、3/1/2/1/3、4/2/2/3/3 |
| 会话完成 | `COMPLETED`，`evaluated_count=3` | completion_json |
| Coach | `SUMMARY_COACH_AGENT`、prompt `1.0.3`、`generation_status=COMPLETED` | report 9，统计来自持久化评价（`overall_average=2.6667`） |
| Memory | `agent` 状态 `PRACTICING`，由真实观察产生 | capability_memories 新增 |
| 下一场计划 | workbench 的 `memory_guided_plan` 引用本场 `evaluation:*` | 1 条 recommendation 命中 |
| 结算 | 全部 `SETTLED`，单一模型 | routes: resume_profile / interview_main / answer_evaluation / interview_coach |

### 3.3 严格核查结论

1. 三题均生成并得到完整回答 ✅
2. 每题 Evaluation 落库 ✅
3. 评价引用来自本题回答 ✅ —— 事后审计按 `answer_id` 重查，**6/6 条 `answer_quotes` 都是对应回答的连续子串**。运行中的一次 FAIL 是我的脚本用 `question_id` 映射评价（该字段在评价 artifact 中不存在）造成的假阴性，已用零 HTTP 脚本 `r69_r121_live_audit.py` 更正。
4. `covered_points` 未见捏造知识：第 1 题的"避免无限循环"是对回答中"固定最大步数和超时"的合理概括（其余点逐字命中）；第 2 题 `covered_points=[]` 且分数偏低，如实反映回答不足，**没有硬凑覆盖点**。
5. 会话真实 COMPLETED ✅；6. Coach 数字与 3 条评价一致 ✅；7. 合法能力观察 ✅；8. 下一场可读本场评价 ✅；9. 账本正常结算 ✅。

### 3.4 实际消耗

**8 HTTP / 42,614 token**（画像 1 / 1,550 + 续跑 7 / 41,064）。硬上限 13 / 90,000，**未超限**；展示沙箱账本 0 → 8 行。

产物：`data/r68_r121_live/live_run.json`、`live_audit.json`。

---

## 4. 阶段四：D1 缺陷零 HTTP 定位与最小修复

### 4.1 三条原句分类

| 行 | 原句特征 | 分类 | 结论 |
|---|---|---|---|
| 83 | `有漏洞挖掘、CTF竞赛经验，或安卓系统安全开发经验者优先` | 用"或"连接的完整优先表达 | **代码缺陷，已修** |
| 84 | `有Linux内核漏洞挖掘经验（…），或具备…经验者优先` | 同上（括号内还有局部"优先"） | **代码缺陷，已修** |
| 117 | `熟悉 Verl…框架，了解分布式训练…者优先` | 强制核心 + 末尾优先，句子内无"或"连接 | **模型存储判断，不改代码**；与既有 `熟悉Linux，有CUDA经验者优先 → REQUIRED` 契约一致 |

### 4.2 根因

`split_strength_clauses` 用 `[，,]` 无差别切分：`A，或B者优先` 被拆成 `A`（无标记→REQUIRED）+ `或B者优先`（PREFERRED）→ `is_compound_mixed=True` → `consumer_strength` 把整条 PREFERRED 升成 REQUIRED。这就是"或"连接被误判成"强制+优先"的机制。

### 4.3 最小修复

`requirement_vocabulary.py` 增加 `_OR_CONTINUATION` / `_REQUIRED_MARKER`：以"或/或者/或是"开头的逗号后段并入前一段，完整表达末尾的"优先"因此作用于整条；**带显式硬条件（必须/至少/需/要求…）的前段不并入**。未改 `supports_literal`、未改历史 Requirement、未执行 0030、未触碰 Evidence。

修复后实测：83/84 → 单条 PREFERRED、非 mixed、`effective=consumer=PREFERRED`；117 与麒麟内核原句仍为 mixed → REQUIRED。

### 4.4 回归

新增 `test_r121_d1_strength_fix.py`（含"显式硬条件不被并入"、"UNKNOWN 不升级"、"合并后 offset/原文不变"）；词表测试 **71 passed**；公共岗位/要求/证据/匹配/目标/上下文回归 **229 passed / 112 skipped / 0 failed**。

---

## 5. 资产状态

| 资产 | 状态 |
|---|---|
| Git | HEAD `1d87a0b`；本轮修改 `requirement_vocabulary.py`（D1）、新增 4 个脚本 + 1 个测试；`docs/120` 仍为未跟踪文件，**未提交、未 push** |
| 正式库 `interview_agent` | alembic 0025；users 5 / resumes 38 / sessions 19 / evals 41 / reports 5 / JD 4 / admissions 0，**未写** |
| JD 沙箱 | 0029；12 版本（9 PUBLISHED / 3 WAITING）、32 账本行 81,458 token，**本轮零写入** |
| 展示沙箱 | 0029；13 会话（8 R120 + 4 Replay UI + 1 Live）、29 评价、9 报告、8 账本行 42,614 token、15 条能力记忆 |
| 历史 Live / 封存库 | 未触碰、未重跑、未删除 |
| MinIO | 生产桶 48、`r31-jd` 0，未变 |
| Evaluation 2.3.4 / Coach Harness / L3-L4 | 未修改、未启用、未推进 |

---

## 6. 后续路线

### 6.1 扩大到 45–50 份真实 JD 的可执行批次计划

现状 9 PUBLISHED，缺口 **36–41 份**。52 份语料中只有蔚来/沐瞳/麒麟有可用官方 DOM，**再扩 3 批也无法达到 45 且保持企业多样性**。按顺序执行：

1. **补采企业**：至少新增 4–6 家企业的官方招聘详情页（每家 6–10 个研发岗），保留 DOM 与原始岗位 ID；禁止合成/社区转载凑数。
2. **固定批次大小 6 份**：复用 R120 流水线——52 份统一清单 → 零 HTTP 复核（正文/DOM/字面身份/Hash/去重/F1 切行）→ `prepare` → 2 HTTP/份 Research → 逐条"VERBATIM + 证据偏移 + 强度"审核 → ADMIN 发布 → targets/detail API 验证。
3. **预算**：每批正常 12 HTTP / ≈30K，硬上限 16 / 45K；连续 2 FAILED 整批停；证据类失败不 Repair。36 份约需 **6 批 / 72 HTTP / ≈180K**（分批独立授权，不一次性批）。
4. **质量门槛不放宽**：无小节标题岗位逐条查逐字（R35 规律）；D1 修复后复核强度投影；不把 OTHER/UNCLASSIFIED 标成研发岗。

### 6.2 另外两场 Live 的建议

- **第二场 Java 后端 Live**：用 jd 5「沐瞳 · 后端开发工程师（AI向/数据平台）」或 jd 17「麒麟 · 嵌入式」；复用本轮的 R121 驱动，独立授权 13 HTTP / 90K；`java_backend` Skill、adaptive FULL、3 题、Harness 全 OFF；重点验证 D1 修复后匹配/出题强度不再把"或…者优先"当硬条件。
- **第三场跨场次训练 Live**：用本场合成用户的真实评价产生合法 WEAKNESS（或冲突时 RECHECK）+ `MemoryGuidedPlan`，3 题以内；验收点是"历史评价引用是否精确、独立作答是否与提示后改善区分、能力状态是否被总分直接推导"。仍为独立预算，不与前两场合并。

### 6.3 未完成 / 待批

- 0030 迁移与 M1 修订落库：本轮未执行；沐瞳 Unity（jd20）与面壁/蔚来推理仍 WAITING_REVIEW。
- 面经 59 题：仍为 0 可入库 / 33 参考 / 26 需原文；未写入业务表。
- 真实简历：仍为 0 份；所有 Live 均使用明确标注的合成简历，**不能称作真实求职者验收**。
- 20 轮长场 Live：未通过，本轮不冲击。

---

## 7. 证据等级

除 §6.1 的批次规划、§6.2 的场次建议为【建议】，以及"WEAKNESS 按钮消失源于状态升级"为【推断】外，其余均为本轮实测【事实】。所有数字来自本轮浏览器日志、API 日志、数据库计数与脚本产物；Replay 结果未混入 Live 统计。
