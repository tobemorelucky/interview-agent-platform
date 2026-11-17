# R124 真实用户体验整改

> 基线 `09a125f`；展示库 `interview_agent_showcase_r123_20261117`（alembic 0029）。
> **本轮模型 HTTP 增量 = 0**（账本 12 行 / 85,130 token 全部来自你 14:34–14:43 的自测，非本轮）。

---

## 一、P0-1：面试第一题报 `ADAPTIVE_TOPIC_MISMATCH`

### 1.1 现场

| 项 | 事实 |
|---|---|
| 会话 | `#622`，用户 `#21`，岗位「华为云 · AI应用开发工程师」，20 题，自适应开 |
| 请求 | `POST /interview/sessions/622/start` → 422，两次（14:43:00 / 14:43:15） |
| 模型调用 | 两次各 1 次 `interview_main`（10,026 / 10,025 token），**每次都用掉了** |
| 落库 | `interview_session_questions` **0 行**、回答 0、评价 0 → 状态仍 `READY` |
| 冻结目标 | `llm / 大模型应用`、`python`、`rag_retrieval`、`system_design`、`agent`（5 个都在词典内） |

### 1.2 根因判定（不推测的部分也写清楚）

**能确定的：**

1. 失败发生在 `validate_decision` 的 NEXT 分支：`目标 topic` 不在 `mentioned_topics(问题文本)` 里。
2. 不是"词典缺 key"——五个目标 topic 全在 `TOPICS` 中。
3. 不是"跨块证据"（那是 R36 的分块问题）——本题还没落库。
4. **这是上下文契约缺口**：prompt 2.3.3 只写"explicitly name the technical topic"，却从没告诉模型**哪些字面词才算数**；`mentioned_topics` 用的是一个不到 40 个词的正则词典。
5. 校验只作用于 `NEXT`；历史成功场次（R121/R122）的首题都写了目标名（如"先从 Agent 的工具调用说起"），而追问题即使不含关键词也不受影响。

**不能确定的（不编）：**
模型那两题的确切文本**没有落库**——`experience_agent_step_runs.output_json` 按设计只存
`schema / model_invocations / usage_complete`，`research/audit.py` 明写"never store raw model responses"。
所以"是模型偏题"还是"模型写了合理的项目开场但没点到词典词"**无法从留痕判定**。

### 1.3 零 HTTP 复现（可重复）

用 `#622` 的冻结快照重建 payload，构造一个"项目开场但不点主题"的问题：

```
问题：请介绍一下你做过最有代表性的一个项目，重点讲你负责的部分。
结果：ADAPTIVE_TOPIC_MISMATCH
target_topic：llm
expected_terms：大模型、LLM、Transformer、微调、注意力
matched_topics：[]
```

说明：校验本身在执行"不能只换目标标签"的职责（这条规则**没有放宽**，`test_director_cannot_relabel_an_unrelated_question` 仍通过），但它对"模型合规成本"没有任何帮助。

### 1.4 修复（三层，都不削弱校验）

| 层 | 改动 | 文件 |
|---|---|---|
| 让模型能照做 | 冻结蓝图与模型 payload 里，每个目标新增 `mention_terms`（校验器**实际接受**的字面词） | `adaptive.py`、`context_projection.py`、`topics.py` |
| 可诊断 | 拒绝时把 `target_topic / expected_terms / matched_topics / question_excerpt / sha256` 写入 `audit_logs`（`action=ADAPTIVE_TOPIC_MISMATCH`），异常消息也直接说明"需点到哪些词" | `adaptive.py`、`core_service.py` |
| 可恢复 | 新增**岗位要求开场**（`opening=REQUIREMENT`）：不调用模型，用冻结的 JD 原句 + 冻结目标名拼题，**仍然过同一个 `validate_decision`**，落库时标 `mode=system_opening` | `core_service.py`、`controller.py`、`router.py` |

前端：422 不再是 alert 死路，而是页内提示 + 两个按钮「重新生成第一题」「改用岗位要求开场」。

### 1.5 恢复验证（真实执行，0 模型调用）

```
POST /interview/sessions/622/start?opening=REQUIREMENT
mode     : system_opening
question : 岗位要求里有一条：「了解Transformer、向量数据库、微调等大模型技术」。
           请结合你做过的一个项目，讲讲你在大模型应用方面的具体做法，
           并说明遇到过一次什么问题、最后怎么解决的。
difficulty: MEDIUM
账本     : 12 → 12（无新增）
```

**你的会话没有被删除，也没有重来一遍。**

### 1.6 关于"重复扣费/重复评价"

- 失败路径不写题目、不写回答、不写评价（已验证 0 行）；
- 因此重试不会产生重复评价，也不会重复结算评价费用；
- 但**每次重试仍会真实调用一次 `interview_main`**（这点无法规避，除非改用 REQUIREMENT 开场）。

---

## 二、P0-2：岗位推荐失真

### 2.1 根因（两个确定性缺陷叠加）

| # | 缺陷 | 证据 |
|---|---|---|
| 1 | 50 份整理版 JD 的 `job_requirements.normalized_value` **252 条全为空**，`evidence_matrix` 于是把**整句要求**当作"字面技术词"去比对（如 `掌握Python/C++/Java之一`），几乎不可能命中 | SQL 计数 `normalized_value not null = 0` |
| 2 | `job_tags` **0 行**，召回 `order_by(overlap desc, job.id)` 的 overlap 全为 0 → **按数据库 id 排序**，途虎（id 小）自然第一 | `select count(*) from job_tags = 0` |

画像本身没有问题：`#20` 的 26 条事实里 Java / Spring Boot / MySQL / Redis / Kafka / ES / Python / FastAPI / LangChain / Milvus / RAG / Agent / Prompt 全部抽到，证据链完整。

### 2.2 修复

| 改动 | 说明 |
|---|---|
| 新增 `job_matching/skills.py` | 用受控技术词表从**要求原句**里抽取技术点（`掌握Python/C++/Java之一` → python/c++/java，任一命中即算满足 OR 条件）；`Java` 不会命中 `JavaScript`；不做任何熟练度推断 |
| `policy.evidence_matrix` | 命中按技术点比对；**必需项与加分项分开统计**；加分项缺失不再压住匹配档位；无可识别技术点的要求（学历/软素质）保持 NOT_APPLICABLE |
| `policy.ranking_components` | 新增 `required_counts`、`preferred_supported`、`evidence_basis`（NONE/PARTIAL/SUPPORTED） |
| `policy.pre_rank_key` | 去掉"id 决定名次"：先比必需项支持数，再比加分项，再比 JD 技术点重合度，最后才是 id |
| `retrieval` | 计算 `topic_overlap`（画像技术点 ∩ JD 技术点），弥补 JobTag 为空 |
| 规则版本 | `literal-evidence-eligibility-v1 → v2`；`JobMatchRun` 复用要求 `policy_version` 相同，**旧缓存不会被错误复用** |

### 2.3 用你真实简历（画像 #20）零模型重算的结果

| 名次 | 企业 / 岗位 | 档位 | 必需项（支持/部分/待确认） | 技术点重合 |
|---|---|---|---|---|
| 1 | 中国电信-天翼云 · 后端开发工程师（JAVA方向） | GOOD_MATCH | 4 / 0 / 0 | 8 |
| 2 | 天猫技术 · AI Agent优化工程师 | GOOD_MATCH | 3 / 1 / 0 | 4 |
| 3 | 沐瞳科技 · 后端开发工程师（IT信息化） | GOOD_MATCH | 3 / 0 / 0 | 10 |
| 4 | 蔚来 · 供应链算法工程师 | GOOD_MATCH | 3 / 0 / 0 | 6 |
| 5 | 麒麟软件 · Python研发工程师 | GOOD_MATCH | 3 / 0 / 0 | 6 |
| 6 | 沐瞳科技 · Agent开发工程师（平台） | GOOD_MATCH | 3 / 0 / 0 | 5 |
| 18 | 麒麟软件 · 测试/测试开发工程师 | GOOD_MATCH | 2 / 1 / 0 | 4 |
| — | 途虎 · 测试开发工程师 | **不在前 20** | — | — |

证据引用示例（第 1 名）：`Python / Java 有相关材料依据`、`Spring Boot 有相关材料依据`、`PostgreSQL / MySQL 有相关材料依据`。

### 2.4 边界（必须说明）

- 前 20 名**都**有 `evidence_basis=SUPPORTED`，因为这份简历确实覆盖 Java/Python/Redis/Kafka/MySQL/RAG/Agent，不是"放水"；
- 若材料里没有任何可对照技术点，`coverage.ranking_confidence=NONE`，页面直接显示**"暂不能可靠排序"**，不再假装精确；
- 旧记录（run #1/#2，policy v1）**保持原样**，仍显示旧顺序——历史不可改写。想看新排序需要在页面上点一次「用当前画像重新匹配」（会调用一次 `job_matching` 模型）。

---

## 三、P1 页面改造

| 页面 | 改造 | 结果 |
|---|---|---|
| 我的画像 | 顶部摘要 + 简历版本；技术栈按 **编程语言 / 后端与框架 / 数据库与缓存 / AI·大模型·检索 / 工程与工具** 分组；教育、项目、实习分节；**每条事实只有一个「来源与修正」**，点开是统一抽屉（原文、offset、页码、修正）；Hash/Prompt/模型收进「高级信息」；同一句重复事实合并显示（数据不删，索引仍指向原条目） | 事实 26 条 → 去重后 15 条展示 |
| 岗位推荐 | 顶部方向 chips + 筛选；默认 6 张卡；每卡 = 为什么推荐 / 还需要确认 / 缺少依据时给具体补全建议 / 硬性条件；排序依据一句话说明；JSON/指纹/召回序号收进「详细依据与高级信息」；「开始面试」带 `company_id/job_id + recommended_skill_id` | 见截图 04 |
| 岗位库（原公共职业资料） | 新增后端 `GET /public-career/job-search`（全库搜索 + 公司/方向筛选 + total）与 `/public-career/companies`；前端搜索框 + 方向 chips + 公司下拉 + 分页；详情抽屉把**岗位职责 / 任职要求分开**，保留来源链接与「招聘信息整理版」标识 | 搜 `Agent` = 27 条，`Java` = 24，`RAG` = 16 |
| 自定义岗位 | 导航改名「自定义岗位」，页面标题改为「自定义岗位（粘贴 JD）」并说明用途；新建面试向导里「找不到岗位？粘贴 JD」直达 | 不再与岗位库混淆 |
| 专项训练 | 只展示 `WEAKNESS / RECHECK` 可执行的候选；无历史时明说「先完成一场完整的模拟面试」；去掉「完整模拟面试」下拉（改为跳转链接）；内部候选列表折叠为「系统推荐的练习顺序（内部依据）」；状态转成「练习改善中 / 疑似缺口 / 建议复测 / 表现存在分歧」 | 见截图 05 |
| 模拟面试 | 左侧**只保留「+ 新建面试」和历史列表**；岗位/方向/侧重/题量/风格/自适应全部搬进 7 步向导（题量预设 3/8/20，自适应有解释与确认行）；「考察侧重」明确说明只有技术基础/项目深挖两种，不假装一面二面 | 见截图 06 |
| 全局 | 导航「公共资料→岗位库」「训练中心→专项训练」；统一卡片/按钮/空状态/错误样式；1024 与 390 宽度无横向溢出 | 见截图 07–09 |

---

## 四、验收证据

### 4.1 浏览器验收（真实 Chrome + CDP，16 项全过）

```
OK 登录 / 岗位库打开 / 搜索 Agent 覆盖全库(27) / 详情区分职责与要求 / 详情标注整理版
OK 画像分组展示 / 画像不再逐条铺开依据 / 来源抽屉可打开
OK 推荐页可打开 / 推荐页解释排序依据 / 推荐页区分匹配与待确认
OK 训练页定位为专项 / 面试页左侧不再堆放配置 / 新建面试向导可打开
OK 响应式 1024px 无横向溢出 / 响应式 390px 无横向溢出
```
截图在 `data/r124_ui/`：`01_career_search_1440` `02_career_detail_drawer` `03_profile_grouped`
`04_match_cards` / `04_match_cards_v2` `05_training` `06_interview_wizard` `07_career_1024` `08_career_390` `09_interview_390`。
记录：`data/r124_ui/ui_acceptance.json`。

### 4.2 测试

| 范围 | 结果 |
|---|---|
| 新增 `test_r124_topic_contract.py` | 7 passed（发布词表必须真的被校验器接受；无证据不得假装） |
| 新增 `test_r124_matching.py` | 7 passed（OR 技能、Agent/Java 高于测试岗、加分项不挡、JS≠Java、版本号升级） |
| 更新 `test_r35_requirement_vocabulary.py` | 契约改为"类别不明但点了技术名要计分；没有技术点才排除" |
| 相关回归 `-k "interview or matching or ..."` | **426 passed / 142 skipped / 0 failed** |
| 前端 `vue-tsc --noEmit` + `vite build` | 通过 |

### 4.3 账本与数据保护

| 项 | 值 |
|---|---|
| 本轮模型 HTTP | **0**（账本 12 行 / 85,130 token，最后一条 14:43:15，均来自你的自测） |
| 新建匹配记录 `#3` | `policy v2`、`status=READY`、**未生成模型解读**（页面对应「安全重试/读取生成结果」） |
| 会话 `#622` | 已恢复为 `IN_PROGRESS`，首题 `system_opening` |
| 你的画像 `#20` / 简历 `#20` | 未修改；未复制简历全文到任何报告 |
| 正式库 | `interview_agent` 0025 / 5 用户，未写 |
| Evaluation 2.3.4 / Coach Harness / L3L4 / 0030 | 未触碰 |

---

## 五、遗留问题

1. **模型偏题仍可能发生**：`mention_terms` 降低概率，但真正保证不卡死的只有「岗位要求开场」。若你希望默认用它，我可以把它做成向导里的显式选项。
2. **匹配解读需要一次模型调用**：我没有替你跑（预算 0）。你在推荐页点一次「用当前画像重新匹配」即可得到带 AI 解读的 v2 结果。
3. **旧推荐记录保持旧顺序**：历史不可改写，这是有意为之。
4. **JobTag 仍为空**：这次用 `topic_overlap` 在应用层补上；要根治应在导入时生成结构化标签。
5. **未做**：把「自定义岗位」完全并入岗位选择页（目前是向导内引导 + 独立页面）；面试页的 `technical_screen/project_deep_dive` 后端枚举未改名，只是前端改叫「考察侧重」。

---

## 六、你再次验收的操作

服务已在运行（80/5173），如需重启：双击 `start-showcase.bat`。

1. 打开 http://127.0.0.1:5173，用 `showcase-r123-user@example.com` / `Showcase123!` 登录。
2. **岗位库**：搜 `Agent`、`Java`、`RAG`，切方向 chips，点卡片看职责/要求分栏与来源链接。
3. **我的画像**：看技术栈分组与去重；点任一「来源与修正」→ 抽屉里的原文与 offset。
4. **岗位推荐**：点「用当前画像重新匹配」（会调用 1 次模型）→ 看新排序、为什么推荐、还需要确认。
   不调用模型也可以看 `#3` 这条：`/jobs/matches/3`（无 AI 解读，仅确定性排序）。
5. **模拟面试**：点「+ 新建面试」→ 按 7 步向导配置（题量 3/8/20、自适应开关）→ 开始。
   若第一题被拒，页面会出现「改用岗位要求开场」，**不删会话**。
6. **专项训练**：没有历史时显示引导；有 R121/R122 的评价时显示可练知识点与来源面试。

完成后停止；本轮未安排后续轮次。
