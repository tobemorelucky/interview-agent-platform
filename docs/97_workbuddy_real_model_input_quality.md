# 第十八轮：真实模型下的简历画像、JD 解析与 Skill 推荐质量

> 目的不是加功能，而是回答一个产品问题：**普通用户粘贴自己的简历和 JD，经过真实模型处理后，
> 得到的东西是否足够可信、能否支撑后续面试。**
>
> 本轮真实模型：**6 次 HTTP / 11,470 token**，全部经由正式准入与结算记录（`model_admissions` 6 行，均为 `SETTLED`）。
> 真实调用上限为 8 次 / 150,000 token / 25 分钟，实际用量约为调用上限的 75% / 8% / 约 1 分钟。
>
> **样本量声明**：2 份合成简历、1 份真实归档公开 JD、1 份合成对照 JD。这只能提供**针对性证据**，
> 不能声称达到大规模统计可靠性。下文每个结论都标了它基于几条样本。

---

## 1. 样本来源与人工标注规则

### 1.1 四份样本

| 编号 | 内容 | 性质 | 位置 |
|---|---|---|---|
| A | AI Agent 方向候选人简历：Python 主力、LangGraph 多轮问答、RAG（含召回率提升）、Redis 缓存优化、Kafka 仅预研未上线、K8s 仅了解、Go 没用过、明确写了"没有做过正式的线上部署与容量压测" | **合成** | `.runtime/r18/samples/resume_a_ai_agent.txt` |
| B | 易误判简历：Java 实际 3 年、Spring Boot、Redis 分布式锁；K8s 只参加过培训考试；Flink 明确没用过；Vue 只了解语法；风控规则引擎由同事负责；2024 年一段模糊的"数据平台支持工作" | **合成** | `.runtime/r18/samples/resume_b_misleading.txt` |
| C | 华为「AI 应用工程师」AI 技术应用方向，逐字截取 579 字 | **真实公开归档** | `.runtime/r18/samples/jd_c_real_huawei_ai.txt` |
| D | 同页岗位的 Java/Kafka/Redis 对照版本，人工改写 | **合成对照**（文件中首行已标注，不得当作企业原始 JD） | `.runtime/r18/samples/jd_d_synthetic_java_variant.txt` |

C 的来源与逐字性可核：

- `source_url`: `https://career.huawei.com/cn/job-details?advertisementId=36390`
- 源文件：`data/manual_sources/2026-10-05/jd_01_huawei_ai_application.txt`（1,093 字，sha256 `6484e324…b356`）
- 切片 sha256 `d7344e24…575c6`，**17/17 非空行均逐字存在于源文件**；切片省略了同一页面的第二个方向（AI 系统软件），
  没有把两个方向的要求拼成一套"同时满足"的条件。
- 采集方式记录在 `manifest.json`：`PUBLIC_BROWSER_RENDERED_BODY_MANUAL_CURATION`，`live_search_used=false`。

### 1.2 标注规则

标注文件 `.runtime/r18/annotation.json`，**在调用真实模型之前**由人工写定，模型输出不参与标注：

- `must_have_facts`：简历里真实存在、应当被画像记住的事实，每条带一个 **逐字锚点**（`anchor` 必须是简历原文子串，
  已用脚本校验 100% 命中）。
- `negative_facts`：明确"仅了解 / 没做过 / 由别人负责 / 没有上线"的限定信息，规定它**最多**能被写成什么状态。
- `must_have_requirements` / `must_not_invent`：JD 里明确写了什么、以及**不能编造**什么（学历年限、LangGraph、
  Kafka、Redis 等原文没有的资格条件）。
- 预期 Skill 方向仅用于判断"排序是否符合 JD 技术方向"，不作为唯一正确答案。

比对脚本 `.runtime/r18/score_quality.py` 只做机械化部分：某条标注锚点是否出现在**被画像事实引用**的证据原文里
（`SUPPORTED` / `EVIDENCE_EXISTS_BUT_NOT_CITED` / `MISSING`）。分类是否正确、归因是否合理，由人工复核。

---

## 2. 真实简历抽取质量（样本 2）

### 2.1 机械对齐结果

| 指标 | A（AI Agent） | B（易误判） |
|---|---|---|
| 技能事实 | 17 | 6 |
| 项目事实 | 3 | 3 |
| 工作经历 / 实习经历 | **0 / 1** | 1 / 0 |
| 教育 | 1 | 1 |
| 证据行被引用率 | 19/19 | 16/16 |
| 人工标注「应有事实」被支持 | **7/7** | **4/4** |
| 限定性信息（negative）出现在被引证据中 | **7/7** | **5/6** |

"证据行被引用率 100%"的含义是：简历切出来的每一条证据都被某个画像事实引用了，没有出现"抽了但没人用"的空转行。

### 2.2 结构性防线确实生效

画像不是让模型自由写事实：模型只能**选证据 + 给标签**，落库的 `statement` 取被引用证据里最长的一段原文
（`LONGEST_CITED_EXCERPT_V1`），技能状态还要经过服务端上限校验：

| 运行 | 技能 | 模型提议 | 服务端上限 | 最终 |
|---|---|---|---|---|
| A | RAG 检索增强 | `DEMONSTRATED` | `MENTIONED` | **MENTIONED** |
| B | Java | `STRONG_EVIDENCE` | `DEMONSTRATED` | **DEMONSTRATED** |
| B | Redis | `STRONG_EVIDENCE` | `DEMONSTRATED` | **DEMONSTRATED** |

这是本轮"不会把'了解'提升为'熟练掌握'"的直接证据（3 条被降级）。同时：

- A 的 Kafka / Kubernetes / Java 全部停在 `MENTIONED`，没有被写成"有具体使用依据"。
- A 的 `Go（没有使用过）` **没有**进入技能列表，而是出现在 `unknowns`（"Go 使用经历：未提供证据"）。
- B 的 `Flink（没有使用过）`、`Kubernetes（只参加过培训）`、`Vue（只了解语法）` 均为 `MENTIONED`，未升级。

### 2.3 主要错误（真实缺陷）

**错误 1（重要）：全职工作经历被归类为"实习经历"。**
A 的画像里 `work_experience` 为空，`internship_experience` 里放着"2023.07-2025.06 某互联网公司 Python 后端开发工程师"，
而这条事实引用的 4 条证据的 `section` 明明是 `work`。根因在第一阶段抽取（`resume_parse_v1`）就已经发生：
它把这段放进 `internships`、`work_experience` 返回空数组。用户在画像页会看到"实习经历"，与简历原文的"工作经历"不符。

本轮**没有**修改抽取行为：任何"按小节标题重排"的启发式都会影响同时含工作与实习的简历，需要单独设计验证。
建议的最小方案见 §10。

**错误 2（偏保守）：RAG 被低估。**
A 的 RAG 项目有量化结果（召回率 62%→78%），但技能标签是"RAG 检索增强"，而项目证据原文写的是"RAG 部分用 pgvector…"，
标签不是原文子串，服务端按规则把上限压到 `MENTIONED`。这是**保守错误**（不会造成夸大），但会低估候选人。

**错误 3（表述问题）：否定式提及也被记成技能。**
B 的 `Flink：没有使用过` 进了技能表（`MENTIONED`），A 的"MCP 协议…还没有落地项目"同样。文案是"仅提及"，
不构成夸大，但读者容易误以为"他提到过 = 他会"。

**错误 4（1/6 限定信息丢失）：** B 的"风控规则引擎由同事负责，我没有参与"没有被任何事实引用，
画像里只剩"我负责对账批次调度与差异单生成"（本身是限定表述，不算编造）。

### 2.4 另一处显现出来的空状态

A 的画像页"简历摘要"显示"当前简历材料中未提供相关证据"。实际原因是抽取阶段的 schema 把
`candidate_summary` 固定为 `None`（`CandidateFactsExtraction` 不允许第一阶段做综述），
不是"没有证据"。这句话会误导用户，属文案缺陷（不涉及数据）。

---

## 3. 真实 JD 解析质量（样本 2 份 JD，各 1 次真实调用）

| 指标 | C（真实华为 JD） | D（合成对照） |
|---|---|---|
| `parse_mode` | LLM | LLM |
| 抽取要求条数 | 4 | 5 |
| 每条 `evidence` 是否为原文逐字 | **4/4** | **5/5** |
| `job_title` | `AI应用工程师`（原文首行） | `AI应用工程师` |
| `company_name` | `null`（切片里没有公司行，**没有编造**） | `null` |
| 明确的 `REQUIRED` | **0** | **0** |
| `PREFERRED` | 0 | 1（"有调用大模型接口的经验者优先"） |
| 编造原文没有的资格条件 | 无 | 无 |

"逐字"是通过服务端的 `_verbatim` 校验落库的：定位不到原文的条目会被**丢弃**而不是保留，因此
抽出来的 4 条 / 5 条都是能被原文对上的。

**关于 `REQUIRED = 0`**：这是本轮最需要说清楚的一条。它不是模型偷懒，而是**规则本身决定的**：

- JD 抽取的 system prompt 规定 `Set importance REQUIRED only when the text itself makes it mandatory (必须/须/至少/要求/硬性)`。
- C 的"岗位要求"段落写的是"1、人工智能…等相关专业；2、熟悉主流大模型应用开发框架…；3、具备扎实的编程能力…"，
  没有"必须/至少/要求"这类词，所以按规则只能是 `UNKNOWN`（前端显示"原文未标明"）。
- D 里唯一带"优先"的一条被正确标成 `PREFERRED`，说明**优先项识别是有效的**，缺的是"段落即强制"的推断。

同一条规则在 RULE 解析上表现一致（见 §4），所以这是**产品策略缺口**，不是模型能力问题。

---

## 4. JD 的 LLM 与 RULE 差异（同一文本，零额外调用）

对 C、D 分别跑确定性规则解析 `rule_extract()`：

| | LLM | RULE |
|---|---|---|
| C 条数 | 4 | 5 |
| C 是否混入"工作地点"与城市列表 | 否 | **是（2 条噪声）** |
| C 的 `REQUIRED` | 0 | 1（"具备扎实的编程能力…**至少**一种编程语言"，命中"至少"） |
| D 条数 | 5 | 6 |
| D 是否混入"工作地点" | 否 | **是（2 条噪声）** |

结论（2 份样本）：**LLM 的主要价值是分段与去噪**——它能识别"工作地点/城市列表"不是任职要求；
RULE 只按小节切行，容易把地点段落当成要求。两者在"强制 vs 优先"的判断上几乎一致，都受关键词约束。
LLM 没有引入原文之外的条件，也没有把 RULE 漏掉的要求补全成编造项。

---

## 5. Skill 推荐与 Requirements 的对应关系

推荐全部来自既有确定性规则（`recommend_for_target`，`POLICY_VERSION 1.1.0`），权重
`category 10 / title 8 / responsibility 6 / capability_group 3`，阈值 `MIN_SCORE 9`、`MIN_MARGIN 3`；
**0 次模型调用**，不写注册表。

### 5.1 三个版本的实测

| 目标 | 版本 | 推荐首位 | 分 | 证据构成 | `input_fingerprint` |
|---|---|---|---|---|---|
| C（真实 JD） | v1 → v2 | `ai_agent_application` | 20 | 1 条 `VERBATIM_FROM_JD` + 1 条标题 + 3 条职责 | `fb0d534c…` → `cecbc a9c…` |
| D（合成对照） | v3 | `ai_agent_application` | 20 | 标题 + 职责 + 分组，全部非逐字 | `d9b6212e…` |

C 的候选分布：`ai_agent_application 20`（eligible）、`bank_it 6`（excluded）、`java_backend 6`（excluded）。
C 的 JD 正文里出现"Java"，所以 Java 后端命中 6 分但被排除（该 Skill 的排除词命中了 JD 的职责描述）。

### 5.2 两个必须记下来的反例

**反例 1：标题权重压过要求文本。**
D 的岗位要求是 Java / Spring Boot / Kafka / Redis，RAG 只写在"优先"里，但推荐首位仍是 `ai_agent_application`（20）
而不是 `java_backend`（15）。原因可拆开验证：保留 D 的全部要求与职责、**只把标题换掉**再离线跑规则：

```
title='AI应用工程师'    -> ai_agent_application 20, java_backend 15
title='Java 后端工程师' -> java_backend 15, ai_agent_application 12
```

也就是说：要求文本本身给的是"Java 领先"（15 vs 12），**是标题的 8 分把结论翻了过来**。
D 是我的合成样本、标题沿用了原岗位的"AI应用工程师"，所以这个反例部分是样本造成的；
但它精确指出了机制：**当用户粘贴的标题与要求方向不一致时，标题决定首位**。

**反例 2：`bank_it` 的"数据库"命中来自"向量数据库"。**
C 的 `bank_it` 拿到 6 分，证据原文是"…向量数据库等…"。这是子串匹配的语义误判（该候选已被 `excluded`，
不会成为推荐），但它会以"岗位的编程、数据库与该专业规则相符"的理由出现在候选列表里，容易让用户困惑。

### 5.3 编辑要求之后（本轮修复的一个真实缺陷）

我用产品自己的"添加一条要求 + 保存要求修改 + 重新确认"在 C 上做了 v2：

- `input_fingerprint` 变化（`fb0d534c…` → `cecbc a9c…`），`content_fingerprint`（只覆盖 raw_text）**不变** —— 与第十六轮设计一致。
- 未改动的 4 条要求**保留** `evidence` 与 `source=LLM`；新增的 Kafka 条目 `evidence=None`、`source=USER`，
  在推荐里报 `USER_ADDED_NO_VERBATIM_QUOTE`，不假装配有原文。
- 推荐首位不变（`ai_agent_application` 20），原逐字证据仍在。

**这里发现并修复了一个数据丢失缺陷**：`update_requirements` 原来用"编辑器提交的 statement+importance"整表重建，
于是**任何一次编辑都会清空所有要求的逐字证据**。第一次实测（在 D 上）时，5 条 LLM 要求全部退化成
`USER_ADDED_NO_VERBATIM_QUOTE`。修复方式是：按语句匹配保留旧行的 `evidence / topic / source`，
只有真正改动或新增的行才是无证据的。回归测试 `tests/test_requirement_edit_evidence.py`（3 条）覆盖了
"未改动保留证据""新增条目标记为无引用""改动后的语句不继承旧引用"。

---

## 6. 证据匹配错误与不确定性

| 现象 | 出现次数 | 影响 |
|---|---|---|
| 推荐证据里"逐字 JD 原文"只占少数（C：5 条中 1 条） | 2/2 JD | 理由串（"岗位的AI应用、RAG与该专业规则相符"）主要来自标题与职责，不是要求原文 |
| 职责类证据标注 `STRUCTURED_DUTY_NOT_VERBATIM_CHECKED` | C 3 条、D 1 条 | 界面明确说"未逐字核对"，没有冒充引用 |
| 标题类证据标注 `USER_SUPPLIED_TITLE_NOT_A_JD_QUOTE` | C、D 各 1 条 | 同上 |
| 子串语义误判（"向量数据库"→"数据库"） | 1 处（`bank_it`，已 excluded） | 不改变首位，但理由文案会误导 |
| 技能状态被服务端下调 | 3 条（§2.2） | 保守方向，不会夸大 |

**结论**：推荐结论目前"方向对、依据弱"。用户能看到"为什么"，但多数"为什么"不是 JD 原句，
而是标题/职责的结构化命中；这在 `reason_note` 和 `evidence_status` 里是**如实标注**的，不是隐藏的。

---

## 7. 真实 HTTP / Token / 延迟审计

| # | 路由 | 用途 | 状态 | 延迟 | Token |
|---|---|---|---|---|---|
| 1 | `resume_report`（SDK） | A 简历结构化抽取 | SETTLED | 8,647 ms | 2,259 |
| 2 | `resume_profile` | A 画像事实选择 | SETTLED | 3,947 ms | 2,764 |
| 3 | `resume_report`（SDK） | B 简历结构化抽取 | SETTLED | 5,244 ms | 1,711 |
| 4 | `resume_profile` | B 画像事实选择 | SETTLED | 2,587 ms | 2,289 |
| 5 | `legacy_llm`（JD 抽取） | C 解析 | SETTLED | 3,049 ms | 1,345 |
| 6 | `legacy_llm`（JD 抽取） | D 解析 | SETTLED | 2,308 ms | 1,102 |
| | | **合计** | 6 次 | 25.8 s | **11,470** |

- 上限 8 次 / 150,000 token / 25 分钟，实际 6 次 / 11,470 token / 约 1 分钟（真实调用窗口）。
- **没有发生**：`MODEL_USER_QUOTA`、`UNKNOWN` 状态、自动重试、Repair 轮次（两次 repair 预算都没用上）。
- 所有 6 次都有正式准入记录（`model_admissions`），`reserved_tokens == actual_tokens`，无挂起。
- 费用：只记录 token（`11,470`）。本轮没有拿到计费账单，不给出金额估算，避免把估算说成事实。
- 模型：两处都解析到 `deepseek-ai/DeepSeek-V4.1-Flash-1017`（`USE_MODELSCOPE=true`），没有切换 Provider。

### 7.1 三道闸门

1. **上游闸门**（本轮新增 `apps/api/scripts/live_model_gate.py`）：在 provider 调用边界计数，
   超过次数 / token / 时限时抛 `LIVE_BUDGET_*`，并显式设置 `request_sent=False`，所以拒绝不会被记成"未知发送"。
   限制值是 `LIMITS = 8 / 150_000 / 25*60`，测试 `tests/test_live_model_gate.py`（7 条）用"计数替身 + transport 炸弹"
   证明拒绝发生在进入 provider 之前（`inner.entries` 不增加）。
2. **网络闸门**：把 `httpx.AsyncHTTPTransport.handle_async_request` 换成白名单，只允许 `api-inference.modelscope.cn`；
   这次是 transport 级而不是 `AsyncClient.request` 级，所以连 OpenAI SDK 的底层请求也覆盖。
3. **正式治理**：`ModelGovernance.reserve/settle` 仍在生效，6 条 `model_admissions` 就是它的账。

---

## 8. Capture / Replay 覆盖情况

- 采集目录 `.runtime/r18/capture/{api,worker}/`，共 **6 条**记录，每条包含：`route`、`model`、`prompt_sha256`、
  `request_bytes`、`response_text`、`usage`、`latency_ms`、`status`。
- **不保存** API Key、Authorization 头、`.env`、完整 prompt 正文；只留请求摘要（sha256）与长度。
  `tests/test_live_model_gate.py::test_capture_is_redacted_and_replays_offline` 对这些做断言。
- 覆盖范围：6/6 真实调用都有采集；**0 次**真实调用没有采集（采集失败不会被当成"已发送"）。
- 离线回放验证：`CaptureReplay` 能从 6 条记录里按请求摘要命中，或按顺序回放；
  实测回放出的第一阶段简历抽取 JSON 与线上一致（`basic_info/education/skills/projects/internships/publications`）。
- 仍未覆盖：画像 agent 的**失败重试**路径、JD 抽取的 repair 轮次——本轮都没触发，所以没有这两类采集样本。

---

## 9. 前端操作与截图（真实 Chrome 157 headless / CDP 9222）

| 步骤 | 结果 | 截图 |
|---|---|---|
| 用户 A 注册 → 上传合成简历 A → Worker 真实抽取 + 画像 | `COMPLETED`，画像第 1 版，17 技能 / 3 项目 / 19 条证据，页面标注"DeepSeek 实时模型" | `r18_01_profile_a.png` |
| 用户 A 粘贴真实 JD C → 解析 → 确认 | 4 条要求，"性质"列全部显示"原文未标明"，`parse_mode=LLM` | `r18_05_jd_c_parsed.png` |
| 推荐区块 | `AI / Agent 应用开发 v1.0.0`，第 1 位、命中权重 20、0 次模型调用、明确"不是百分比/置信度"，含逐字证据与职责证据 | `r18_05_jd_c_recommendation.png` |
| 粘贴合成对照 JD D → 解析 → 确认 | 5 条，1 条"优先" | `r18_06_jd_d_parsed.png` / `r18_06_jd_d_recommendation.png` |
| C 上编辑要求（新增 Kafka）→ 保存 → 重新确认 | 版本 2，指纹变化，未改动条目保留逐字证据 | `r18_07_jd_c_v2.png` |
| 用户 B 上传易误判简历 B | 6 技能，Java/Spring/Redis 有具体使用依据，Flink/K8s/Vue 仅提及 | `r18_04_profile_b.png` |
| 目标岗位 → "用这个方向开始面试" | 面试页目标岗位预选"AI应用工程师（第 2 版）"，技能预选 `ai_agent_application` | `r18_08_interview_preselect.png` |
| 手动改成 `java_backend` 等 3.5 秒 | 未被自动推荐覆盖，来源显示"用户手动选择" | 同上 |
| 跳转本身是否创建会话 | 0 → 0（未创建任何面试） | — |

跨账号（用户 B 读用户 A 的资源）：

| 请求 | 结果 |
|---|---|
| `GET /resumes`（B） | 只看到自己的 1 份，`A 的简历可见 = false` |
| `GET /resumes/1` | **404** |
| `GET /candidate-profile/versions/1` | **404** |
| `GET /job-targets/2` | **404** |
| `GET /job-targets/2/skill-recommendations` | **404**，响应不含岗位名/要求原文/RAG |
| `GET /job-targets/2/match` | **404** |
| `GET /job-targets`（B） | 200，`count = 0` |

---

## 10. 未解决的质量问题与下一阶段建议

按"影响用户"排序，每条都标了本轮样本数。

1. **全职工作经历被归到实习（1/2 份简历）** —— 根因在 `resume_parse_v1` 的第一阶段输出。
   建议最小方案：抽取后做一次**基于原文小节标题**的一致性核对——原文有"工作经历"节且该节有内容时，
   若 `work_experience` 为空而 `internships` 非空，把它移回；同时保留一条审计记录说明发生了重排。
   不建议让模型"再想一次"（会消耗调用且不可复核）。
2. **`REQUIRED` 在真实 JD 上恒为 0（2/2 份）** —— 这是策略缺口，不是模型缺陷。建议的最小方案是：
   在"任职要求/岗位要求"小节内、且没有"优先/加分"标记的条目，默认 `REQUIRED`；
   需要同时改 `rule_extract` 与 LLM 的 system prompt 并加回归，属于独立一轮的改动。本轮只记录，不改。
3. **推荐依据以标题/职责为主，逐字要求只占少数（2/2 份 JD）** —— 标题 8 分 + 分组分可以让"方向被标题带走"。
   建议：给"逐字要求命中"单独的加分位，或在标题与要求方向冲突时降级为 `AMBIGUOUS` 并要求用户手选。
   这会改变排序语义，需单独立项。
4. **否定式提及被当作技能（4 处）** —— 建议在技能事实里增加"仅学习/未使用"的表示，而不是只靠"仅提及"。
5. **画像页空状态文案错误（1 处）** —— "简历摘要"本阶段不生成综述，文案应说"本阶段不生成摘要"，
   而不是"简历材料中未提供相关证据"。
6. **"向量数据库"→"数据库"的子串误判（1 处）** —— 不改变首位，但会污染候选理由；
   可考虑对中文术语做词边界或白名单。
7. **仍未验证**：真实模型下的**失败重试/repair**（本轮 0 次触发）、真实用户的长简历与多语言简历、
   真实 JD 的第二种排版（表格、PDF 抄出）。

### 能否让普通用户开始使用？

**可以开始小范围试用，但还不能宣称"稳定可用"**：

- 输入侧（上传简历 → 画像 → 粘贴 JD → 推荐 → 进入面试准备）在真实模型下链路完整、证据可查、权限与冻结正确；
- 但画像会把"工作经历"显示成"实习经历"（1/2 样本），JD 的"硬性要求"一律显示"原文未标明"（2/2 样本），
  推荐理由多数不是 JD 原句。这三点都会直接影响用户对系统的信任，应先于任何新功能修掉。

---

## 附：本轮数据库与隔离资源

| 用途 | 名称 | 迁移版本 |
|---|---|---|
| E2E（浏览器 + 真实模型） | `interview_agent_wb_r18_e2e_20261113` | 0027 |
| 专项测试 | `interview_agent_wb_r18_test_20261113` | 0027 |
| 全量业务回归 | `interview_agent_wb_r18_full_test_20261113` | 0027 |
| 迁移专项 | `interview_agent_v21_wb_r18_mig_20261113` | 空库 → head |

- MinIO 桶 `interview-agent-r18-e2e`；对象键 `resumes/<user_id>/<uuid>.txt`（2 个对象）。
- Redis 逻辑库 9（缓存/限流）、10（broker）、11（result backend）。
- 生产库 `interview_agent`：`revision=0025`、`interview_sessions=19`、最新 `2026-10-07`，本轮**未写入**。
- 公开表 `companies / jobs / job_description_versions / job_requirements` 在 E2E 库中均为 **0 行**：
  私有 JD 没有被写成公共岗位或题库。
- 真实模型调用的进程内闸门与网络白名单同时生效；`ACCEPTANCE_TARGET` 日志确认库、Redis、桶三者一致。
