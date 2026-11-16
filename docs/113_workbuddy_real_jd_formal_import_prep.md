# R31 真实 JD 正式业务入库试验准备（零 HTTP）

> 本轮真实模型调用 **HTTP = 0**。没有发送任何 LLM 请求，没有写正式库，没有新增 Agent，没有改 Evaluation 2.3.4，没有 push/reset/clean。
> 基线提交 `db40251`。实施日期 2026-11-16。

## 结论摘要

| 问题 | 结论 |
|---|---|
| 3 份目标 JD 来源是否成立 | 成立。3 家官方站点当天重渲染均在线，各有独立岗位 ID、DOM 快照、SHA-256、职责与任职资格分段，正文无导航、无他人岗位正文 |
| 能否走合法 Research 流程入库 | 能。零 HTTP 预演（真实 `PublicDataResearchAgent` + 真实校验 + 真实审核服务）3/3 `COMPLETED`，35 条 Requirement 全部 `offset_exact` 且字面命中引用证据 |
| 一轮失败会不会留下 PUBLISHED 岗位 | 不会。注入失败后 `companies=0 / jd_versions=0 / published=0`，任务 `FAILED`，同一任务可显式重试 |
| 重复导入是否产生重复岗位 | 不会。`prepare` 复用同一任务，`run` 拒绝重跑，`publish` 复用已发布版本，`jd_versions` 仍为 3 |
| 前端岗位选择是否可用 | 可用，但**必须经过 ADMIN 审核发布**。未发布时 `GET /interviews/targets` 返回 0 行（已在 attempt-1 库实测）；发布后 3 行，且 3 份都被推荐 `ai_agent_application` |
| 3 份真实调用预算 | 正常 **6 次 HTTP**（每份 2 次），修复余量 2 次，硬上限 **10 次 / 30,000 token** |
| 正式业务表里的真实 JD 数量 | 生产库仍是 **4 份**（未变）；本轮正式沙箱 **0 份**（未获批准）；预演库 3 份为 REPLAY 产物，**不计入** |
| 缺口 | 距 50 份目标仍缺 **42 份**（已核验完整原文 52 份中，只有 8 份进入过正式业务表的历史是 4 份，见 §七） |

---

## 一、3 份目标 JD 的来源核验

选择依据：用户指定「蔚来 AI Agent / 沐瞳 Agent 或 Java 后端 / 麒麟 AI 研发」，在 R33 已采集的 52 份语料中按标题与正文匹配度挑选，未使用任何合成 JD。

| # | 企业 | 岗位 | 独立岗位 ID | 官方 URL |
|---|---|---|---|---|
| 1 | 蔚来 | 车端AI Agent与应用生态系统架构工程师 | `7673028675729656105`（飞书 position id，正文另含「职位 ID：A25338A」） | `https://nio.jobs.feishu.cn/campus/position/7673028675729656105/detail` |
| 2 | 沐瞳科技 | Agent开发工程师（平台） | `7668253932858362118` | `https://moonton.jobs.feishu.cn/campus/position/7668253932858362118/detail` |
| 3 | 麒麟软件 | AI研发工程师 | `0b8de09c-3508-4144-99b2-3ddcfd88a29b`（北森 `jobAdId`） | `https://kylinos.zhiye.com/campus/detail?jobAdId=0b8de09c-3508-4144-99b2-3ddcfd88a29b` |

逐份核查结果（`data/r34_target_jd/source_manifest.json`）：

| 项 | 蔚来 | 沐瞳 | 麒麟 |
|---|---|---|---|
| 采集方式 | 无头 Chrome `--headless=new --dump-dom`，当天重渲染 | 同 | 同 |
| 采集时间（UTC） | 2026-11-16T08:16:20Z | 2026-11-16T08:16:28Z | 2026-11-16T08:16:32Z |
| 是否仍有效 | 是（无「已下线/不存在」标记，正文完整） | 是 | 是 |
| 正文长度 | 1,461 字 | 1,111 字 | 1,161 字 |
| 正文 SHA-256 | `bfff84a1fdc42da8…` | `315c006260b5bdb7…` | `bf918ee9c90c2b6d…` |
| 职责条数 | 7 | 8 | 7 |
| 任职资格条数 | 10 | 8 | 9 |
| 加分项条数 | 5 | 3 | 0（该岗位无加分项段） |
| 企业名/岗位名在正文中 | 是/是 | 是/是 | 是/是 |
| 导航/页脚残留 | 无 | 无 | 无 |
| 他人岗位正文 | 无 | 无 | 无 |
| 与 R33 语料比对 | 同 `position id`，同岗位 | 同 | 列表页 `<a href>` 指向同一 `jobAdId` |
| 与正式库 4 份 JD 比对 | 不重复 | 不重复 | 标题与「中国移动湖北/AI研发工程师」同，但企业不同 → 不重复（唯一键是 company+title） |
| 内容缺失（相对 R33 采集） | 0 行 | 0 行 | 0 行（6 行为标点/标签差异：`岗位职责：`→`工作职责`、`任职要求：`→`任职资格`、`工作地点：`/`发布时间：` 前置标签） |

**关于「不得伪装成官网逐字原文」**：这三份**不是改写稿**，是官方页面 DOM 的投影文本。投影规则固定且可复算：`文档标题行 + [岗位标题行 … 正文末行]`，只剔除夹在两者之间的**全局导航行**和**页脚 CTA 行**，剔除内容逐条记录在 `source_manifest.projection.dropped_nav_lines / dropped_footer_lines`。原始 DOM 原样保存，可随时重放该投影。

**必须说明的一处降级**：R33 采集时麒麟这一条取的是官方列表页（同一页面含 20 个岗位），本轮改用官方详情页（独立 `jobAdId`），因此正文标点与段标题与 R33 切片略有差异——这是**同一岗位的两种官方呈现**，不是内容变更。以详情页为准。

产物：`data/r34_target_jd/{text,dom}/*`、`source_manifest.json`、`precheck.json`、`replay_report.json`、`verify_report.json`。

---

## 二、正式 Research 入库：实际入口、字段映射与硬约束

### 2.1 真实链路（以现有代码为准，不是设计稿）

```
① ManualImportService.prepare(document)          manual_import.py:110   0 次模型
   → sources / source_snapshots / public_data_refresh_tasks(READY_FOR_EXTRACTION)
② PublicDataResearchAgent.run(task_id)           research/agent.py:43   2 次模型
   EXTRACTING → job_jd@1.0.1 (EXTRACT_ROUTE)
              → quality@1.0.0 (QUALITY_ROUTE)
   VALIDATING → 身份/证据/字面校验
   PUBLISHING → _publish_job（纯确定性）→ companies/jobs/claims/claim_evidence/job_description_versions/job_requirements
   COMPLETED
③ PublicCareerReviewService.publish_jd(...)      public_career/review.py:24   0 次模型
   WAITING_REVIEW|APPROVED → PUBLISHED，实体置 ACTIVE，写 audit_logs
④ recommend_jobs(db, job_ids)                    skill_recommendation.py:213  0 次模型
⑤ GET /interviews/targets                        interview/router.py:59       0 次模型
```

**结论（对应「检查是否存在无需模型即可合法完成的步骤」）**：①③④⑤ 以及 `_publish_job` 的全部落库动作都是零模型；**只有 ② 的两次抽取/评估必须是模型**，因为 `JobJDExtraction` 与 `ResearchQuality` 按设计是模型产物，代码里没有任何规则回退路径。**没有为了凑调用次数强行发请求**，也没有用规则抽取冒充模型输出去写业务表。

### 2.2 字段映射

`ManualDocument` → 采集层：

| 源 | 目标列 | 说明 |
|---|---|---|
| `source_url` | `sources.canonical_url` / `url_hash=sha256(canonical_url)` / `domain` | 经 `canonicalize_public_url`：只去掉 `utm_*` 与追踪参数，`jobAdId` 保留；拒绝 file/localhost/带凭据 |
| `content` | `source_snapshots.content_text` / `content_hash=sha256(bytes)` / `normalized_content` | 原文不截断，≤2MB |
| `acquired_at` | `source_snapshots.fetched_at` | 文件 mtime，元数据显式标注 `LOCAL_ACQUIRED_AT_NOT_NETWORK_FETCH` |
| `company/job_title` | `public_data_refresh_tasks.reason`（manifest JSON） | 供 `validate_expected_identity` 比对，防止抽错岗位 |
| `key` | `public_data_refresh_tasks.idempotency_key` | `manual:sha256(url+content_hash+doc_type)` → 天然幂等 |

`JobJDExtraction` → 业务层：

| 模型字段 | 落库位置 |
|---|---|
| `company_name` | `companies.canonical_name` / `normalized_name`，`status` = 门禁结论 |
| `job_title` | `jobs.canonical_title` / `normalized_title`，唯一键 `(company_id, normalized_title)` |
| `identity_evidence_ids` | `claims`(身份) + `claim_evidence`；`job_description_versions.metadata_json.identity_claim_id` |
| `jd_evidence_ids` | 决定 `raw_text`：**取被引用 span 所属快照的完整正文**（不是模型选的片段），按 `compute_content_fingerprint` 排序拼接；指纹相同即同一版本 |
| `requirements[]` | `job_requirements(statement, normalized_value[:255], requirement_type, importance, claim_id)` |
| `attributes[]` | `claims` + `metadata_json.attribute_claims`（RESPONSIBILITY/LOCATION/PUBLISHED_AT/JOB_NUMBER，必须字面） |
| `ResearchQuality` | `metadata_json.quality_assessment` + `quality_gate` |

### 2.3 每步硬约束（代码强制，不是约定）

| 步骤 | 约束 |
|---|---|
| prepare | URL 合法性与 SSRF 拦截；`source_type ∈ {official, community}`；正文 UTF-8 且 ≤2MB；企业与岗位名必填；时间必须带时区 |
| run | 任务必须 `READY_FOR_EXTRACTION`（或 manual+FAILED 且显式 `force`）；无快照正文 → `Task has no fetched body evidence`；企业与岗位名必须**逐字**出现在被引用的身份证据里（空白不敏感）；`attributes` 必须字面；`web` 来源的 requirement 必须可从引用证据逐字拼出（manual 不做此校验，靠审核兜底） |
| publish_jd | 必须 `ADMIN` 且 `is_active`；必须写审核理由；**每条 Requirement 都必须有 claim_id**，身份与属性 Claim 也必须存在；每条 `claim_evidence` 的 `content_text[start:end] == excerpt` 且快照 sha256 一致；`matching_constraints` 只能取原文出现的值；`requirement_topics` 必须同时出现在「要求文本」与「引用证据」中 |
| 前端可见 | `companies.status=ACTIVE` + `jobs.status=ACTIVE` + `job_description_versions.publication_status='PUBLISHED'` |

**Owner / 权限**：模型调用的归属由 `run_owner()` 从 FK 链解析——`experience_agent_runs.refresh_task_id` 存在 → `owner="system:public-career"`，不接受客户端传入。发布环节的 Owner 是 `ADMIN` 用户 + `audit_logs` 留痕。两者都不是"谁能调 API 谁就能发布"。

---

## 三、零 HTTP 合法导入预演（9 项检查）

实现：`scripts/r34_replay_import.py`。用 `ReplayModelProvider`（项目内既有的回放 Provider）喂入**由规则从真实正文切出的 fixture**，其余全部走真实代码路径——真实状态机、真实 `EvidenceCatalog` 校验、真实 `_publish_job`、真实 `publish_jd`。

> fixture 的每条 statement 都是快照原文的一行，evidence_ids 由该行在快照中的字符区间反查 600 字 span 得到，**不构造任何不存在的事实**。这份 fixture 只存在于 REPLAY 库，不进入正式库，也不冒充模型输出。

| # | 检查项 | 结果 |
|---|---|---|
| 1 | 3 份能否走完合法导入 | ✅ 3/3 `COMPLETED`，`jd_version_id` = 1/2/3 |
| 2 | Requirement 与 Evidence 能否对应 | ✅ 35 条全部 `offset_exact` 且 `literal`（9/9、15/15、11/11），`without_claim=0` |
| 3 | 失败是否留下错误 PUBLISHED | ✅ 注入 `_claim` 失败后：任务 `FAILED`，`companies=0`、`jd_versions=0`、`published=0` |
| 4 | 重复导入是否产生重复岗位 | ✅ `prepare.reused=true`、同 task id、`run` 抛 `Research requires READY_FOR_EXTRACTION`、`publish.reused=true`、`jd_versions` 仍 3 |
| 5 | 公司与 JD 版本是否隔离 | ✅ 3 公司 / 3 job / 各自 `version_number=1` |
| 6 | 业务表能否正确读回 | ✅ `raw_text` 与快照正文逐字相等（3/3），快照 sha256 复核通过 |
| 7 | 能否被前端岗位选择使用 | ✅ 发布后 `targets` 返回 3 行；未发布时返回 0 行（在 attempt-1 库实测） |
| 8 | Skill 推荐是否可解释 | ✅ 3 份均 `RECOMMENDED → ai_agent_application`，理由文本给出命中的规则（如「岗位的AI研发、RAG、AI应用与该专业规则相符」） |
| 9 | 失败后能否安全重试且不重复计费 | ✅ 同一 task（id=1）显式 `force` 重试成功；`ManualImportService.execute` 对非 `READY` 状态直接返回，**不会自动重发** |

补充实测数据：本轮预演共写入 `claims=59`、`claim_evidence=63`、`job_requirements=35`、`audit_logs=3`。

**未在预演中覆盖的一项**（如实列出）：同一岗位**内容变更后的版本递增（v1→v2）** 未在预演库执行，因为会给「每岗 v1」的证据集引入噪音。该路径由既有测试 `tests/test_m2b_research_agent.py::job_scenario` 覆盖（正文变化 → 新 version，旧版本保留），本轮已连同 `test_manual_public_career_import.py`、`test_public_career_publication.py` 一起重跑：**45 passed / 0 failed**。

---

## 四、前端岗位选择是否可用

可用，但**不是"入库即可见"**。

- `GET /interviews/targets` 的条件是：`companies.status='ACTIVE'` ∧ `jobs.status='ACTIVE'` ∧ 存在 `publication_status='PUBLISHED'` 的 JD 版本；返回时同时附 `interviewer_skill_recommendation`。
- 预演中 3 份 JD 由 Research Agent 写入时状态是 `WAITING_REVIEW`（不是 `PUBLISHED`），此时前端 **0 行**——这一点在 attempt-1 库有直接证据。
- 经 `PublicCareerReviewService.publish_jd`（ADMIN + 理由 + 证据偏移复核）之后才 `PUBLISHED`，前端才出现 3 行。

**这意味着**：如果只批准模型预算而不安排审核动作，3 份 JD 会停在前端不可见的状态。审核本身零模型、零外网。

---

## 五、下一次真实调用预算（只提预算，不执行）

| 项 | 值 |
|---|---|
| 正常 HTTP | **6**（`job_jd` 抽取 ×3 + `quality` 评估 ×3） |
| Repair 可能增加 | **+2**（`generate_structured` 每次调用最多 1 次结构化修复；默认无 fallback 路由，不会翻倍） |
| 硬上限 | **10 次 HTTP / 30,000 token**（超出即中止，不再发第 11 次） |
| 模型 | `modelscope` / `deepseek-ai/DeepSeek-V4.1-Flash-1017`（两条路由同模型，`governed=true`，每次调用写 `model_admissions`） |
| Prompt 版本 | `job_jd@1.0.1`（1,108 字符，manual 专用）、`quality@1.0.0`（622 字符）；冻结指纹已记录在 `verify_report.json` |
| 输入规模 | 每份：`job_jd` 输入 ≈ 2.4–2.7K 字符（prompt + 证据 1,140–1,500 字 + 包装）；`quality` 输入 ≈ 5.2–6.1K 字符（含重复的抽取结果） |
| 输出规模 | 每份结构化抽取 ≈ 3.2–3.8K 字符（预演 fixture 实测），单次上限 6,000 token |
| Token 估算 | 每份 ≈ 6.0–7.0K（6,043 / 7,023 / 6,052），3 份正常合计 **≈ 19.1K**；含 2 次修复余量按 **≈ 25K** 计，封顶 30,000 |
| 已取得可复用部分 | 3 份 DOM、投影正文、SHA-256、分段职责/要求、去重结论、证据 span 定位算法——**不需要再抓一次网页**，正式跑只需把 fixture 换成真实模型输出 |

**停止条件**（任一触发即停，且不再自动重试）：
1. HTTP 计数达 10；2. token 累计达 30,000；3. `MODEL_LEDGER_TARGET_MISMATCH` / `MODEL_LEDGER_TARGET_UNKNOWN` / `MODEL_IDENTITY_REQUIRED`（这三个是"没有请求发出去"的失败，重试无意义）；4. 同一份 JD 的抽取连续 2 次被证据校验拒绝；5. Provider 连续 2 次失败。

---

## 六、后续批量导入与失败处理

剩余 **49 份** = 45 份官方直采 + 4 份历史策展（华为/移动×2/工行，单独标注 `source_type` 与来源等级，不得伪装为官方直采）。

| 批次 | 内容 | 正常 HTTP | Token 上限 | 验收门槛 |
|---|---|---|---|---|
| 试点 | 本轮 3 份 | 6 | 30,000 | 35/35 Requirement 有 Evidence 且字面命中 |
| 第 1–5 批 | 45 份官方，每批 9 份 | 每批 18 | 每批 66,000 | 同上 + 每批去重 0 冲突 |
| 策展批 | 4 份 | 8 | 30,000 | 同上，且 `source_type=community/curated` 显式标注 |

失败处理：
- **一份一个 refresh task**，单份失败不影响同批其他份；失败任务停在 `FAILED`，**不会自动重发**（`ManualImportService.execute` 对非 `READY` 直接返回）。
- 人工介入只走 `force=True`（仅 manual + `FAILED` 允许），复用同一 task，不新建任务、不重复计费。
- 已 `PUBLISHED` 的 JD 不可原地改写（`PUBLISHED_JD_IMMUTABLE`）；正文变化 → 新版本号，旧版本保留。
- 每批入库后必须同时满足：来源快照 sha256 一致、Requirement 全部有 claim、证据偏移复核 0 失败、Skill 推荐有理由文本——四项缺一不计入「正式入库数量」。
- 面经 59 题继续留在 `data/sandbox_test_pkg_20261114/R29_面经扩充包_20261116/` 与 R33 夹具中，**不创建任何历史 Session**。

---

## 七、当前业务表正式 JD 数量

| 口径 | 数量 |
|---|---|
| 生产库 `interview_agent` 的 `job_description_versions` | **4**（移动嘉兴 AI工程师 / 工行广东 科技菁英 / 移动湖北 AI研发工程师 / 华为 AI应用工程师，均 `PUBLISHED`）——本轮未变 |
| 本轮正式沙箱 `interview_agent_wb_jd_r31_20261116` | **0**（空库，等待批准） |
| 预演库 `..._replay2_...` | 3 份 `PUBLISHED`，**REPLAY 产物，不计入任何正式口径**（库已写 `COMMENT` 标注） |
| **已取得完整原文并核验的独立真实 JD** | **52**（48 官方直采 + 4 策展） |
| 距 50 份「真实原始 JD」目标 | 已达 52，但其中**进入正式业务表的只有 4 份** |

**所以「目标 50 份真实 JD」要分两层看**：语料层已 52；业务可用层仍是 4，本轮试点是把它从 4 推到 7 的第一步。

---

## 八、Git 与资产保护状态

| 项 | 状态 |
|---|---|
| 生产库 `interview_agent` | 5 users / 38 resumes / 19 sessions / 41 evaluations / 5 reports / 4 JD versions / 0 model_admissions —— **全部未变** |
| 数据库总数 | 246 → **249**（+3：`interview_agent_wb_jd_r31_20261116`、`..._replay_...`、`..._replay2_...`，均写了用途 `COMMENT`） |
| 历史 Live 库 / 备份 | 未触碰 |
| MinIO | 生产桶 `interview-agent` 仍 **48 对象**；新建隔离桶 `interview-agent-r31-jd` **0 对象**（本轮导入链路不写对象存储） |
| Redis | 沙箱进程用 `db 7`（**0 key**）；`db 0` 仍 0；`db 1/10/13` 未触碰 |
| 冻结 Prompt | Evaluation **2.3.4 / 7,452 字符 / `e7704357b5fc37ba`** —— 未改 |
| 删除/归档/清库 | 无（未 drop 任何库、未 truncate 任何表、未移动任何历史文件） |
| Git | 本轮新增脚本/测试/数据/文档后分点提交；**未 push / 未 reset / 未 clean** |

---

## 九、阻断项与最小解决方案

| # | 阻断/风险 | 影响 | 最小解决方案 |
|---|---|---|---|
| B1 | **未获模型预算批准** | 正式沙箱无法写入（本轮据实停止） | 批准 §五 的 6 次 / 10 次上限 |
| B2 | `.env` 的 `DATABASE_URL` 仍指向生产库 | 若直接跑 CLI 会写正式库 | 运行时同时 export `DATABASE_URL` 与 `MODEL_LEDGER_DATABASE` = 沙箱名；预检已断言二者一致（`ready=true`） |
| B3 | `public_research_official_domains` 为空 | 3 份官方 JD 的门禁结论是 `WAITING_REVIEW`，且 reason 文案误写成 `unverified_or_community_source`（对官方来源是**标签错误**） | 两条路：① 在配置里登记 3 家官方域名（仍需 ADMIN 审核才能 `PUBLISHED`）；② 不改配置，直接走 ADMIN 审核。**建议 ②**，避免为了一次试点改配置 |
| B4 | Skill 推荐只给出规则理由，没有 `ranking_points` | 可解释性偏弱（`recommend_jobs` 公开路径不返回加权明细） | 属产品口径问题，本轮不改代码；验收时以 `reason` + 命中主题为准 |
| B5 | 2 份 JD 正文偏短（麒麟 1,161 字 / 沐瞳 1,111 字） | 抽取信息量有限，Requirement 条数 9–11 | 不改；验收看证据质量而非条数 |
| B6 | 麒麟的正式来源依赖「列表页 → 详情页」一跳 | 若详情页下线，正文不可复现 | DOM 已存档 + 官方 URL + `jobAdId` 三者齐备，可复现 |
| B7 | 真实原始 JD 仍缺 42 份（业务可用口径） | 无法支撑 50 岗位规模测试 | 按 §六 分批，优先补不同企业 |

---

## 十、等待批准的具体请求

请批准以下**一次**真实调用试验，之后我停下汇报：

1. 目标：沙箱 `interview_agent_wb_jd_r31_20261116` 导入上述 3 份真实 JD。
2. 预算：**6 次 HTTP**（硬上限 10），**30,000 token**（硬上限）；单次超时 60s，最坏 8 次调用 ⇒ 不超过 10 分钟。
3. 模型/Prompt：`modelscope / deepseek-ai/DeepSeek-V4.1-Flash-1017`，`job_jd@1.0.1` + `quality@1.0.0`。
4. 边界：只写该沙箱；不碰生产库、历史 Live、Evaluation、Coach、Memory、Capture/Replay 与备份；不新建 Agent；不改 Prompt。
5. 结束后交付：每份 JD 的 `Requirement × Evidence` 逐条对照、门禁结论、Skill 推荐理由、实际 HTTP/token 用量，以及是否推进到 Admin 发布。

未获批准前不再发起任何模型请求，也不启动个人完整面试流程，不继续 L3/L4 删除功能。
