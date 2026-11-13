# 第十九轮：简历经历类型、JD 要求强度与 Skill 推荐标题偏置的根因与修复边界

> 本轮是**有界质量修复**，不新增 Agent、不新增 Harness、不重构推荐系统。
> **真实模型 HTTP = 0**。所有模型侧结论都来自第十八轮已归档的真实响应 + Replay/Mock。
>
> **样本量声明**：真实模型样本仍只有第十八轮的 2 份合成简历（A、B）、1 份真实归档公开 JD（C）、
> 1 份合成对照 JD（D）。本轮新增的都是**合成压力样本**，它们证明"修复是否按规则生效"，
> **不能证明"真实模型抽取质量提升"**——没有新的真实调用，就没有新的模型质量数据。下文每个结论都标注了样本来源。

---

## 1. 结论摘要

| 问题 | 根因层级 | 本轮状态 | 依据 |
|---|---|---|---|
| 全职工作被写进实习 | **第一阶段 Schema/提示词只有实习桶**（不是模型"分类错"） | 已修（服务端按原文小节裁决 + Schema 补齐 + 画像侧复核） | 第十八轮真实响应 A 重放：1 条 `internships` → `work_experience` |
| JD 要求强度几乎全 UNKNOWN | 提示词只给关键词，未给**章节语义**；RULE 同样 | 已修（逐字证据定位章节 + 措辞裁决 + `basis` 说明来源） | C：4 条中 1 条 REQUIRED；D：5 条中 1 条 PREFERRED，其余标 `LISTED_UNDER_REQUIREMENTS` |
| 标题压过要求文本 | 标题 8 分 > 单条要求 6 分，标题+任一泛化能力组即过 9 分门槛 | 部分修复：不再**自动选择**冲突候选，改为要用户手选；分值权重未按样本调参 | D：`ai_agent_application` 20 分仍第一，但状态 AMBIGUOUS（`TITLE_CONFLICTS_WITH_REQUIREMENTS`） |
| "向量数据库"命中"数据库" | 跨 Skill 长术语未覆盖短术语 | 已修（已注册的长术语覆盖同片段短术语） | `bank_it` 由 3 分降为 0 分 |

**四项中三项属"已修"，第三项属"已阻止错误自动决策、未解决排序本身"** —— 报告必须这样区分，否则会把"不再自动选错"说成"排序变准了"。

---

## 2. 问题一：全职工作经历被写成实习

### 2.1 真实样本与失败现象

第十八轮真实响应（`.runtime/r18/capture/worker/01-resume_extract.json`，样本 A）：

```
work_experience : (字段不存在)
internships     : [{"company": "某互联网公司", "role": "Python 后端开发工程师", ...}]
```

而简历 A 的原文在 **`工作经历`** 小节下写着这段全职经历（2022.07–2025.06），`实习经历` 小节并不存在。
最终画像页也会把它显示在"实习"分组里，因为投影按类别名走。

### 2.2 逐层定位（结论：第一层就错了，后面只是照抄）

| 层 | 检查结果 | 是否本轮缺陷点 |
|---|---|---|
| ① 原始简历切分 | 切分正确：`工作经历` 是独立整行标题，其下内容归属 `work` | 否 |
| ② 第一阶段模型抽取 | **Prompt 的 JSON 示例里只列了 `internships`，没有 `work_experience` 键**；模型把全职经历放进唯一可用的桶 | **是（主因）** |
| ③ Schema 解析 | `ResumeSummary` 同样没有 `work_experience` 字段，多出的键被丢弃 → **桶名不等于雇佣类型的错误无法被发现** | **是（共因）** |
| ④ 证据归属校验 | 画像侧当时只校验"证据是否存在"，**不校验证据所在小节是否与该类别一致** | **是（共因）** |
| ⑤ 最终 CandidateProfile 投影 | 按类别名直接落库/展示 | 否（只是放大 ①–④ 的错） |

换句话说：这不是"模型判断力不足"，而是**我们把唯一能表达全职的字段删掉了，再让模型在剩下的桶里选**。
把它当模型问题去"加强提示词约束"会修不好——必须先补字段，再让服务端按原文裁决。

### 2.3 修复内容与准确边界

**(a) 补齐第一阶段结构**（`apps/api/prompt_templates/resume_parse_v1.md`、`modules/resume/schemas.py`）

- 新增 `work_experience`，并明确写出：**桶名不是雇佣类型；类型由简历原文所在小节决定**。
- 明确禁止用公司名、时长、"参与项目"等词推断全职/实习。
- 无法确定时写入 `unclassified_experience`，不硬塞进两个桶之一。

**(b) 服务端裁决**（新增 `modules/resume/sections.py`、`modules/resume/employment.py`，在 `processor.py` 落库前调用）

- `sections.py`：标题识别只认**整行**标题（去装饰符后完全匹配别名），所以句子里出现"工作经历"四个字不会开小节；
  另加"目录块守卫"（连续两个标题视为目录，不切换小节）与"未识别标题闭合"（`其他经历`、`校园活动` 这类短标题行会结束上一小节，
  否则学生会活动会继承到"工作经历"里）。
- `employment.py`：把每条经历的公司/岗位/时间/职责/技术栈当作锚点，回到原文找到锚点所在的**非标题行**，
  只有锚点在"工作"或"实习"**恰好一个小节**出现时才投一票（公司 3 分、岗位 2 分、其余 2/1 分）；
  锚点在两个小节都出现则不投票（无区分力）。得分高者胜；平票记 `CONFLICT`；全无票记 `UNRESOLVED`。
- `CONFLICT`/`UNRESOLVED` 一律进 `unclassified_experience`，**原文一字不改、不丢条目**，并在报告里留审计块
  `employment_kind={policy, counts, entries[], note}`。

**(c) 画像侧复核**（`modules/candidate_profile/evidence.py::normalize_employment_categories`）

- 事实的类别只由**它引用的逐字证据所在小节**决定：全部指向 `work` → 计入工作；全部指向 `internship` → 计入实习。
- 类别与证据不一致 → 服务端**移动**它（保留 label、statement、证据链接，逐字不改）。
- 证据指向两个小节 / 只来自项目小节 / 简历有小节但这事实不属于任何雇佣小节 → **丢弃该事实**，并追加一条"待确认"话题到 `unknowns`。
- 简历完全没有小节标题时无从核对 → **保留**并按 `UNVERIFIED_NO_EMPLOYMENT_HEADING` 标注，不假装已核实。

**(d) 面试侧**：`interview/prompt_builder.py` 原来只读 `internships` 并把标题写成"实习/工作经历"；
现在分别读 `work_experience`、`internships`、`unclassified_experience`，不再把全职工作介绍成实习。

**边界（明确没做的事）**：
- 不改历史画像。修复只对**后续新抽取**生效；已生成的 `candidate_profile_versions` 不重算、不改写。
- 不引入"实习生"字样的关键词规则去兜底（那会变成新的硬编码），只认小节结构。
- 不改 `CandidateProfile` 的类别字段语义，`work_experience` / `internship_experience` 仍是原来的两条。

### 2.4 第十八轮失败样本的前后差异

用**原始真实响应**重放（不重新调用模型）：

| 项目 | 修复前 | 修复后 |
|---|---|---|
| `work_experience` | `[]` | 1 条：某互联网公司 / Python 后端开发工程师 |
| `internships` | 1 条（全职） | `[]` |
| `unclassified_experience` | 无 | 无（有区分力，无需降级） |
| 审计 | 无 | `counts={WORK:1, INTERNSHIP:0, CONFLICT:0, UNRESOLVED:0}`，`matched_anchors=11`，最长锚点 59 字，`sections=["work"]` |

11 个锚点全部落在 `work` 小节，胜出无歧义——这也说明**服务端有足够证据可以自证**，不需要相信模型的桶名。

### 2.5 工作/实习分类规则（最终形态）

1. 只认原文**小节**；公司名、任职时长、"参与项目"等措辞**不作为**雇佣类型证据。
2. 小节标题必须是整行命中别名（`工作经历/工作经验/work experience/employment` 与 `实习经历/实习经验/internships/...`）。
3. 单条经历用多锚点投票，锚点在两个雇佣小节都出现则弃权。
4. 冲突或无法判定 → 保留原文到 `unclassified_experience` + 生成"待确认"话题，**不猜**。
5. 同一简历同时含全职与实习时分别落两个桶（已用样本验证，见 §5）。

---

## 3. 问题二：JD 要求强度（REQUIRED / PREFERRED / UNKNOWN）

### 3.1 先核对业务语义（未改字段含义）

沿用既有三值，不新增状态：

- `REQUIRED` = **原文自己写了硬性门槛**（必须 / 须 / 至少 / 及以上 / 硬性）。
- `PREFERRED` = **原文自己写了优先或加分**（优先 / 加分 / 更佳 / 最好）。
- `UNKNOWN` = 原文没有把强度写出来（含"列在任职要求里但只陈述能力"、"只在职责里出现"）。

第十八轮的实际缺陷不是"REQUIRED 太少"，而是**分类完全不反映原文**：C 的"至少一种编程语言"明明是硬门槛，却被判 UNKNOWN；
同时"列在岗位要求下"的普通资格也没有任何标注，用户无法区分"企业说的门槛"和"企业只是列了一下"。

### 3.2 根因

1. Prompt 只给了关键词清单（必须/须/至少/要求/硬性），**没有告诉模型"章节"也是语义**，模型于是只在句子里找词；
   而真实 JD 的门槛句常写成"具备扎实的编程能力，熟练掌握 … 至少一种"，关键词只命中"至少"，模型仍给 UNKNOWN。
2. RULE 抽取同样只按关键词，且**把工作地点与城市列表当成资格条件**（第十八轮 C 多出 2 条地点噪声）。
3. 没有记录"为什么是这个强度"，用户无法复核，也就无法纠正。

### 3.3 修复内容

**(a) 服务端按"逐字证据所在章节 + 措辞"裁决**（`modules/job_target/service.py`）

新增 `jd_section_of(raw, evidence)`：从粘贴原文里定位该逐字证据所在小节，返回
`REQUIREMENT`（任职要求/岗位要求/任职资格/技能要求…）、`DUTY`（岗位职责/工作内容/职责描述…）或 `None`（无小节）。

`classify_requirement(statement, section)` 的判定顺序：

| 条件 | importance | basis |
|---|---|---|
| 句内有"优先/加分/更佳/最好" | `PREFERRED` | `EXPLICIT_PREFERRED` |
| 句内有"必须/须/至少/及以上/硬性"且**不在职责小节** | `REQUIRED` | `EXPLICIT_MANDATORY` |
| 位于任职要求小节 | `UNKNOWN` | `LISTED_UNDER_REQUIREMENTS` |
| 位于职责小节 | `UNKNOWN` | `DUTY_SECTION` |
| 无小节 | `UNKNOWN` | `NOT_STATED` |
| 用户手动新增/修改 | 用户所选值 | `USER_PROVIDED` |

关键设计：**"列在任职要求里"不会被升级成 REQUIRED**。企业把一条能力列在要求章节，不等于它写明了硬性门槛；
这种情况下保留 UNKNOWN 并标注来源，比虚构一个企业从未写过的硬限制更诚实。

**(b) `basis` 只存在 `requirements_json`（JSONB）内，不新增数据库列、不加迁移**。
前端在性质下拉框下以灰字显示 `basisLabels` 说明，用户看得见"为什么这样判"。

**(c) RULE 抽取同步**：同一文本走同一 `classify_requirement`，并新增 `location_only()` 过滤
"工作地点/工作城市/base 地"与纯城市列表（`北京/西安/南京/…`），但**不误杀**专业列表
（`人工智能、计算机、软件工程…`）与"接受出差，工作地点北京/上海"这类真实条件。

**(d) 用户编辑语义不变**：未改动的行保留原 `evidence`/`topic`/`source`/`importance`/`basis`；
被改写的行按用户声明处理并标 `USER_PROVIDED`。已确认的历史 JD 版本不重新分类。

### 3.4 第十八轮真实抽取结果的前后差异

C（真实华为 AI JD，4 条）：

| # | 语句（截断） | 修复前 | 修复后 |
|---|---|---|---|
| 1 | 人工智能、计算机、软件工程…相关专业 | UNKNOWN | UNKNOWN / LISTED_UNDER_REQUIREMENTS |
| 2 | 熟悉主流大模型应用开发框架… | UNKNOWN | UNKNOWN / LISTED_UNDER_REQUIREMENTS |
| 3 | 或有大模型…开发经验 | UNKNOWN | UNKNOWN / LISTED_UNDER_REQUIREMENTS |
| 4 | 具备扎实的编程能力…**至少一种**编程语言 | UNKNOWN | **REQUIRED** / EXPLICIT_MANDATORY |

D（合成 Java 变体，5 条）：前 4 条由 UNKNOWN → `UNKNOWN / LISTED_UNDER_REQUIREMENTS`；
第 5 条"有调用大模型接口的经验者优先"由 UNKNOWN → **PREFERRED / EXPLICIT_PREFERRED**。

RULE 路径（同一文本、零调用）：C 3 条（1 REQUIRED、2 LISTED），D 4 条（1 PREFERRED、3 LISTED），
**地点/城市列表 0 条**（第十八轮 RULE 为 C 6 条 / D 6 条，其中含 2 条地点噪声）。

> RULE 比 LLM 少一条不是漏判：C 的第 2、3 条在原文里是**同一行、分号连接的两句**，LLM 会拆成两行，
> RULE 按行不拆。两半都是原文逐字子串，所以都能通过逐字校验。这是切分差异，不是强度判定差异。

### 3.5 JD 强度语义（最终形态）

1. 强度来自**原文措辞 + 所在章节**，不来自关键词表，也不来自"这条看起来重要"。
2. 只有原文明确写了强制，才允许 REQUIRED；写在要求章节但没有强制措辞 → UNKNOWN + `LISTED_UNDER_REQUIREMENTS`。
3. 职责描述一律不进资格判定（即使句中有"熟悉/掌握"）。
4. 地点与城市列表是 offer 条件，不是资格条件，两条路径都不计入要求。
5. 每条仍必须有逐字证据；没有逐字证据的行（模型编的）直接丢弃并计数。

---

## 4. 问题三：Skill 推荐的标题偏置与术语子串

### 4.1 现有评分的真实组成（未改权重数值）

`policy_version = 1.2.0`，`WEIGHTS = {category:10, title:8, responsibility:6, capability_group:3}`，
`MIN_SCORE=9`，`MIN_MARGIN=3`，本轮新增 `MIN_CONTENT=6`。

排序 = `(excluded, -ranking_points, skill_id)`，`ranking_points` 是**整数规则权重之和**，
不是概率、不是百分比、不是模型置信度（这句话同时写进了接口的 `RANKING_SEMANTICS` 与前端文案）。

第十八轮 D 的实测（修复前）：

```
ai_agent_application 20 = title 8 + responsibility 6 + capability_group 3 + 3
java_backend         15 = responsibility 6 + capability_group 3 + 3 + 3
```

**问题不是"标题加分"，而是标题 8 分 > 单条要求 6 分**：只要标题命中，再叠任一泛化能力组就过 9 分门槛并自动选中。
要求文本再多，也追不平这 8 分。

### 4.2 修复内容

**(a) 拆分分值并设内容门槛**：每条候选暴露 `content_points = ranking_points - title_points`。
自动选择要求 `ranking_points ≥ 9` **且 `content_points ≥ 6`** —— 只有标题（8 分）或标题 + 一个泛化词（11 分但内容 3 分）
不再构成自动推荐。

**(b) 内容领先者与分值领先者冲突 → 拒绝自动选择**：若分值第一的内容分低于另一条合格候选，
状态置 `AMBIGUOUS`，`auto_select=False`，`decision.ambiguity_reason = TITLE_CONFLICTS_WITH_REQUIREMENTS`，
理由文案直说："岗位名称指向 X，但已写明的岗位要求与职责更多指向 Y；标题不能代替要求文本，系统不替你决定，请手动选择。"

**(c) 只有标题对得上**：`TITLE_ONLY_LEADER`，同样不自动选。

**(d) 术语边界**：`occurrences()` 计算字符区间；若某短术语的命中区间**被另一个 Skill 拥有的更长已注册术语完全覆盖**，
该命中不计分。"向量数据库"属于 AI/Agent 规则，因此 `bank_it` 的"数据库"不再命中；若长术语属于同一 Skill（上位词），则不抑制。

**(e) 理由与原文证据分开**：`matched_reason` 是规则解释（"岗位的 X、Y 与该专业规则相符"），
`evidence[]` 才是 JD 逐字证据（带 `evidence_status=VERBATIM_FROM_JD` 与 `requirement_id`）。
前端分两段显示："规则理由（算法匹配，不是招聘原文）"与"JD 逐字证据"。

**未做**：没有为了 D 把 `title` 权重从 8 调到 3，也没有加"Java 关键词加权"。那是针对样本调参，下一份 JD 就会再次失真。

### 4.3 控制变量实测（A–F，全部合成，零调用）

| 组 | 构造 | 结果 |
|---|---|---|
| A | 同一标题，要求换成明显不同的技术方向 | Top-1 随要求变化，不再随标题固定 |
| B | 同一套要求，只换标题 | 合格候选与内容分不变；标题只影响 title 分 |
| C | 标题与要求方向一致 | `RECOMMENDED`，自动选中 |
| D | 标题指向 AI，要求指向 Java | `AMBIGUOUS` / `TITLE_CONFLICTS_WITH_REQUIREMENTS`，**不自动选** |
| E | "向量数据库" vs "数据库" | `bank_it` 3 → 0 分；长术语归属迁移正确 |
| F | 只有标题，没有足够要求 | `INSUFFICIENT_DATA`（无要求/职责）或 `NO_APPLICABLE_SKILL`，不猜 |

第十八轮 C/D 的真实重放（同一份归档响应 + 不同标题）：

| 样本 | 标题 | 状态 | 首推 | 说明 |
|---|---|---|---|---|
| C | AI应用工程师 | RECOMMENDED | ai_agent_application (20) | 标题与要求一致，内容 12 分过门槛 |
| D | AI应用工程师 | **AMBIGUOUS** | 无 | 标题 20 vs 内容领先 java_backend 15；拒绝自动选 |
| D | Java 后端工程师 | RECOMMENDED | java_backend (15) | 标题与要求一致时排序反转 |

**诚实结论**：D 在"AI 标题"下**分值排序仍是 AI 第一**，我们做的是"不替你决定 + 说明冲突"，不是"让 Java 排第一"。
如果目标是让要求文本在所有情况下压倒标题，需要重新设计打分（例如按命中条目数加权），那是下一轮的设计题，不是本轮的有界修复。

### 4.4 仍会误判的范围（必须写清）

- 长术语覆盖只在**该长术语已被某个 Skill 注册**时生效。若文本出现"图数据库""时序数据库"而注册表里没有这些术语，
  "数据库"仍会命中 `bank_it` —— 这是子串匹配的固有边界，不是本轮遗漏。
- 同义改写（"向量检索" vs "向量数据库"、"大模型" vs "LLM"）不做语义归一。
- 标题仍是 8 分。若一份 JD 的要求文本极短（1 行职责），标题依旧可能领先，此时系统返回 `AMBIGUOUS` 而不是给错答案——
  **代价是用户要多点一次**，这是有意的取舍。

---

## 5. 合成压力样本与回归（不含任何伪造的成功数据）

新增三个后端测试文件，共 **38 条**：

**`tests/test_employment_kind.py`（18 条）**

- 第十八轮真实响应 A 不再把全职判成实习；
- 仅全职 / 仅实习 / 全职+实习同时出现（分桶正确）；
- 项目小节内容不能变成工作经历；
- 泛化小节（`其他经历`）与完全无标题的简历不猜；
- 模型桶名与明确小节冲突 → 移动到正确桶；
- 目录块不劫持后续小节；同公司出现在两个小节时按有区分力的证据判定；
- 归并不改动入参、不丢条目；空原文不重排；
- 画像侧：引用工作小节的事实被移动到工作、只引用项目小节的事实被丢弃、
  同时引用两个雇佣小节被丢弃并生成待确认话题、无小节简历保留并标注未核实、移动时 label/statement 逐字不变。

**`tests/test_requirement_strength.py`（10 条）**

覆盖任务书要求的六种情形：`必须熟悉 Python` → REQUIRED；`Python 开发经验` 在任职要求章节 → UNKNOWN/LISTED；
`熟悉 Python 优先` → PREFERRED；`负责 Python 服务开发` 在职责章节 → UNKNOWN/DUTY；
学历/年限/语言未写出 → 不生成；地点与城市列表 → 丢弃且不误杀专业列表与出差条件。
另有：章节定位用逐字证据而不是标题顺序；模型行被重新裁决、不可逐字引用的行被丢弃；模型无法把城市列表存成要求；
用户行保留自己的强度并被标 `USER_PROVIDED`；同一文本上 RULE 与 LLM 判定一致。

**`tests/test_skill_recommendation_controls.py`（10 条）**

A–F 六组控制变量 + 分值是整数规则分（不是置信度）+ 只能推荐已注册 Skill + 长术语只抑制**其他** Skill 的短术语 +
冲突投影要求人工选择且理由与逐字证据分离。

前端新增 `apps/web/tests/interviewEntry.test.ts`（7 条，第十八轮遗留的入口竞态纯函数）本轮保持通过。

---

## 6. 前端与真实浏览器结果

真实 Chrome 157 headless + CDP 9222，独立 E2E 库 `interview_agent_wb_r19c_e2e_20261113`（迁移到 0027），
API + Celery worker + Vite 全部真实启动，页面操作真实点击。

| 检查项 | 结果 | 证据 |
|---|---|---|
| 全职工作显示在"工作"，实习显示在"实习" | 通过 | `r19_01c`：工作 = 2022.07-2025.06 DEMO 云计算公司 后端开发工程师 + 2 条职责事实，依据链接为"工作经历"；实习 = 2021.07-2021.09 DEMO 电商公司 后端开发实习生，依据为"实习经历" |
| 模糊经历不被强行标成实习 | 通过 | "其他经历"（开源社区文档贡献）未进入任一雇佣分组 |
| JD 任职条件/优先/未知可区分 | 通过 | `r19_02`、`r19_08`：性质下拉下显示灰字说明（"列在任职要求里，原文没有写必须"/"原文写明优先或加分"） |
| 推荐展示证据与规则理由 | 通过 | `r19_03`：规则理由与 JD 逐字证据分两段 |
| 修改确认要求后能重新取推荐 | 通过 | `r19_05`：编辑后重新确认生成第 2 版，推荐重取，不再沿用旧输入 |
| 低确定性可手动选择 | 通过 | `r19_07`：状态 AMBIGUOUS，文案说明标题与要求冲突，无"系统首推"标记，可手选 |
| 旧 Session / 旧 JD 仍能打开 | 通过 | `r19_06`：会话仍冻结在原 JD 版本 |

未做整体视觉改版，只加了 `basis` 灰字说明与推荐区块的两段式标签。

---

## 7. 测试结果

独立数据库（全部 `interview_agent_wb_r19*`），遵守第十七轮的启动前目标校验；无生产库连接。

| 项目 | 通过 | 失败 | 跳过 |
|---|---|---|---|
| 后端业务全量（`--database interview_agent_wb_r19_full3_test_20261113`） | **928** | 0 | 1 |
| 迁移专项（`interview_agent_v21_wb_r19_mig_20261113`） | **10** | 0 | 0 |
| 本轮新增长项（38 条，含在上述全量内） | 38 | 0 | 0 |
| 前端单元测试（`node --experimental-strip-types --test`） | 7 | 0 | 0 |
| 前端类型检查 + 生产构建（`vue-tsc && vite build`） | 通过 | — | — |

跳过的 1 条为既有环境相关用例（与第十八轮相同），未新增跳过。

**真实模型 HTTP = 0**，全部模型交互走 Replay/Mock（E2E 库画像版本标记为"Replay 回放模式，不是 Live 模型验收"）。

---

## 8. 安全与不回归的边界

- **没有新增迁移**：`basis` 存在 `requirements_json`（JSONB）内，`alembic` 仍为 **0027**；生产库 `interview_agent` 仍为 **0025**，未升级、未写入。
- **历史数据未改**：既有 `candidate_profile_versions`、已确认 JD 版本、已开始 Session 的冻结 JD/Skill 全部不动；
  修复只对新的简历抽取与新解析的 JD 生效。
- **不通过放宽校验让测试变绿**：逐字证据、章节核对、Schema、权限校验都未放宽；两条既有测试的期望值是因为**行为被有意改变**
  （"必须熟悉分布式系统"现在会被判 REQUIRED、画像提示词版本升到 1.0.5）而更新，并在提交里写明了原因。
- **权限与版本冻结**：跨账号 404、已确认目标不可重解析、编辑不覆盖冻结版本等既有约束未触碰，相关测试全绿。

---

## 9. 尚需真实模型验证的具体问题（下一轮真实回归的清单）

1. **模型是否会正确使用 `work_experience`**：本轮改的是 Prompt 与 Schema，但真实 HTTP=0，模型的实际输出未验证。
   服务端裁决能兜住"桶放错"，兜不住"模型干脆不输出该条经历"。
2. **REQUIRED 是否回到合理水平**：C 的 4 条里 1 条 REQUIRED 是文本事实，但真实 JD 的写法差异很大，
   需要至少 3–5 份新 JD 才能判断分类分布是否忠实；目前只有 2 份。
3. **模型侧标题偏置是否与离线判断一致**：第十八轮"原标题压过要求"的现象在离线重放里仍存在（只是不再自动选择），
   需确认这不是归档响应里 `job_title` 提取缺失（第十八轮曾出现 `job_title=null`）造成的假象。
4. **无小节/弱结构简历**：本轮对"完全无标题"的处理是保留并标未核实；真实简历里这种形态不少，
   需要真实样本确认"保留"不会变成"把学生活动当工作"。
5. **中英混排与英文简历**：`work experience` / `internship` 别名已支持，但未用真实英文简历验证。
6. **长 JD 截断**：RULE 限 `requirements[:30]`、职责 `[:20]`，超长 JD 会截断；真实长 JD 是否丢关键要求未验证。
7. **未分类经历的可见性**：`unclassified_experience` 会进入面试 Prompt，但**前端没有单独的"未分类经历"区块**，
   用户看不到被降级的条目，只能从画像的"待确认"话题间接感知。这是本轮已知的产品缺口，未修。

---

## 10. 附：本轮资源与改动清单

- 新增源码：`modules/resume/sections.py`、`modules/resume/employment.py`
- 修改源码：`resume/processor.py`、`resume/schemas.py`、`prompt_templates/resume_parse_v1.md`、
  `candidate_profile/evidence.py`、`candidate_profile/agent.py`、`candidate_profile/prompts.py`（1.0.5）、
  `interview/prompt_builder.py`、`interview/skill_recommendation.py`、`job_target/service.py`
- 修改前端：`pages/JobTargetPage.vue`、`pages/InterviewChatPage.vue`、`types/job-target.ts`、`types/interview.ts`
- 新增测试：`tests/test_employment_kind.py`、`tests/test_requirement_strength.py`、`tests/test_skill_recommendation_controls.py`
- 测试库：`interview_agent_wb_r19_test_20261113`、`_full3_`、`_mig_`、`_e2e_`(a/b/c)
- 截图：`.runtime/ui/shots/r19_01c`、`r19_02`、`r19_03`、`r19_04`、`r19_05`、`r19_06`、`r19_07`、`r19_08`
