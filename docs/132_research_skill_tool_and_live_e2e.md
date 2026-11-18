# R132：面经采集、专业 Skill、主动检索与真实 API 全链路验收

日期：2026-11-18 · 基线：本地 HEAD `4ff38d2`（R127–R130 已提交并 Push）
验收库：`interview_agent_showcase_r132_20261118`（从展示库克隆，独立库；正式库与展示库均未改写）
模型 HTTP：**20 / 20**（上限用尽）· 累计 Token：**163,469 / 200,000**
DeepSeek Web：Search **4 / 4**、Fetch **3 / 4** · 外部抓取仅限这 7 次
未 Push、未自动批准长期记忆、未批量处理 50 个岗位

---

## 0. 结论速览

| 目标 | 结果 | 证据 |
| --- | --- | --- |
| 复用已有搜索/抽取 Agent | **完成，未新建采集体系** | 只用 `WebCollectionService` + `PublicDataResearchAgent` + 既有 Search/Fetch Gateway |
| DeepSeek Search/Fetch 真实可用 | **是** | 4 次搜索、3 次抓取全部 HTTP 200；拿到真实牛客正文与快照 |
| 真实面经入库 | **1 条成功，2 条被证据契约拒绝** | 百度 Agent Harness：17 题、审核通过、精确关联 job 31 |
| 公司/岗位关联 | **1 条精确关联** | `experience 1 → company 6 百度 → job 31 Agent Harness研发工程师(J100815)` |
| Skill 匹配修复 | **完成** | 两个 frontend 错配改判 `ai_agent_application`；Java 岗位恢复 `java_backend`；50 岗位自动推荐 32→35 |
| 新增 Skill | **未新增** | Go/分布式/LLM 工程与现有 10 个主题高度重叠，证据见第 5 节 |
| 主面试官主动查面经 | **实现并离线跑通；真实模型 0 次调用** | 2 场 Harness 真实会话均选择直接出题；工具在真实冻结数据上返回了 experience 1 的 3 道题 |
| 预加载面经影响提问 | **有 Live 证据** | #640 第 3 题 `PUBLIC_QUESTION 16`，追溯到百度面经与牛客 URL |
| 3 题完整面试 + 报告 | **完成 1 场** | #640：3 题、3 次评价、报告 18、2 条待确认成长记录 |
| 预算 | **20/20 HTTP，163,469 Token** | 见第 7 节逐次明细 |

一句话：**搜索→正文→抽取→审核→关联→面试→报告这条链已经有一次真实闭环；"模型主动调工具"这条支线没有拿到 Live 证据。**

---

## 1. 已有搜索/抽取 Agent 盘点与复用

### 1.1 两条采集路线都真实存在，职责不同

| | A. SearXNG 路线 | B. DeepSeek 官方 Web 路线 |
| --- | --- | --- |
| 搜索 | `experience/search/searxng.py` | `public_career/tools/deepseek_web.py` |
| 正文 | `experience/fetchers/httpx_fetcher.py` | DeepSeek `fetch`（官方服务取正文） |
| 入口 | `build_searxng_tool_gateway()`，显式 legacy | `build_live_tool_gateway()` / `WebCollectionService` |
| 默认配置 | `experience_search_provider=searxng` | 公共岗位采集的管理端接口 |
| 审计/限额 | 无独立 Web 预算 | `WebHTTPAudit`：按日、按 DB 的 Search/Fetch 硬上限 |
| 当前定位 | **旧经验采集的显式备用**，不会在付费 Web 失败后自动回退 | **公共岗位/面经的正式 LIVE 入口** |

两条路线共用同一套 Gateway、`ToolInvocation`、`Source`/`SourceSnapshot`、`PublicDataRefreshTask`，没有两套落库逻辑。

### 1.2 逐项确认

| 问题 | 结论 |
| --- | --- |
| 1. 哪个是正式入口 | **B（DeepSeek Web）**用于公共岗位/面经；A 保留为显式 legacy，`experience_search_provider` 默认值仍是 searxng，但公共采集接口不读它 |
| 2. 哪些节点真实可跑 | 搜索、选择、抓取、快照、抽取、审核、关联全部有真实代码路径；本轮 A 路线未调用，B 路线跑通搜索/抓取/抽取/审核/关联 |
| 3. DeepSeek Search/Fetch 是否可用 | **可用**：4 次 search、3 次 fetch 均 200，返回真实牛客结果与正文 |
| 4. 两套凭据是否分离 | **是**：`DEEPSEEK_WEB_API_KEY` 只给 `DeepSeekWebClient`；ModelScope/LLM 走 `MODELScope_API_KEY` 与 `ModelRouter`。管理端 status 明确返回 `model_route_unchanged=true` |
| 5. 结果选择/正文/证据/防重复 | 只能 select 本次搜索真实返回的 URL；Fetch 正文按 SHA-256 内容寻址；`web-search:` / `web-fetch:` / `web-extract:` 三段幂等键；同 URL+正文+文档类型不能重复入库 |
| 6. 抽取/路由/可信度/质量闸门 | `InterviewPipelineAdapter` 依次跑 Extraction → Validation → Routing → Reliability → QualityGate；前三个是模型调用，Gate 是确定性规则 |
| 7. 能否关联现有公司/岗位 | 能，但只在**公司名精确匹配 + 岗位名精确/唯一基名匹配**时写入；不唯一就留空 |
| 8. 能否形成 reported_topics/Question/证据 | 能；百度这条产出 17 题 + 1 个 REPORTED_TOPIC，每题有原文片段与 claim |
| 9. 是否必须管理员逐条审核 | 是；Web 采集一律 `WAITING_REVIEW`，`approve` 后才 `APPROVED` 并索引题目 |
| 10. 可复用模块 | `WebCollectionService`、`ToolGateway`、`WebHTTPAudit`、`PublicDataResearchAgent`、`InterviewPipelineAdapter`、`ExperienceReviewService` 全部直接复用，本轮只补了"岗位基名唯一匹配"和审核后的可审计关联脚本 |

### 1.3 本轮新增的采集侧代码（很小）

- `research/agent.py`：`job_title_base()` + 唯一基名匹配。用于把「Agent Harness 研发工程师」匹配到「Agent Harness研发工程师(J100815)」；只在**该公司内唯一**时成立，否则仍留空。
- `scripts/r132_web_accept.py`：分步驱动 search/select/extract，便于限额。
- `scripts/r132_review_link.py`：审核前确认公司/岗位关联，写 AuditLog；不自动批准。

没有新增独立搜索 Agent，没有新增第二套 Snapshot/审核体系。

---

## 2. DeepSeek Search / Fetch 真实调用

| # | 用途 | 查询 | 结果 | 耗时/状态 |
| --- | --- | --- | --- | --- |
| S1 | AI Agent | `百度 Agent Harness研发工程师 面经 (site:nowcoder.com)` | 「百度Agent Harness 研发工程师 - 9月8日 - 一面 - 秋招」 | 200，1,147ms |
| S2 | Java | `携程 Java开发工程师 面经 (site:nowcoder.com)` | 「面试复盘｜携程提前批Java后端开发一二hr面」 | 200 |
| S3 | Java 备选 | `中国电信天翼云 后端开发工程师 Java 面经` | 「天翼云后端开发一面面经」 | 200（未抓取） |
| S4 | RAG | `快手 AI应用开发 RAG 面经` | 「快手 AI应用开发 二面」 | 200 |

| # | Fetch | 正文 | Snapshot |
| --- | --- | --- | --- |
| F1 | 百度 Agent Harness | 真实一面经过、17 个问题、Python 基础题 | snapshot 51，hash `db029db7…` |
| F2 | 携程 Java 后端 | 一面/二面/HR 面，Spring、线程池、HashMap、分库分表等 | snapshot 52 |
| F3 | 快手 AI 应用 | RAG 幻觉、Recall@K、事实一致性、Memory、Agent Runtime | snapshot 53（**专栏预览，约 40% 正文**） |

关键约束的执行情况：

- `need_content=False`：搜索返回的 `summary` 只作为元数据，不写快照；快照只来自显式 Fetch 的 `content`。
- 未把搜索摘要冒充正文；三条快照都来自 `public.fetch` 且带 `body_invocation_id`。
- 快手那条正文含"剩余60%内容，订阅专栏后可继续查看"，属于**不完整正文**；本轮没有把它当成完整面经使用，抽取阶段也被拒绝（见 3.2）。

---

## 3. 面经抽取、审核与入库

### 3.1 成功：百度 Agent Harness（experience 1）

| 项 | 值 |
| --- | --- |
| 任务 | refresh task 3，`INTERVIEW_DATA`，COMPLETED |
| 来源 | `https://www.nowcoder.com/discuss/926928449204129792?sourceSSR=home` |
| 公司/岗位/轮次 | 百度 / Agent Harness 研发工程师 / 一面 |
| 快照 | snapshot 51，content hash `db029db76650bff4…` |
| 抽取 | 17 道题，全部有原文片段（`evidence` 为原文行） |
| reported_topics | 1 条（"有没有考虑过使用工程的方式…解决代码规范问题"） |
| 路由 | 识别为面试经历；题目分类含 BASIC_KNOWLEDGE / PROJECT_DEEP_DIVE / SYSTEM_DESIGN |
| 可信度 | 0.72；risk flags：`community_source`、`candidate_transcription`、`unverified_company_role` |
| 质量闸门 | `passed=true`、`review_status=WAITING_REVIEW`、`recommended_action=REVIEW` |
| 公司/岗位关联 | company 6（百度）、job 31（Agent Harness研发工程师(J100815)） |
| 审核 | reviewer 22，`APPROVED`；17 题同步 `APPROVED` 且 `should_index=true` |
| 关联方式 | 抽出岗位名与 job 31 的**基名**一致；由审核脚本再确认，写 `AuditLog(experience.review.confirm_job_link)` |

题目样例（原文证据可逐条核对）：

| 题 ID | 问题 | 类型 |
| --- | --- | --- |
| 1 | 怎么理解 Harness？ | BASIC_KNOWLEDGE |
| 2 | agent 修复代码的效果是怎么评判的？最后修复正确的评判维度有哪些？ | PROJECT_DEEP_DIVE |
| 7 | 多 agent 并行修复同一文件冲突怎么办？Claude Code 文件锁了解吗？ | SYSTEM_DESIGN |
| 15 | Python 装饰器、生成器、上下文管理的应用场景？ | BASIC_KNOWLEDGE |
| 16 | 生成器 yield 之后，函数 return 的结果怎么拿到？ | BASIC_KNOWLEDGE |

### 3.2 失败：携程 Java 与快手 RAG

两条都完成了搜索、抓取、快照，但**抽取被现有证据契约拒绝**：

```
EvidenceValidationError: questions[0].fragment_id:
Fragment is not an exact reported question; source_kind=CONTEXT
```

原因不是网络或模型失败，而是：

- 模型把 `自我介绍`、`项目介绍`、`8.20 一面` 这类**上下文行**当成了 QUESTION；
- `InterviewSourceFragments` 明确判定它们为 `CONTEXT`，`question_span()` 拒绝把上下文伪装成原题；
- 这是"AI 抽取必须有原文证据"的硬闸门，**不是可以绕过的解析错误**。

本轮没有重试：每次重试要再花 3 次模型调用，预算不允许。两条任务保留为 `FAILED`，快照 52/53 与来源 URL 保留，未生成 experience/question。

另外，快手那条正文只有约 40%（专栏付费墙），即使抽取成功也应标为不完整来源；本轮直接按失败处理，没有把它算作可用面经。

### 3.3 审核语义

- Web 采集的 experience 一律 `WAITING_REVIEW`，与 `quality_gate.recommended_action=REVIEW` 一致；
- 只有 `ExperienceReviewService.approve()` 后才是 `APPROVED`，题目才进入 `should_index`；
- 本轮审核人 22 是验收库中新建的 ADMIN（`r132-reviewer@example.com`），只在独立验收库生效；
- `memory` 没有被自动批准：报告产生的 2 条 proposal 仍是 `PENDING`。

---

## 4. 公司与岗位关联

| 面经 | 公司 | 公司 ID | 岗位 | 岗位 ID | 关联依据 |
| --- | --- | --- | --- | --- | --- |
| 1 百度 | 百度 | 6 | Agent Harness研发工程师(J100815) | 31 | 岗位名唯一基名匹配 + 审核确认 |
| 携程 | 携程 | — | — | — | 抽取失败，无 experience |
| 快手 | 快手 | — | — | — | 抽取失败，无 experience |

没有猜测关联：

- 「携程 Java 后端开发」没有强行挂到「Java开发工程师（2027届秋招）」；
- 「快手 AI应用开发」即使公司匹配，也因抽取失败没有生成岗位关联；
- 百度岗位的 `(J100815)` 后缀被剥离，但只在公司内唯一时才接受，避免模糊匹配。

结果：**当前 50 个展示岗位中，只有 job 31 有 1 条已审核、可索引的面经（17 题）；其余 49 个仍为 0。**

---

## 5. Skill 匹配修复与新增评估

### 5.1 两个错配的真正原因

R131 把「百度 Agent Harness → frontend_web」归因于"状态管理"；R132 复算发现**首要原因是 `ReAct` 被 `normalized()` 统一小写后当成 `React`**：

```
"理解ReAct、CoT、Plan-and-Execute及Agent基本原理"  →  react  →  frontend_web 命中
```

`recommend()` 的 ASCII 词边界只防 `JavaScript` 里的 `Java`，防不住大小写折叠后的 `ReAct/React`。

修复（`skill_recommendation.py`）：匹配 `react` 前先把原文的 `ReAct` 掩码为 `XeAct`，保留长度与位置；不改变其他词、不改变评分公式。

### 5.2 其他修复

| 问题 | 修复 |
| --- | --- |
| frontend 的通用词误伤 | `responsibility_terms` 去掉裸 `性能优化`，改为 `前端性能优化`；`状态管理`、`性能优化` 不再单独触发 capability group |
| frontend 真岗位丢失 | 保留 `Vue`、`React`、`TypeScript`、`JavaScript`、`首屏`、`懒加载`、`打包构建` 等专有词 |
| Java 内容分不足 | 增加 `Java`/`并发编程`/`JVM调优`/`分布式系统`/`高并发` 等责任词（1.0.1） |
| AI Agent 标题不识别 | 标题别名增加 `Agent`、`AI模型`；责任词增加 `Agent` |
| 版本不可变 | 三个 Skill 新增 `1.0.1` 目录，`1.0.0` 原文保留；历史快照仍读旧指纹 |

### 5.3 50 个岗位复算

| 指标 | R131 | R132 |
| --- | --- | --- |
| RECOMMENDED | 32 | **35** |
| AMBIGUOUS | 6 | 6 |
| NO_APPLICABLE | 12 | **9** |
| frontend_web 自动推荐 | 5 | 1 |
| ai_agent_application 自动推荐 | 14 | 20 |
| java_backend 自动推荐 | 3 | 5 |

关键修复：

| 岗位 | R131 | R132 |
| --- | --- | --- |
| 百度 Agent Harness研发工程师 | frontend_web（错） | **ai_agent_application** |
| 上海华为 AI模型工程师-Agent技术 | frontend_web（错） | **ai_agent_application** |
| 上海华为 JAVA后端开发工程师 | NO_APPLICABLE | **java_backend** |
| 携程 Java开发工程师 | AMBIGUOUS | **java_backend** |
| 字节 AI Agent开发工程师 | NO_APPLICABLE | **ai_agent_application** |

代价：两个 Unity/C++ 客户端岗位从 `frontend_web` 变为 `NO_APPLICABLE`。它们不是 Web 前端，保留错配反而更糟；当前 10 个 Skill 里没有客户端/游戏引擎方向，报告为覆盖缺口，而不是硬塞。

### 5.4 是否新增 Go / 分布式 / LLM 工程

**不新增**，理由是可验证的：

- Go：50 个岗位里只有「后端研发工程师（Go/Java）」和「云原生研发工程师」涉及 Go；前者已由 `java_backend` 以 4/5 相同主题覆盖，单独建 Go Skill 会与 `java_backend` 的 `mysql_consistency/redis/kafka_reliability/system_design` 高度重叠。
- 分布式/中间件：没有独立岗位标题，现有 `java_backend` 的 Kafka/Redis/事务主题已覆盖；新增只会造成推荐歧义。
- LLM 工程/模型服务：与 `ai_agent_application` 的 `llm`、`rag_retrieval` 重叠，且 50 个岗位中没有"只做推理服务、不做 Agent/RAG"的独立岗位。

新增 Skill 的硬标准是"不同主题覆盖 + 不同提问策略 + 不同合法技术范围"，这三类都不满足。保留 10 个，记录为下一轮候选。

---

## 6. 主面试官工具：实现与真实调用审计

### 6.1 实现

复用 `MainHarness`，新增 `search_reviewed_interview_experiences`：

| 项 | 实现 |
| --- | --- |
| 作用域 | 会话创建时冻结 `reviewed_experience_scope`：只含本场精确公司+岗位、`APPROVED`、有题目的面经，最多 5 条/20 题 |
| 入参 | `topics`（0–2 个，必须来自本场候选目标）、`max_items`（1–3）；**无 SQL、无公司/岗位参数、无自由文本** |
| 返回 | 经验 ref、轮次、审核时间、可信度、来源 URL、主题、最多 3 道题（题干 ≤140 字） |
| 上限 | 沿用 `_feedback` 的 2,048 字节；最多 3 条经验；工具调用总次数仍为 2 |
| 安全 | `trust=UNTRUSTED_DATA`；未在冻结 `public_questions` 里的题不返回；不能新增题源 |
| 回退 | 空作用域返回 `NO_REVIEWED_EXPERIENCE_FOR_SCOPE`；未知主题返回 `TOPIC_OUT_OF_SCOPE` |
| 审计 | `main_harness_tool` 步骤记录工具/参数/结果；`experience_agent_runs.output_json.tool_audit` 记录是否调用、返回 refs、最终题源、是否使用工具结果、是否只是用了预加载题 |

提示版本：`2.4.0-harness.4`（旧 `.1/.2/.3` 仍可读）。

### 6.2 真实模型会话

| 会话 | Skill | Harness | Main 调用 | 工具调用 | 最终题源 | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| #638 | ai_agent 1.0.1 | 是（.3） | 2（1 次失败） | **0** | requirement:153 / 失败 | 直接出题，未查面经 |
| #639 | ai_agent 1.0.1 | 是（.4） | 1 | **0** | requirement:153 | 直接出题，未查面经 |

`tool_audit` 实测：

```json
{"tools": [], "tool_called": false, "used_tool_result": false,
 "selection_source_ref": "requirement:153",
 "reviewed_search_called": false, "reviewed_search_count": 0,
 "preloaded_public_question": false, "used_preloaded_without_tool": false}
```

模型两次都选择 JD 要求，而不是面经；`.4` 的"count>0 时可查一次"提示没有改变行为。预算用尽，无法再试第三次。

### 6.3 工具在真实冻结数据上确实可返回

用 #639 的真实快照（job 31、experience 1、17 题）直接调用工具：

```
status=OK, code=REVIEWED_EXPERIENCES
items=[experience:1, round=一面, reliability=0.72,
       topics=[agent, python],
       questions=public_question:2/3/4]
trust=UNTRUSTED_DATA
```

这说明"数据库里有、会话冻结了、工具能读"三层都成立；缺的是"模型主动发起"这一层。

### 6.4 预加载 vs 主动调用：必须分开的两个数字

| 能力 | 本轮结果 |
| --- | --- |
| 面经在会话创建时被冻结 | **是**：#640 快照含 1 条 experience + 17 道 public questions |
| 模型使用了预加载面经出题 | **是**：#640 第 3 题 `PUBLIC_QUESTION 16`，绑定 `source_question_id=16` |
| 模型主动调用工具查面经 | **否**：2 场 Harness 会话，0 次 `main_harness_tool` |
| 工具返回数据被用于最终选择 | **未发生**（无调用） |

#640 第 3 题（非 Harness）是"面经真正影响提问"的最强证据：

```
q584  source=PUBLIC_QUESTION  source_question_id=16
      → interview_questions.id=16
      → experience_id=1（百度，APPROVED）
      → https://www.nowcoder.com/discuss/926928449204129792
```

它不是工具查出来的，是会话创建时预加载、蓝图把 `public_question:16` 挂到 `ai_agent_application:python` 目标后被模型选中的。**这正好证明"预加载"与"主动检索"是两种能力，不能混为一谈。**

---

## 7. 真实请求与预算

### 7.1 Web API

| 类型 | 上限 | 实际 | 结果 |
| --- | --- | --- | --- |
| DeepSeek Search | 4 | **4** | 4×200 |
| DeepSeek Fetch | 4 | **3** | 3×200 |

### 7.2 模型 HTTP / Token

| 阶段 | 调用 | Token |
| --- | --- | --- |
| 百度抽取（extract/routing/reliability） | 3 | 15,429 |
| 携程抽取（extract，validation 拒绝） | 1 | 4,595 |
| 快手抽取（extract，validation 拒绝） | 1 | 9,057 |
| #636 Java 会话 | 3 | 25,244 |
| #637 RAG 会话 | 1 | 10,533 |
| #638 Harness 会话（含 1 次失败 Main） | 3 | 28,149 |
| #639 Harness 会话 | 1 | 10,949 |
| #640 完整面试 + 报告 | 7 | 59,513 |
| **合计** | **20 / 20** | **163,469 / 200,000** |

没有计算人民币费用：本轮没有查询提供商账单，也没有可靠的单价，不用估算值冒充实测。Token 来自 `experience_agent_step_runs.token_usage_json`。

达到 20 次后立即停止；没有自动重试失败付费请求。

---

## 8. 完整面试与报告（#640）

| 项 | 值 |
| --- | --- |
| 岗位 | 百度 · Agent Harness研发工程师(J100815) |
| Skill | `ai_agent_application 1.0.1`，Main Prompt `3.0.0`，Evaluation `2.3.4` |
| 开场 | 脚本化自我介绍（0 模型） |
| 正式题 | 3 道（`introduction_count=1`、`technical_question_count=3`） |
| 完成原因 | `QUESTION_COUNT_REACHED` |
| 评价 | 3 次，五维分数 3/2/2、1/1/1、1/1/1，均分 1.6667 |
| 报告 | report 18，`COMPLETED`，`answered_count=3`，Coach 1 次（6,855 Token） |
| 成长记录 | 2 条 `PENDING`，supporting evaluation ids `[567,568,569]` / `[567,568]`，未自动批准 |

三道题与来源：

| 题 | 来源 | 目标 | 内容 |
| --- | --- | --- | --- |
| 582 | `JOB_REQUIREMENT 153` | `ai_agent_application:agent` | 主面试官状态机如何决定追问/换题/结束 |
| 583 | `FOLLOW_UP` | `ai_agent_application:agent` | 哪些规则写死、哪些交给模型 |
| 584 | `PUBLIC_QUESTION 16` | `ai_agent_application:python` | Python 生成器 `yield` 与 `return` |

覆盖了：正常开场、项目/机制提问、1 次追问、1 道已审核面经题源、3 次评价、正常结束、报告、成长记录来源关联。

其他会话：

- #636 Java：2 道技术题，1 答 1 评，用户提前结束；证明 `java_backend 1.0.1` 加载并问出 Redis 秒杀链路问题。
- #637 RAG：1 道题，未作答；目标是 `rag_retrieval:agent`，**没有进入检索/RAG 细节**，只能算 Skill 加载证据，不能算方向深度证据。
- #638/#639 Harness：0 次工具调用；#638 在第二次 Main 被 `INVALID_QUESTION_GAP_SCOPE` 拒绝（模型选了 FOLLOW_UP 但 `focus_gap_ids` 为空），属于既有契约，不是本轮改动导致的回归。

---

## 9. 当前不能完成的功能与原因

| 未完成 | 原因 | 影响 |
| --- | --- | --- |
| 主面试官真实主动调用面经工具 | 2 次 Harness 会话都直接出题；预算 20/20 用尽，无法第三次尝试 | C 只有"实现 + 离线真实数据"证据，没有 Live 调用证据 |
| Java / RAG 面经入库 | 抽取把 CONTEXT 行当 QUESTION，被证据契约拒绝；重试需 3 次/条，预算不足 | 3 个方向只完成 1 条；两条仅保留来源快照 |
| 3 题全部作答的面试 | #640 完成；#638 中途被 gap-scope 拒绝 | D 有 1 场完整证据，但不是 3 场 |
| RAG 方向深度提问 | 首题落在 `agent` 子主题，预算不足以追问到检索 | B 的 RAG 证据偏弱 |
| 50 岗位面经覆盖 | 只完成 job 31 一条；其余 49 个为 0 | 面试仍以 Skill + JD 为主 |
| 10 个 Skill 全部 Live 使用 | 本轮只真实用了 ai_agent、java_backend、rag_retrieval 三个 | 其余 7 个仍只有投影/冻结证据 |

---

## 10. 与 R131 对比：哪些从"部分实现"变成"有 Live 证据"

| 能力 | R131 | R132 |
| --- | --- | --- |
| DeepSeek Search/Fetch | 只有代码与配置 | **真实 4 搜 3 取，200 成功** |
| Source→Snapshot→Experience→Question | 正式库 5 条历史数据，展示库 0 | **验收库新增 1 条真实面经、17 题、可追溯 URL/快照/claim** |
| 公司/岗位关联 | 0 条精确岗位 | **1 条精确关联（百度 job 31）** |
| 审核 | 历史 5 条同秒批量 APPROVED | **本轮单条审核、reviewer/时间/审计齐全，17 题同步索引** |
| 面经影响提问 | 仅 V1 历史 16 题 | **#640 q584 真实使用 PUBLIC_QUESTION 16，可追到牛客原帖** |
| Skill 匹配 | 2 个已知错配 + Java 漏配 | **修复并回归；50 岗位自动推荐 32→35** |
| 10 个 Skill | 2 个有运行样本 | 3 个有运行样本（ai/java/rag） |
| Main 工具 | 0 真实调用 | 工具实现 + 真实冻结数据可返回；**模型主动调用仍为 0** |
| 完整面试+报告 | #625/#632 等历史 | **#640 真实 3 题 + 报告 18 + 2 条待确认记忆** |
| 预算 | 未使用 | **20 HTTP / 163,469 Token，全部可核算** |

---

## 11. 工程改动与验证

改动文件：

- `modules/interview/skill_recommendation.py`：ReAct/React 掩码。
- `interviewer_skills/{ai_agent_application,java_backend,frontend_web}/1.0.1/`：匹配规则修正；1.0.0 保留。
- `modules/interview/context.py`：冻结 `reviewed_experience_scope`，投影面经轮次/URL/审核时间。
- `modules/interview/context_projection.py`：Main 投影隐藏完整作用域，只暴露 `reviewed_experiences` 可用性；兼容 harness.2/.3/.4。
- `modules/interview/adaptive.py`：把作用域 refs 加入 `allowed_refs`。
- `modules/interview/main_harness.py`：新增工具、审计 `tool_audit`。
- `modules/interview/adaptive_prompts.py`：harness.3/.4 提示。
- `modules/public_career/research/agent.py`：岗位唯一基名匹配。
- 测试：`test_r132_skill_matching.py`（4）、`test_r132_experience_tool.py`（5），并更新 3 个旧测试的版本假设。

测试结果：

```
test_r132_skill_matching.py        4 passed
test_r132_experience_tool.py       5 passed
test_r126_skills.py                4 passed
test_interviewer_skills.py        18 passed, 2 skipped
test_v211_director_contract.py    19 passed, 2 skipped
test_r127/r129/r130 + R132        13 passed, 12 skipped
```

未 Push。验收库 `interview_agent_showcase_r132_20261118` 保留现场；正式库 `interview_agent` 与展示库 `interview_agent_showcase_r123_20261117` 未写入。

---

## 12. 下一步（按优先级）

1. **让工具真正被调用**：不是加强制指令，而是给模型一个"必须依赖面经才能答好"的场景（例如用户明确要求按该公司真实面试节奏），并在预算内至少留 3 次重试；本轮 0 次调用主要是预算和场景问题。
2. **修复抽取的 CONTEXT 误选**：在提示中要求"只选 `source_kind=QUESTION`；没有就返回空"，并给一次**免费**的确定性修复（丢弃非法片段而不是整单失败）；不要用重试掩盖。
3. **补 Java/RAG 面经**：优先选非付费墙、题干带问号的帖子；先做 1 条端到端，再扩到 3 条。
4. **RAG 首题偏 Agent**：检查 `rag_retrieval` 的蓝图优先级与 `opening_category`，让第一题落在检索/重排，而不是通用 Agent。
5. **Go/分布式/LLM 工程**：等出现 3 个以上只覆盖该方向的岗位，再按"不同主题+不同策略"标准新增。
