# R125 恢复真实技术面试体验，修复岗位推荐失败

> 基线 `edaea35`；展示库 `interview_agent_showcase_r123_20261117`（alembic 0029）。
> **本轮模型 HTTP 增量 = 0。** 账本现有 14 行，最后两行是 15:52:50 `answer_evaluation` / 15:52:54 `interview_main`，
> 来自你重新体验 R124 时的自测；本轮没有产生任何 `model_admissions` 行（所有验证走 Replay 与测试库）。
> 未新增 Agent、未扩展 JD、未改动 Evaluation 2.3.4、未改写历史会话与历史评价。

---

## 一、P0-A：岗位推荐"本次推荐失败"

### 1.1 现场（零模型排查，先定位再动手）

| 项 | 事实 |
|---|---|
| 失败运行 | `job_match_runs#4`，用户 `#21`，画像版本 `#20` |
| 状态 | `status=FAILED`，`error=MATCH_INPUT_BUDGET`，`attempt_version=1` |
| 尝试 | `job_match_attempts` 1 行：`status=FAILED`，`error=MATCH_INPUT_BUDGET`，`agent_run_id=1197` |
| 模型调用 | **0 次**。`ExperienceAgentRun#1197` 在 `JobMatchingAgent.call` 的字节预算检查处就抛错，没有进入 provider |
| 候选 | `job_match_candidates` 20 行，`pre_rank` 有值、`final_rank`/`agent_rank` 全空，`job_match_recommendations` 0 行 |
| 扣费 | 无。预算检查在调用之前，账本没有这次运行的记录 |

**根因（按证据分层）：**

1. **直接原因 = 模型输入超预算，不是模型失败。**
   `model_context` 的快照 355,788 B，投影后 346,433 B；加 `MatchArtifact` JSON Schema（2,431 B）与 Prompt，信封远超
   `job_matching_max_input_bytes = 200,000`。所以模型一次都没被调用。
2. **体量来自服务器自己算出来的重复数据。** 每个候选 18,690 B 里 `evidence_matrix` 占 15,977 B（85%），
   其中 `supports` 又占 14,008 B：同一份简历证据（64 位 sha256 键）在 20 个岗位里各抄一遍。
   R124 追加 `job_terms/topic_overlap` 后，这份重复刚好把信封顶过预算线。
3. **失败被当成"整次运行失败"。** `generate()` 的异常分支把 `run.status` 直接置 `FAILED`，
   于是 20 条已经落库的确定性排序在页面上被当成"没有结果"，
   前端又把 `MATCH_INPUT_BUDGET`（`errorLabels` 里没有）降级成"操作未完成，请刷新后安全重试"。

**能确定 / 不能确定：**

- 确定：没有模型调用、没有扣费、没有写坏数据；`run#3`（`READY/NOT_GENERATED`）说明规则排序一直是算出来的。
- 不能确定：模型拿到完整矩阵时会给什么排序（本轮 0 预算，不去猜）。

### 1.2 整改：确定性排序与 AI 解读解耦

**（1）模型输入改成有界投影（`job_matching/context.py`，`PROJECTION_VERSION = 2.0.0`）**

- 每个要求行**全部保留**（`id / requirement_ref / statement / importance / status / message / matched_topics`）；
- 每个要求行最多带 **2 条**最强关联（`SUPPORTED` 优先），Prompt 本身也只允许"每个要求选一条"；
- 简历事实、证据原文、岗位要求原文、`fit_band_ceiling`、`required_counts` 全部保留；
- 只去掉**服务器已算过的派生副本**（`ranking_components`、`job_terms`、`topic_overlap`、指纹），
  它们的输入本来就在同一份 payload 里。

实测同一份快照：**346,433 B → 150,748 B**，信封 **155,316 B ≤ 200,000 B**。
Prompt 升到 `1.0.2`（新增一句"supports 是有界抽样，缺席不等于 NO_EVIDENCE"），`1.0.0/1.0.1` 仍可解析，历史运行不受影响。

**（2）状态语义：规则结果与 AI 解读分开（`job_matching/service.py`）**

不改表结构，用已冻结的列派生一个 `result_state`：

| `result_state` | 含义 | 依据 |
|---|---|---|
| `RULE_READY` | 规则排序已就绪，还没请求过 AI | `status=READY`、`attempt_version=0` |
| `AI_RUNNING` | 正在生成解读 | `status=RUNNING` |
| `AI_COMPLETED` | 解读完成 | `status=COMPLETED` |
| `AI_FAILED_RULE_READY` | 解读失败，但规则排序可用 | `status=READY` + `error` + 有候选 |
| `NO_RESULT` | 连候选都没有 | `status=FAILED` 且无候选 |

关键行为变化：**AI 失败不再把整次运行置为 FAILED**，只要还有可用候选就保持 `READY` 并把错误码留在
`run.error` 与 `attempts[].error`（"谁失败了"记在尝试上，"结果还在不在"记在运行上）。
`attempts` 仍然只记 `FAILED/SUCCEEDED`，重复读取不会新增尝试，也不会重复扣费。

**（3）页面不再用一句通用错误盖掉结果（`JobMatchingPage.vue`、`i18n/zh-CN.ts`）**

- 顶部徽标改读 `result_state`（"AI 解读未完成 · 基础排序可用"）；
- 失败提示写明"顺序来自规则排序（硬性条件 → 必需技术要求有无真实依据 → 加分项），和完整解读用的是同一份材料"；
- 为 `MATCH_INPUT_BUDGET / MATCH_COVERAGE_INSUFFICIENT / MATCH_RUNNING / MATCH_PROMPT_VERSION_UNAVAILABLE` 等
  补了中文文案，不再落到"操作未完成"；
- 重试按钮改成"补充 AI 解读（会调用一次模型）"，明确它会产生费用；规则结果本身不需要点任何按钮。

**（4）用户真实记录的现状（只读，不改写）**

```
GET /api/v1/job-matches/4
status=FAILED | result_state=AI_FAILED_RULE_READY | rule_ready=True | error=MATCH_INPUT_BUDGET
candidates=20 | recommendation=None
attempts=[('FAILED','MATCH_INPUT_BUDGET')]

GET /api/v1/job-matches?limit=5
(4, 'AI_FAILED_RULE_READY', 'MATCH_INPUT_BUDGET')
(3, 'RULE_READY', None)
(2, 'AI_COMPLETED', None)
(1, 'AI_COMPLETED', None)
```

历史行原样保留（`status` 仍是 `FAILED`），页面按派生状态解释它。用户下次点"补充 AI 解读"会用新的有界输入重新生成。

---

## 二、P0-B：把"按技术目标机械提问"改回"像人在面试"

### 2.1 旧版 vs 现在的差异（对照历史 Prompt 与当前链路）

| 维度 | `interview_interviewer_system_v1.md`（原版） | 2.3.3（R124 前在用） |
|---|---|---|
| 开场 | "从自我介绍或简历中的第一个项目经历开始提问" | 没有开场概念，首题也必须 `NEXT` 且**点名技术主题** |
| 追问 | "先简要评价（1-2 句），再提出下一个追问" | 从 evaluation 的缺口里选一个机制直接问 |
| 口吻 | 专业但友好，每轮 1-2 个问题 | 无口吻约束，只有一句"one short conversational question" |
| 结构 | 允许自然承接 | 每条决策都要求"目标 + 来源 + 证据引用"，输出像工单 |

根因不是措辞，是**契约形状**：旧链路里"开场"这件事根本不存在——第一题被当成一次 `NEXT` 主题切换，
而 `NEXT` 的校验要求问题文本里必须出现目标主题的字面词。所以模型只能朗读岗位要求或直接点名技术，
一旦从项目切入（"聊聊你做的那个客服知识库"）就会被 `ADAPTIVE_TOPIC_MISMATCH` 拒掉。

### 2.2 修改（只影响新会话）

**（1）新 Prompt 版本 `interview_main 2.5.0`**（`adaptive_prompts.py`）

在 `2.3.3` 的合法性与证据规则之上，追加 `CONVERSATION_CONTRACT`：

- **开场**：第一题从一份真实的简历项目/实习/工作项或自我介绍切入；有项目事实的 `CANDIDATE_PROFILE`
  候选优先；**不朗读岗位要求**，不宣布计划，不把"介绍项目 + 技术方案 + 难点 + 解决过程"塞进同一轮；
- **倾听**：先回应候选人实际说了什么；不清楚本人负责范围就先问范围和整体流程；已经说得够好就换题，
  不为了凑轮次去挖缺口；**把 evaluation 的缺口当自己的地图，不当下一句的措辞**；
- **节奏**：一次一个主问题；同一个知识点候选人两次说"不知道/没做过"就停止换措辞，换目标或收尾；
- **口吻**：口语、短、专业；不要习惯性用"请结合…/请说清楚…/说明具体机制/谈谈你的理解"；
  严格风格只增加技术挑战，不等于咄咄逼人或试卷腔。

Evaluation 仍是 `2.3.4`，**一个字没改**（`git diff` 为空）。

**（2）项目开场与主题校验的边界（`adaptive.py`、`topics.py`）**

保留 `validate_decision` 的全部来源/证据/难度校验，只把"必须点名主题"这一条**限定在真正的主题切换上**：

```
is_project_opening = 本场第一题（payload 无 current_question）
                  且 strategy == NEXT
                  且 题源是 CANDIDATE_PROFILE 的 projects / work_experience / internship_experience 事实
                  且 问题文本确实在说"你简历里的/你做过的/这段经历"
```

四个条件缺一不可，且**没有绕过任何来源验证**：候选仍来自冻结蓝图，`resolve_selection` 仍从该事实
绑定 `target_knowledge_id`，`validate_question` 仍校验 `candidate_fact_ids / candidate_evidence_ids`。
学历事实、岗位要求来源、第二题之后的 `NEXT`、`FOLLOW_UP` 全部照旧拒绝（见测试）。

**（3）面试页：进行中不批改作业（`InterviewChatPage.vue`）**

- 逐题评分卡、五维分数、缺失点、参考答案、"表达提示"、目标标签，全部改为**会话结束后**才出现；
- 进行中每轮只显示一句"面试官已记下这段回答，继续下一题。"；
- 后台 Evaluation 照常运行并驱动下一轮，只是不把分数推给候选人；
- 结束时（含历史场次回看）恢复完整点评与最终报告。

### 2.3 三段连续对话（真实会话、零模型 HTTP）

模型文本是脚本化的，但**决策、校验、落库全是产品自己的链路**：每一轮都由真实的 payload
（历史、上一轮回答、开放缺口、可用策略）决定，并经过 `validate_decision` → `SessionController`。
完整记录见 `data/r125_transcripts.md`，这里摘录开场与关键轮次。

**① AI Agent / RAG：回答很浅，先澄清再深入**

```
面试官（开场）：先聊聊你简历里那个智能客服知识库吧，你在这个项目里主要负责哪一部分？
候选人：我就是做了一个 RAG，然后可以信息检索。
面试官（追问）：那你们整体是怎么跑起来的？从用户提问到给出回答，中间经过哪几步？
候选人：我主要负责检索这块，用户问问题就去向量库里找相关的内容，然后交给模型回答。
面试官（追问）：检索回来的内容是直接给模型的吗？大概给几条，有没有做过筛选？
```

开场是 `NEXT / rag_retrieval / CANDIDATE_PROFILE`——问题文本里没有 "RAG/检索" 字样，靠项目开场例外通过；
`evaluation` 给出的缺口（"候选人本人负责的模块与整体链路"）**没有**被直接念成问题。

**② Java 后端：项目讲得充分，深入后合理换题**

```
面试官（开场）：你简历里那个订单秒杀系统，聊聊你负责的那部分吧，库存这块是怎么扛住并发的？
候选人：库存我用了 Redis 的 Lua 脚本做原子扣减，前面还有一层预校验……
面试官（新题）：你刚提到用 Kafka 削峰，那重复消费这块你们是怎么处理的？
...
面试官（新题）：换个话题。你们 MySQL 的慢查询，一般怎么定位到具体是哪条 SQL？
```

第一题答得充分 → 该目标直接被标为 `DEMONSTRATED_IN_SESSION`，**下一轮是 `NEXT` 换题**，没有为凑追问而追问；
后续在不熟的 Kafka 上连追两轮再换。

**③ 连续"不知道"：不纠缠**

```
面试官（开场）：简历里这个订单秒杀项目，缓存这块你实际做过吗？大概是怎么用的？
候选人：这块我参与得不多，具体细节记不清了。
面试官（追问）：没关系。那这个项目里你主要参与了哪一块，能说个大概就行？
候选人：不知道。
面试官（追问）：好，那我们换成看问题的角度：如果让你设计，你会先考虑什么？
候选人：不太清楚，没印象了。
面试官（新题）：那聊个别的。MySQL 的事务隔离级别，你平时用得多吗？
```

同一个知识点最多两次追问（`max_follow_ups=2`），之后必须换目标；连续 3 题同一主题还会被
`coverage().blocked_topics` 从决策空间里剔除（`MAX_CONSECUTIVE=3`）。

---

## 三、P1：岗位库分类与导航

### 3.1 分类从"关键词命中"改为"主要方向 + 辅助标签"

旧实现是 `title/raw_text LIKE`：岗位职责里提一句 Agent 就算 AI 岗，`JavaScript` 会命中 `Java`。

新实现 `public_career/classification.py`：

- **岗位名称权重 8**（括号内的限定语减半），**核心职责权重 2**，**任职要求/加分项权重 1**；
- 加分项**永远不能决定主要方向**，只能作为辅助标签；
- `JavaScript / Node.js` 在匹配 `Java` 前先剔除；`Agentic` 不算 `Agent`；
- 标题里出现"测试/QA/质量保障"时测试方向加权，`AI 测试开发` 归测试而不是 AI；
- 多方向岗位返回 `directions` 列表（主要方向排第一），筛选按 `directions` 命中，页面同时显示"主要方向 · 兼 xxx"。

50 份 JD 的实测分布（`GET /public-career/job-search`）：

| 主要方向 | 岗位数 |
|---|---|
| AI / Agent 应用 | 27 |
| 测试开发 | 7 |
| 后端开发 | 5 |
| 嵌入式 / 系统 | 3 |
| Java 后端 | 2 |
| Python | 2 |
| 前端 / 移动 / 运维 / 安全 | 各 1 |
| 数据 | 0（本批 50 份里确实没有纯数据岗，不再靠"数据"二字硬凑） |

易混样本核对：

| 岗位 | 主要方向 | 说明 |
|---|---|---|
| `AI 测试工程师（web平台）` | 测试开发 | 标题锚点优先，不因"AI"变 AI 岗 |
| `AI 智能化测试开发岗位` | 测试开发 | 同上 |
| `JAVA后端开发工程师` | 后端开发（兼 Java） | 两个方向都命中，Java 筛选仍能找到 |
| `后端开发工程师（AI向/数据平台）` | 后端开发（兼 AI） | 括号限定语降权 |
| `安全研发工程师` | 安全 | 标题决定，不被职责里的内核/C++ 词拉走 |

### 3.2 主导航

顶级导航只留主流程：首页 / 我的简历 / 我的画像 / 岗位推荐 / 岗位库 / 模拟面试 / 面试报告 / 成长记录。

- **自定义岗位**：功能、路由、数据都保留，入口改到岗位库（列表下方"这里没有你想投的岗位？粘贴一份 JD"
  以及空结果页的引导）；
- **专项训练**：入口保留在已完成的面试报告与岗位推荐卡片上，不再占顶级导航。

---

## 四、验证

### 4.1 自动测试（零模型 HTTP）

| 范围 | 结果 |
|---|---|
| 新增 `test_r125_matching_decoupling.py` | 11 passed（预算内投影、证据行/结论全保留、最强关联优先、只删派生副本、五种状态、失败后仍可按原顺序展示、重试不重复扣费） |
| 新增 `test_r125_conversation.py` | 4 passed（三段 ≥5 轮连续对话、开场不朗读 JD、一次一问、浅答先澄清、充分回答直接换题、连续不知道不纠缠、新会话冻结 2.5.0 而旧会话保留自身版本） |
| 新增 `test_r125_project_opening.py` | 9 passed（只有首题、只有真实项目事实、只有项目类措辞才放行；其余全部拒绝） |
| 新增 `test_r125_classification.py` | 8 passed（JS≠Java、加分项不定类、测试≠AI、标题优先、多方向单主方向、未知不猜） |
| 新增 `test_r125_chat_template_contract.py` | 4 passed（进行中不渲染分数/参考答案/目标标签，结束条件取自会话状态） |
| 全量回归 | **1262 passed, 1 skipped**（测试库 `interview_agent_v21_wb_r125_test_20261117`） |
| 前端 | `vue-tsc --noEmit` 通过；`vite build` 通过 |

### 4.2 明确没做到的验证

- **浏览器 UI 验收没跑完。** 本轮机器进入套接字缓冲区耗尽状态（`OSError: [WinError 10055]`），
  API 与 Vite 在启动后十几秒内被系统杀掉，Chrome 连不上 `127.0.0.1:5173`，所以没有截图证据。
  已做的替代：接口层实证（见 1.2(4)、3.1）+ 模板契约测试 + 类型检查/构建。
  待环境恢复后按下面清单人工过一遍即可：
  1. `/jobs/matches/4` 顶部显示"AI 解读未完成 · 基础排序可用"，下面 20 张卡片有序，可点"开始模拟面试"；
  2. 任意进行中的面试，右上不出现 `x/5` 分、无"展开参考答案"，每轮只有一句"已记下这段回答"；
  3. `/career` 按方向筛选，结果里每个岗位都显示主要方向；
  4. 左侧导航无"自定义岗位""专项训练"。
- **真实模型验收未做**（预算 0）。若要用真模型确认新 Prompt 的口语化与项目开场，最低预算建议：
  1 场 6 题自适应面试 ≈ 6 次 `interview_main` + 6 次 `answer_evaluation`，约 **12 次调用 / 60K token**；
  如需对比 strict 风格再加 1 场。

---

## 五、剩余问题

1. `MATCH_INPUT_BUDGET` 现在不会再挡住规则结果，但 20 个候选仍是"只给每条要求最强的 2 条关联"。
   如果以后岗位数放大到 50+，需要按分页/分批调用模型，而不是继续加大预算。
2. 岗位库"数据"方向为 0：这是分类结果，不是丢数据；要找数据岗需要新增 JD（本轮按要求不采集）。
3. `job_family` 字段仍为空，方向分类是实时计算的（每次搜索重算 50 份 JD，成本可接受）；
   若将来要按方向做 SQL 分页，需要落库一个物化字段并接受"分类规则升级要重算"。
4. 项目开场的例外依赖"问题文本提到项目/简历/做过"，措辞极端的自然开场仍可能被拒；
   被拒时页面会给"改用岗位要求开场"，不会卡死。
5. 面试页把评分藏到结束，会让习惯逐题看分的用户不适应；复盘入口在报告页和结束后的会话里都有。
