# 第 30 轮：《真实面经与 JD 扩充包_20261116》接收、核验与去重（真实模型 HTTP = 0）

日期：2026-11-16　基线：`2db004c`　**真实模型 HTTP = 0**

本轮只做四件事：接收 23 道面经改写题、接收 18 份合成回答、核验 13 条 JD 候选来源、跑离线契约测试。
不改 Prompt 2.3.4、不加 Agent、不改 Schema/迁移、不写业务表、不发布 JD、不删除任何历史资产。

---

## 0. 包校验

| 文件 | SHA-256 | 结论 |
|---|---|---|
| `01_2026真实面经_23道改写问题.jsonl` | `7d098afc…` | ✅ 与 `SHA256SUMS.txt` 一致 |
| `02_9道题_18份模拟好坏回答.jsonl` | `86405344…` | ✅ |
| `03_2027校招JD待采集官方来源.csv` | `2f597113…` | ✅ |
| `04_50份JD收集进度模板.csv` | `a421abac…` | ✅ |
| `05_WorkBuddy任务说明.md` | `574de742…` | ✅ |
| `README_请先阅读.md` | `600974a5…` | ✅ |

6/6 通过；包解压到 `data/incoming_20261116/`，原始压缩包未改动。

---

## 1. 三个问题的直接回答

| 问题 | 答案 |
|---|---|
| 累计有多少份**已核验的独立真实 JD**？ | **8 份**：既有 4 份（华为官方 1 + 社区策展 3）+ 本轮新获取 4 份（麒麟软件官方页，各带独立 `jobAdId`）。另有 1 份（拼多多）正文已获取但来源是高校转载，**不计入**。 |
| 多少道**有公开出处的面经问题**？ | **23 道**，但**出处今天无法复核**：原始页面 `minims.cn/archives/J20260801095700579582` 返回 **HTTP 404**，`archive.org` 在本机不可达（代理 502），因此只能是 `UNVERIFIED_SOURCE`，不能写成「公开可核验」。 |
| 还缺什么？ | ① **脱敏真实简历 1–2 份**（全链路硬前提，仍为 0）；② **42 份**达到 50 份目标的 JD（且需跨企业、跨方向）；③ 人工金标复核（`evaluation_calibration_v1.json` 仍 `DRAFT_UNREVIEWED`）；④ 真实候选人逐字回答。 |

---

## 2. 23 道面经改写题

### 2.1 契约（0 处问题）

- 23 条、ID 唯一、必填字段齐全；
- 全部 `question_origin=PARAPHRASED_FROM_PUBLIC_INTERVIEW_TOPICS_NOT_VERBATIM`；
- 全部 `is_real_interview_transcript=false`、`can_import_as_official_question=false`、`real_candidate_answer_available=false`；
- 全部 `review_status=PENDING_HUMAN_REVIEW`，难度均为 `ASSISTANT_ESTIMATE_*`（人工估计，不是标定难度）。

### 2.2 来源身份：从「公开面经」降级为「包内自述」

| 检查 | 结果 |
|---|---|
| 原始页面 GET | **404**（`minims.cn` 站点根域 200，文章路径 404，`/archives/` 亦 404） |
| 网页存档 | **未完成**：本机 `archive.org` / `web.archive.org` 均不可达（HTTP 80 连接超时、HTTPS 代理 502） |
| 页面标题/日期 | 只能引用包内字段：`source_title`、`reported_interview_date=2026-06-09`、`publication_date=2026-06-10` |
| 结论 | `UNVERIFIED_SOURCE_ORIGINAL_PAGE_GONE`：**不能声称这 23 道题现在仍可公开核验**，只能写「包内自述来源于该页」 |

这不是拒绝接收，而是把「可核验等级」如实降级：题可以留在夹具里，但任何报告都不得称其为「已核实公开面经」。

### 2.3 主题覆盖：16 道能落到系统主题，7 道落不下去

| 包内 topic | 数量 | 系统 topic | 系统正则能否识别该题 |
|---|---|---|---|
| rag | 10 | `rag_retrieval` | 5/10 |
| agent | 3 | `agent` | 1/3 |
| redis | 3 | `redis` | 3/3 |
| algorithm | 5 | **无** | 0/5 |
| cs_fundamentals | 2 | **无** | 0/2 |

- **16 道**能映射到系统词表（`rag_retrieval` / `agent` / `redis`），但其中只有 **9 道**能被 `topics.py` 的正则从题干里识别出来。rag 有 5 道（分块、Embedding 选型、Query 改写、降级、多路召回）与 agent 有 2 道（LangChain/LangGraph 选型、状态图转移）**不含任何词表关键词**——它们是真问题，但当前规则不会把它们送进对应蓝图 target。
- **7 道**（5 道算法 + 2 道计算机基础）在系统词表里**根本没有对应主题**。这不是数据缺陷，而是产品口径：平台只覆盖具体技术域，不覆盖「冒泡排序」「动态规划」「数据结构复杂度」这类校招基础题。
- 结论：这 23 道题**不能直接当作蓝图的候选池**；它们只能作离线参考与人工比对，这也符合「不因为本次 23 道题而更改真实问答动态出题机制」。

### 2.4 重复率

| 比对 | 最高相似度 | 结论 |
|---|---|---|
| 23 道题两两（归一化 + SequenceMatcher） | **0.00** | 无近重复（阈值 0.55） |
| 23 道 × V3 五题 | **0.00** | 与既有困难样本集不重合 |

同一段面经里的连续追问（如 Q20–Q23 冒泡排序四连问）措辞差异足够大，没有被判为重复；但它们属于**同一主题的四次递进**，在人工编排时应视为一组而非四个独立考点。

---

## 3. 18 份模拟回答（9 对）

| 检查 | 结果 |
|---|---|
| 数量与配对 | 18 份 / 9 对，每对恰 1 GOOD + 1 POOR |
| 来源标注 | 18/18 `ASSISTANT_SYNTHETIC_NOT_REAL_INTERVIEW`、`real_candidate=false`、`scoring_status=CANDIDATE_REFERENCE_NOT_HUMAN_GOLD` |
| 长度关系 | GOOD 均长于 POOR，比值 **1.33–1.65**（最小 1.33） |
| GOOD/POOR 相似度 | SequenceMatcher **0.07–0.25**，不是同文改写 |
| 标签泄漏 | 0 份答案正文出现 `GOOD_SYNTHETIC`/`POOR_SYNTHETIC`/`quality_label` |
| 技术检查点 | 每份 ≥2 条（GOOD 与 POOR 都有） |

### 3.1 POOR 的错误是可指认的（本轮人工标注，**不是金标**）

9 份 POOR 都能指到具体错误，例如：

- `Q09`：「BM25 和向量相似度总是同一量纲且都在 0–1」「RRF 是把两个得分相乘再取对数」——分数尺度与 RRF 定义双错，正好与第 28 轮 `RAG-A2` 的「RRF 无需调参」属同类；
- `Q12`：「LangGraph 任何节点都必须配 Checkpointer，否则框架无法运行」——与 `LG-B2/LG-C1` 的 checkpointer 绝对化同型；
- `Q14`：「GET 判断 + SET 写入天然原子」「DEL 不校验持有者」——分布式锁三处错；
- `Q15`：穿透/雪崩/击穿定义整体互换；
- `Q16`：「所有命令 O(1)」「每个请求新建线程」。

标注写入 `03_回答配对标注_非金标.json`，字段 `is_human_gold=false`、`annotation_author=ASSISTANT_REVIEW`。**这些标注不参与自动打分，也不能当评分准绳。**

### 3.2 这 9 对能做什么、不能做什么

- 能：检验 Evaluation 是否把「错误说法」改写成正确命题后计入 `covered_points`（`RAG-A2` 缺陷的定向复现材料）；检验 GOOD/POOR 的长度偏置。
- 不能：当训练金标、当 `expected_band` 真值、当真实用户历史、当 Memory 证据。答案全部是 AI 写的，长度 63–139 字，比真实回答短得多。

---

## 4. 13 条 JD 候选来源核查

逐条 GET（浏览器 UA、单次、25s 超时），保存原始响应与抽取正文，脚本 `apps/api/scripts/r29_jd_source_check.py`。

| 判定 | 数量 | 说明 |
|---|---|---|
| `JS_SHELL`（正文不在 HTML 里） | **11** | 10 条飞书招聘（沐瞳 2、蔚来 4、Unity、面壁、小鹏、小米）+ 华为 1 条；页面返回 200，但正文由前端接口渲染，静态抓取拿不到 |
| `LIST_PAGE`（官方列表页，含多岗位全文） | **1** | 麒麟软件校招页：**20 个岗位**，每个都有职责/要求 + 独立 `jobAdId` |
| `BODY_PRESENT`（单岗位全文） | **1** | 拼多多 AI Agent 研发工程师，但来源是**高校就业网转载**（`jy.bsu.edu.cn`），非雇主官方页 |

### 4.1 去重结果

| 去重维度 | 结果 |
|---|---|
| 与既有 4 份 JD 比 URL / `advertisementId` / `jobAdId` | **1 条重复**：`JD_CAND_13` 华为 AI应用工程师 = 既有 `JD01`（同一 `advertisementId=36390`） |
| 与正式库 `interview_agent` 比公司+岗位 | 无重复（该库仅 3 个 AI 相关 JD：华为、移动湖北、移动嘉兴） |
| 与快照库 `interview_agent_v231_context_20261026_a` | 无重复（该库 JD 全为 `M6 DEMO REPLAY` 合成公司） |
| 采集内部去重 | 4 个麒麟岗位正文 sha256 互不相同，标题互不相同 |

### 4.2 本轮实际取得正文的 JD（5 份）

| 企业 | 岗位 | 方向 | 字符 | 身份 |
|---|---|---|---|---|
| 麒麟软件 | AI研发工程师 | AI Agent + RAG | 1,278 | 官方列表页 + `jobAdId` ✅ |
| 麒麟软件 | Python研发工程师 | RAG/AI 应用后端 | 803 | ✅ |
| 麒麟软件 | 后端研发工程师（Go/Java） | 后端 | 838 | ✅ |
| 麒麟软件 | 云原生研发工程师 | 云原生 | 550 | ✅ |
| 拼多多 | AI Agent研发工程师 | Agent | 1,268 | 高校转载 ⚠️ 待官方页复核 |

同一页面另有 16 个岗位正文可用（C/C++、Linux 内核、嵌入式、安全、Android、测试、Qt、虚拟化、图形栈、前端、软件适配、产品、运营、法务、质量、生态），**本轮未纳入**：避免单一企业刷数量，破坏 5 个方向多样性。

**本轮一份都没有导入业务表**：按规则，导入必须走 Research → Owner → Evidence 流程，该流程本身要调用模型解析 JD，而本轮 HTTP=0。因此这 5 份正文只是**候选原件**（`data/incoming_20261116/acquired/`，带 sha256 与来源 URL），不是已发布 JD，也不在 `job_description_versions` 里。

### 4.3 50 份进度

| 项 | 数 |
|---|---|
| 目标 | 50 |
| 已核验独立真实 JD | **8**（既有 4 + 麒麟 4） |
| 待复核（正文已获取、身份未定） | 1（拼多多） |
| 已有 URL 但正文拿不到 | 10（沐瞳 2、蔚来 4、Unity、面壁、小鹏、小米） |
| 重复不计 | 1（华为） |
| **距目标** | **42** |

**不补造**：没有为凑数写任何摘要、模板或「AI 生成 JD」。`04_50份JD收集进度模板.csv` 的 50 个槽位保持空白。

---

## 5. 夹具与离线验证

### 5.1 夹具（`data/sandbox_test_pkg_20261114/R29_面经扩充包_20261116/`）

| 文件 | 内容 |
|---|---|
| `01_面试改写题_夹具.jsonl` | 23 条，附 `fixture_kind=PUBLIC_INTERVIEW_PARAPHRASED_QUESTION`、`model_generated_question=false`、`official_question_bank_import_allowed=false`、`forbidden_use=[AS_MAIN_AGENT_LIVE_QUESTION_RECORD, AS_OFFICIAL_PUBLISHED_QUESTION]` |
| `02_模拟回答_夹具.jsonl` | 18 条，附 `not_real_candidate_answer=true`、`not_human_gold=true`、`forbidden_use=[AS_TRAINING_GOLD, AS_REAL_USER_HISTORY, AS_MEMORY_EVIDENCE]` |
| `03_回答配对标注_非金标.json` | 9 对 POOR 的错误点，`is_human_gold=false` |
| `MANIFEST.json` | 源包 6 个文件的 sha256、来源 404 记录、约束（`model_calls=0`、`written_to_business_tables=false`） |

**没有写任何业务表**：`interview_questions`、`interview_answers`、`capability_memories` 均未新增行；夹具不是「Main Agent 出题记录」，字段层面就能区分。

### 5.2 契约测试（`apps/api/tests/test_r29_real_pack_contract.py`，9 passed）

1. 23 条、ID 唯一、题干非空；
2. 23 条全部声明「改写 / 非真实逐字 / 非模型出题 / 非官方题库」；
3. 两两相似度 < 0.55（当前最高 0.00）；
4. 主题映射显式：16 映射 / 7 未映射，且未映射必须给理由；
5. 18 份、9 对、标签 9/9、全部合成且非金标；
6. GOOD 明显长于 POOR（≥1.2）且不相似（<0.5）、检查点 ≥2；
7. 标签不泄漏进答案正文；
8. 标注文件显式 `is_human_gold=false` 且覆盖 9 对；
9. MANIFEST 记录的源包 sha256 与磁盘实际一致。

这些断言都能在坏输入上失败：改一个 `can_import_as_official_question`、删一份回答、把两条 POOR 写重复、改写源包任一文件，都会红。

---

## 6. 数据安全与资产保护

| 项 | 状态 |
|---|---|
| 真实模型调用 | **0**（全部为本地文件、只读 GET、离线测试） |
| 业务表写入 | 0（`interview_agent` 仍 0025 / 38 简历 / 19 场 / 41 评价 / 5 报告） |
| 新建数据库 | 0（`pg_database` 245 = 242 业务 + 3 系统，与第 29 轮一致） |
| MinIO | 仍 48 对象，未上传 |
| 闸门 | `purge_execution_enabled=false`、`interview_coach_harness=false` |
| 历史资产 | 未删除/未归档/未改写任何 JD、简历、面试、Evaluation、Coach、Memory、Capture |
| Git | 未 push / reset / clean；未改 `AGENTS.md`、`CLAUDE.md`、`.claude/` |
| 网络 | 只读 GET：13 条 JD 候选 + 1 条面经源页；`archive.org` 不可达（已记录，未绕过） |

---

## 7. 下一步（等真实简历）

顺序不变，且**不得跳步**：

1. 你上传 1–2 份脱敏真实简历（DOCX/PDF，去姓名/电话/邮箱/证件号）+ 岗位意向 + 授权；
2. 先做**零 HTTP 预检**：脱敏校验、文件落点、`resume_report` 路径、沙箱账本目标（`DATABASE_URL` 与 `MODEL_LEDGER_DATABASE` 必须同时指向沙箱）；
3. 再单独申请真实模型预算：1 场 3 题，**13 HTTP / 90,000 token / 25 min** 硬上限；
4. 3 题冒烟通过后，才讨论更长场次，**不自动启动 18 题完整面试**；
5. JD 侧继续按「官方页正文 + `jobAdId`/`advertisementId` + sha256」逐份补到 50，优先补不同企业/方向，不用转载页与摘要凑数。
