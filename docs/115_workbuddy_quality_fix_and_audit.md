# R32 质量修复与蔚来有限重试结果

> 本轮真实模型 **HTTP = 0**（新增 0 次 / 0 token）。原累计 6 次 / 16,921 token 保持不变，仍在上限 8 次 / 30,000 token 之内。
> 生产库、历史 Live、备份、Evaluation 2.3.4、Coach、Memory 全部未变；未发布、未删除、未清库、未 push/reset/clean。
> 基线提交 `619a237`。

## 结论摘要

| 项 | 结果 |
|---|---|
| 蔚来是否通过重试 | ❌ **未执行重试**。零 HTTP 核验发现重试在现行代码下**不可能成功**：`MANUAL_IMPORT_IDENTITY_CONFLICT` 挡住了修正后的身份 |
| 蔚来身份结论 | 模型抽的身份**没错**（通过证据校验），错的是我登记在 manifest 的标签；修正后标签可通过现有严格校验（已 Replay 验证） |
| 三份 JD 最终状态 | 麒麟 `COMPLETED`/`WAITING_REVIEW`；沐瞳 `COMPLETED`/`WAITING_REVIEW`；蔚来 `FAILED`（零实体） |
| 词表归一化 | ✅ 已实现受控枚举 + 中英同义映射，未知/歧义值**不再默认当技术技能**；Schema 与 Matching 双端生效；`62 passed` |
| 尾部「优先」污染 | ✅ 已修复在消费边界：麒麟 4 条混合句的**实际强度由 PREFERRED 纠正为 REQUIRED**（存储值不动、原文与证据不动） |
| 发布条件 | 麒麟、沐瞳均**无阻断项**，具备 Admin 审核发布条件；蔚来无版本，不可发布 |
| 49 份共性阻断 | 仍有 1 个硬阻断（身份标签写法）与 1 个治理项（歧义类型需人工映射） |

---

## 一、蔚来：为什么没有花那 2 次 HTTP

### 1.1 官方字面身份（DOM 实证）

| 项 | 值 |
|---|---|
| `<title>` | `校招-车端 AI Agent 与应用生态系统架构工程师 - 蔚来校招` |
| `data-test="jobTitle"` 元素 | `校招-车端 AI Agent 与应用生态系统架构工程师` |
| 正文内独立职位 ID | `A25338A`（「职位 ID：A25338A」） |
| 企业名 | `蔚来`（DOM 中出现 28 次） |
| manifest 登记 | `车端AI Agent与应用生态系统架构工程师` |

### 1.2 差异分类（区分「官方字面」与「展示名」）

| 候选 | 在快照中字面存在 | 等于页面元素 | 等于 manifest | 与页面比只剩排版差异 | 实质名称相同 |
|---|---|---|---|---|---|
| `车端AI Agent与应用生态系统架构工程师`（manifest 现值） | 否（无空格版不连续） | 否 | 是 | 是 | 是 |
| `校招-车端 AI Agent 与应用生态系统架构工程师`（页面原样） | 是 | **是** | 否 | — | 是 |
| `车端 AI Agent 与应用生态系统架构工程师`（去渠道前缀、保留排版） | 是 | 否 | 否 | 否 | **是** |

判定：
- 「校招-」是**渠道/批次前缀**，与麒麟的「「2027校招」」、沐瞳的「【2027届秋招】」同类，不是岗位名的一部分；
- 空格是页面排版，属**展示差异**，不构成实质性岗位名称差异；
- 去掉前缀后两者**实质相同**（`strip_markers` 后相等），没有把「车端 AI Agent」偷换成别的岗位。

### 1.3 严格校验的两道关（用真函数跑）

| 候选 | 证据身份校验 `EvidenceCatalog.validate_identity` | 期望身份校验 `validate_expected_identity`（对旧 manifest） |
|---|---|---|
| manifest 现值 | 失败（该串不是快照连续子串） | 通过 |
| 页面原样（带前缀） | **通过** | 失败 `MANUAL_EXTRACTED_IDENTITY_MISMATCH` |
| 去前缀保排版 | **通过** | 失败 `MANUAL_EXTRACTED_IDENTITY_MISMATCH` |

这解释了 R31 的失败顺序：**证据关过了**（所以模型没抽错岗位），挂在「与操作者登记标签逐字相等」这一关。

修正映射后再验：`{"job_title": "车端 AI Agent 与应用生态系统架构工程师"}` → `expected_identity = PASS`。

### 1.4 重试被什么挡住（关键发现）

用修正标签重新 `ManualImportService.prepare(...)`：

```
ManualImportError: MANUAL_IMPORT_IDENTITY_CONFLICT
```

原因在代码里是明确的：
- 幂等键 `manual:sha256(canonical_url + content_hash + document_type)` **不包含身份**；
- 同一份正文必然命中同一个 task；
- `prepare` 发现既有 task 后，会逐项比对 `company / job_title`，一旦不同就抛 `IDENTITY_CONFLICT`；
- 而 `run(force=True)` 用的是 task 里**冻结的旧 manifest**，再跑一次仍然拿旧标签做期望值。

**结论：在现行代码下，「修正身份标签后重试同一份 JD」这条路径不存在。** 这不是模型问题，是流程缺口。

`force=True` 重试虽然合法（task #2 状态 `FAILED`，manual manifest 完整），但它只会用旧标签再赌一次模型的自由发挥——这属于「为了用完预算发请求」，按你的规则不发。

### 1.5 失败任务无残留

| 检查 | 结果 |
|---|---|
| task #2 状态 | `FAILED`，错误 `MANUAL_EXTRACTED_IDENTITY_MISMATCH` |
| 快照完整性 | 1,500 字，`sha256` 与 `content_hash` 一致 |
| 蔚来相关实体 | `companies=0 / jobs=0 / jd_versions=0` |
| 库内总计 | `companies=2 / jobs=2 / jd_versions=2`（仅麒麟、沐瞳） |

### 1.6 需要你决策（二选一）

- **方案 A（推荐，代码 1 处）**：把 `validate_expected_identity` 的比较改为「忽略空白」——与它旁边的 `EvidenceCatalog.validate_identity` 保持一致（后者本来就 `''.join(normalize_text(x).split())`）。这样 task #2 不需要重建，`force=True` 即可重试（2 HTTP）。**不改证据校验**，只让「期望值一致性」不再被排版打败。
- **方案 B（数据 1 处，但碰库）**：把 task #2 的 manifest `job_title` 改成页面字面。需要直接改 `public_data_refresh_tasks.reason` JSON，属于你本轮明令禁止的「直接修改数据库中的任务状态」。
- **方案 C**：接受失败，等批量导入时统一用「页面字面标签」重建任务。

我不擅自执行 A（改核心逻辑）或 B（改库）。**本轮 2 次 HTTP 一次未用。**

---

## 二、三份 JD 最终 Research 状态

| JD | task | 状态 | JD 版本 | 发布状态 | 备注 |
|---|---|---|---|---|---|
| 麒麟软件 AI研发工程师 | `#1` | `COMPLETED` | `#1` v1 | `WAITING_REVIEW` | 证据 10/10、偏移 100% |
| 沐瞳科技 Agent开发工程师（平台） | `#3` | `COMPLETED` | `#2` v1 | `WAITING_REVIEW` | 证据 12/12、偏移 100% |
| 蔚来 车端AI Agent… | `#2` | `FAILED` | — | — | 零实体；身份映射已定位 |

真实消耗未变：**6 HTTP / 16,921 token / 0 repair**。

---

## 三、词表归一化：具体修复与测试

### 3.1 检查发现（Schema 与 Matching 契约）

- `research/schemas.py`：`requirement_type: str`、`importance: str` 都是**自由文本**（≤64/≤32 字符），无枚举、无校验 → 两套口径都合法。
- `job_matching/policy.py:76`：`norm(requirement_type) in {"education","major","graduation_year","location","eligibility"}`。`norm` 只做 casefold，于是 `EDUCATION` 命中、`学历/专业` 不命中，被当成**技术适配项**去打分。
- `interview/context.py:152`：把 `importance` 原样塞进面试快照，"required" 这种小写值会一路传到出题 Prompt。

### 3.2 修复（全部在结构化校验与业务消费边界，未扩写 Prompt）

新增 `apps/api/src/interview_api/modules/public_career/requirement_vocabulary.py`：

| 能力 | 说明 |
|---|---|
| `RequirementType` 枚举 | 13 个受控值：EDUCATION / MAJOR / GRADUATION_YEAR / LOCATION / ELIGIBILITY / SKILL / EXPERIENCE / SOFT_SKILL / CERTIFICATE / LANGUAGE / PORTFOLIO / OTHER / UNKNOWN |
| `RequirementStrength` 枚举 | REQUIRED / PREFERRED / UNKNOWN |
| `HARD_ELIGIBILITY` | 学历/专业/毕业年份/地点/资格 → 永远不进技术适配 |
| `normalize_requirement_type()` | 显式同义词表（中英各一套）+ 三态结论 `KNOWN / AMBIGUOUS / UNKNOWN` |
| `can_be_technical` | **只有明确且非硬资格**的类型才可为 True；UNKNOWN 一律 False |
| `normalize_strength()` | required/必须/强制 → REQUIRED；preferred/加分/优先 → PREFERRED；其余 → UNKNOWN |

接入点：

| 位置 | 改动 | 效果 |
|---|---|---|
| `research/schemas.py` | `RequirementExtraction` 两个 `field_validator` | 新入库即规范化；未知标签落 `UNKNOWN`，绝不升级为 SKILL |
| `job_matching/policy.py` | 用 `normalize_requirement_type` 判定 | 硬资格 → `NOT_APPLICABLE`；未知/歧义 → `NOT_APPLICABLE` + 「需人工确认」，**不再计作技术缺口** |
| `interview/context.py` | 公共 JD 分支用 `effective_strength` | 出题看到的强度与实际语义一致 |
| `public_career/library.py` | 公开岗位详情用归一后的 `type` / `effective_strength` | 页面展示不再把混合句写成"可选" |
| `job_matching/retrieval.py` | 候选项的 `importance` 用 `effective_strength` | 匹配响应口径统一 |

**兼容性**：模型 JSON Schema 未变（仍是 4 个属性、`additionalProperties: false`），Prompt 未改；历史已发布 JD 的 `structured_data_json` **一字未动**（规范化只在新入库与读取时发生）。

### 3.3 测试

新增 `apps/api/tests/test_r35_requirement_vocabulary.py` —— **62 passed**：

| 分组 | 覆盖 |
|---|---|
| 同义词映射 | 21 组中英标签 → 单一枚举 |
| 未知拒绝 | `""`/`None`/`有意思的标签`/`SKILL/EXPERIENCE`/`bonus` → UNKNOWN，`can_be_technical=False` |
| 歧义标记 | `学历/专业`、`education/major` → AMBIGUOUS + 硬资格 + 不可技术 |
| 强度映射 | 8 组中英 |
| Matching 消费 | 学历类（6 种写法）→ 硬资格消息；未知类（4 种）→ 需人工确认；技能类（4 种）→ 仍进技术对齐 |
| Schema 兼容 | 中文/大小写入库即归一；未映射标签保持 UNKNOWN 且不当技能 |

回归：`test_m6_job_matching` / `test_m2b_research_agent` / `test_public_career_publication` / `test_manual_public_career_import` / `test_m2b_models_evidence` / `test_r34_target_jd_contract` / `test_interview_topic_policy` / `test_context_projection_target_refs` / `test_job_target` —— 三次运行分别为 **152 / 41 / 119，全部 0 failed**（含跨批重复执行的用例，未去重）。

---

## 四、混合强制/优先的处理结果

### 4.1 能力

`split_strength_clauses()` 只识别**强度标记**（优先 / 者优先 / 优先考虑 / 同等条件下… / preferred / is a plus），**不含任何技术关键词**，因此不是为单个样本写的硬编码规则。

| 场景 | 行为 |
|---|---|
| 混合句（麒麟 Linux 句） | 拆成 3 条 REQUIRED + 1 条 PREFERRED；`effective_strength` = **REQUIRED** |
| 整句优先（麒麟第 9 条） | 保持 PREFERRED |
| 条件性（同等条件下…优先） | 合并状语后判 PREFERRED，不会被误拆成硬性 |
| 纯硬性句 | 保留记录值 |
| `；` 分隔的混合句 | 同样可拆 |

### 4.2 麒麟实测（真实数据）

| # | 语句（截断） | 存储强度 | 实际强度 | 判定 |
|---|---|---|---|---|
| 1 | 计算机、人工智能…2027届本/硕/博均可投递 | REQUIRED | REQUIRED | — |
| 2 | 熟练掌握Python… | REQUIRED | REQUIRED | — |
| 3 | 熟悉深度学习框架… | REQUIRED | REQUIRED | — |
| 4 | 了解LLM原理及LangChain… | REQUIRED | REQUIRED | — |
| 5 | 了解AI计算框架…有算子开发经验者优先 | PREFERRED | **REQUIRED** | 混合句纠正 |
| 6 | 熟悉至少一种推理引擎…有部署经验者优先 | PREFERRED | **REQUIRED** | 混合句纠正 |
| 7 | 熟悉Linux操作系统…有CUDA经验者优先 | PREFERRED | **REQUIRED** | 混合句纠正 |
| 8 | 具备良好数学基础…有微调/端侧经验者优先 | PREFERRED | **REQUIRED** | 混合句纠正 |
| 9 | 有操作系统AI助手…国产平台经验者优先 | PREFERRED | PREFERRED | 整句优先，正确 |
| 10 | 具备良好问题分析/文档/协作能力 | REQUIRED | REQUIRED | — |

统计：存储 **5 REQUIRED / 5 PREFERRED** → 实际 **9 REQUIRED / 1 PREFERRED**。

沐瞳第 11 条「本科及以上学历，计算机…等相关专业优先」同样是混合句（学历硬、专业优先），实际强度取 REQUIRED，与「学历是门槛」的语义一致。

### 4.3 没有做的事（如实）

- **没有改写存储值**：`job_requirements.importance` 仍是模型原值（麒麟 PREFERRED、沐瞳 required）。这是有意的——模型输出是审计证据，不能为了让报表好看去覆盖。
- **没有生成独立 Requirement 行**：把一条混合句拆成多条独立 `job_requirements` 需要新写「派生需求」服务（新建 Claim/Evidence 行 + 审计留痕）。本轮**未做**，因为那会写入非模型产生的证据行，属于需要你单独批准的写操作。当前用消费边界投影已达到「分类与匹配含义一致」。
- **没有为此调用模型**：0 次 HTTP。

---

## 五、每份 JD 仍存在的语义问题

| JD | 遗留问题 | 影响 | 性质 |
|---|---|---|---|
| 麒麟 #1 | 4 条混合句的**存储**强度仍是 PREFERRED | 任何绕过本项目消费层、直接读 `job_requirements.importance` 的外部系统会看到错误强度 | 需「派生需求」服务或评审时接受投影 |
| 麒麟 #1 | 第 5 条「了解AI计算框架基本原理…」前半句其实也是「了解」级，不是「熟悉/掌握」级 | 强度细节可再细分（了解 vs 熟悉 vs 掌握），当前只分硬/优 | 精度问题，不影响发布 |
| 沐瞳 #2 | `requirement_type` 存储是中文自由文本（10 种） | 本项目已归一；外部消费者需同表映射 | 兼容性问题 |
| 沐瞳 #2 | 第 11 条类型 `学历/专业` 被判 AMBIGUOUS | Matching 会跳过它（不误算技术缺口），但也不会校验它 | 需评审时确认归 EDUCATION 还是 MAJOR |
| 沐瞳 #2 | 第 17 条要求横跨两个证据块 | 已验逐字连续，但跨块仍是可读性风险 | 无害 |
| 蔚来 | 无 JD 版本 | 不参与任何下游 | 阻断 |

---

## 六、三份分别是否具备 Admin 发布条件

按 `publish_jd` 的前置条件逐项只读复核（**未执行发布**，`audit_logs = 0`）：

| 检查项 | 麒麟 #1 | 沐瞳 #2 | 蔚来 |
|---|---|---|---|
| 官方来源与岗位身份 | ✅ 麒麟官网 `jobAdId=0b8de09c…`，身份 Claim 有证据 | ✅ 沐瞳官网 `position/7668253932858362118` | ❌ 无版本 |
| 正文与 SHA-256 | ✅ 1,186 字，`raw_text` 与快照逐字相等 | ✅ 1,140 字，逐字相等 | — |
| Requirement 完整性 | ✅ 10/10 有 Claim | ✅ 12/12 有 Claim | — |
| 强度 | ⚠️ 存储 5/5，实际 9/1（已投影纠正） | ✅ 实际 8/4 | — |
| EDUCATION 归一化 | ✅ `EDUCATION` | ⚠️ `学历/专业` → AMBIGUOUS（不误算技术缺口） | — |
| Claim/Evidence 逐字 | ✅ 10/10 偏移精确、字面命中 | ✅ 12/12 | — |
| Skill 推荐 | ✅ 预览 `RECOMMENDED → ai_agent_application` | ✅ 同 | — |
| 质量风险标记 | 5 条（职责过广/多项优先/届别跨度/职责跨度/单一来源） | 4 条（校招非社招/证据截断/团队定位/门槛未体现） | — |
| 重复导入与版本 | ✅ 幂等复用 task #1，无 v2 | ✅ 单版本 | — |
| **阻断项** | **无** | **无** | 不适用 |
| **结论** | **可发布**（建议发布时在理由中记录 4 条强度投影） | **可发布**（建议先确认 `学历/专业` 归类） | **不可发布** |

注：Skill 推荐在发布前为空是设计使然——`recommend_jobs` 只读 `Job.status='ACTIVE'` 且 JD `PUBLISHED`。审核用的预览值是用同一规则直接算的（只读，不写）。

---

## 七、49 份批量导入前的共性阻断

| # | 阻断 | 证据 | 修法 | 是否需改代码 |
|---|---|---|---|---|
| 1 | **身份标签必须是页面字面** | 3 份里 1 份因此失败（33%）。蔚来页面标题带「校招-」前缀与空格，manifest 写的是展示名 | 生成 manifest 时直接取 `data-test="jobTitle"` / 页面 h1 的字面串，不做人工美化 | 否（数据侧） |
| 2 | **修正标签后无法重试** | `MANUAL_IMPORT_IDENTITY_CONFLICT`（本轮实证） | 见 §1.6 方案 A | 是（1 行） |
| 3 | **类型歧义需人工映射** | 沐瞳 10 种中文类型，1 条歧义 | 已归一为受控枚举；歧义值进审核清单 | 否（已完成） |
| 4 | **混合句强度** | 麒麟 4/10 受影响（40%） | 已在消费边界修复；若要写入存储需派生需求服务 | 否（已完成投影） |
| 5 | 失败留痕不足 | 身份值不落库，复盘只能推断 | 失败时把抽取到的身份写进 `ExperienceAgentRun.output_json` | 是 |

预算外推：剩余 49 份 ≈ **98 HTTP / ≈276K token**（按实测 5,640 token/份）。若先修 #1+#2，失败率可从 33% 降到接近 0。

---

## 八、Git 与受保护资产对账

| 项 | 状态 |
|---|---|
| 生产库 `interview_agent` | 5 users / 38 resumes / 19 sessions / 41 evaluations / 5 reports / **4 JD 版本** —— 未变 |
| 沙箱 `interview_agent_wb_jd_r31_20261116` | 2 companies / 2 jobs / **2 JD 版本** / 0 audit_logs / 6 admissions（本轮未新增调用） |
| 预演库 `..._replay2_...` | 3 份 REPLAY 版本，不计入 |
| 库总数 | **249**（本轮未新增库） |
| MinIO | 生产桶 48 对象；`interview-agent-r31-jd` 0 对象 |
| Redis | db7 空；db1/10/13 未触碰 |
| Evaluation Prompt | 2.3.4 / `e7704357b5fc37ba` 未改 |
| `public_research_official_domains` | 仍为 `{}` |
| 删除/归档/清库/生产迁移/push/reset/clean | 均无 |
| 本轮新增代码 | `requirement_vocabulary.py`、5 处接入、3 个脚本、2 个测试文件 |

## 九、等你决定

1. **蔚来**：选 §1.6 的 A / C（A 需批准 1 行核心改动，之后 2 HTTP 重试）。不选则维持 FAILED。
2. **发布**：麒麟、沐瞳是否批准 Admin 发布（我仍不会自动发布）。
3. **派生需求服务**：是否要我把麒麟 4 条混合句落成独立 Requirement 行（需新建服务 + 审计，0 模型）。
4. **批量导入**：是否批准先修「身份标签生成」与「修正后可重试」两处，再开 49 份。

在收到批准前不再发起模型请求、不发布、不扩展批量、不启动个人面试。
