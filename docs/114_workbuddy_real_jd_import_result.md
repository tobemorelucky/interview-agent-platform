# R31 真实 JD 正式入库试验结果（真实模型，6 次 HTTP）

> 授权范围：3 份官网 JD，正常 6 次 HTTP，硬上限 10 次 / 30,000 token。
> 实际：**6 次 HTTP / 16,921 token / 0 次结构化 Repair**，全部 `SETTLED`，无 UNKNOWN、无配额错误、无归属异常。
> 目标库：`interview_agent_wb_jd_r31_20261116`（`DATABASE_URL` 与 `MODEL_LEDGER_DATABASE` 均指向它，已断言）。
> 模型：`modelscope` / `deepseek-ai/DeepSeek-V4.1-Flash-1017`（两条路由同模型，`governed=true`，无 fallback 路由）。
> 基线提交 `09715ef`。**未 push / 未 reset / 未 clean / 未删除 / 未清库。**

## 结论摘要

| 问题 | 结论 |
|---|---|
| 麒麟软件 AI研发工程师 | ✅ `COMPLETED`，JD 版本 `#1`，`WAITING_REVIEW` |
| 沐瞳科技 Agent开发工程师（平台） | ✅ `COMPLETED`，JD 版本 `#2`，`WAITING_REVIEW` |
| 蔚来 车端AI Agent与应用生态系统架构工程师 | ❌ `FAILED`：`MANUAL_EXTRACTED_IDENTITY_MISMATCH`，**零实体残留**（无 company/job/JD 行） |
| 真实消耗 | **6 HTTP / 16,921 token**（比预估 19.1K 少 11%），0 次 repair |
| Requirement 抽取 | 22 条（麒麟 10、沐瞳 12），每条都有 Claim 与 Evidence，`literal_in_raw_text` 全部命中 |
| 幻觉 | **0 条**：22/22 的 statement 都是原文子串，没有新增事实、没有编造职责 |
| 遗漏 | **0 条**：麒麟 9 条任职资格 → 10 行（1 句拆分）；沐瞳 11 条（8 资格 + 3 加分）→ 12 行（1 句拆分） |
| 错误归类 | **有，2 类**：① 麒麟 5 条「尾句带*优先*」的复合句被整句降为 `PREFERRED`（过度降级，含 Linux/推理引擎这类事实必需项）；② 两岗 `importance`/`requirement_type` 用了两套词表（`REQUIRED`+英文类型 vs `required`+中文类型） |
| Admin 发布条件 | 技术条件**已满足**（22/22 有 Claim、偏移 100% 精确、状态 `WAITING_REVIEW`）；但建议先处理「词表不一致」再发布，否则下游会把沐瞳的学历要求当成技术匹配项 |
| 能否扩到剩余 49 份 | **可以，但要先修两件事**（见 §九），否则每批都会重复踩「身份字面不一致」和「词表漂移」 |

---

## 一、执行与账本

| 序号 | JD | 路线 | 输入 | 输出 | 合计 | 延迟 |
|---|---|---|---|---|---|---|
| 1 | 麒麟 `job_jd` | `public_research_extract` | 1,639 | 1,324 | 2,963 | 7.5s |
| 2 | 麒麟 `quality` | `public_research_quality` | 2,360 | 168 | 2,528 | 3.6s |
| 3 | 蔚来 `job_jd` | `public_research_extract` | 1,811 | 1,456 | 3,267 | 6.3s |
| 4 | 蔚来 `quality` | `public_research_quality` | 2,676 | 104 | 2,780 | 1.7s |
| 5 | 沐瞳 `job_jd` | `public_research_extract` | 1,601 | 1,315 | 2,916 | 6.3s |
| 6 | 沐瞳 `quality` | `public_research_quality` | 2,316 | 151 | 2,467 | 2.3s |

合计 **6 次 / 16,921 token / 27.6s**，`model_admissions` 6 行全部 `SETTLED`，`structured_retry` 全为 0（未触发任何修复，也没有为用满预算而多发请求）。

Prompt 版本：`job_jd@1.0.1`（manual 专用）、`quality@1.0.0`。

---

## 二、三份任务状态

| JD | refresh task | 状态 | JD 版本 | 发布状态 | 实体 |
|---|---|---|---|---|---|
| 麒麟软件 | `#1` | `COMPLETED` | `#1` | `WAITING_REVIEW` | company 1 / job 1 |
| 蔚来 | `#2` | `FAILED` | — | — | 无（回滚） |
| 沐瞳科技 | `#3` | `COMPLETED` | `#2` | `WAITING_REVIEW` | company 2 / job 2 |

### 蔚来失败诊断（重要）

代码路径：`_validate_identity(catalog)` **通过了** → `validate_expected_identity(manifest)` 才抛 `MANUAL_EXTRACTED_IDENTITY_MISMATCH`。
说明：模型抽出的企业名+岗位名**确实逐字出现在页面证据里**（否则会先在 catalog 校验失败），但**与我们 manifest 里登记的标签不完全相等**。

`normalize_text` 的规则是「NFKC → 空白折叠为单个空格 → casefold」，**不删除空格**：

- manifest 标签：`车端AI Agent与应用生态系统架构工程师`
- 页面原文（详情页 h1）：`校招-车端 AI Agent 与应用生态系统架构工程师`

两者差在「空格」与「校招-」前缀。因此只要模型照页面抄，就必然不等于我们的标签。

**这是本轮最值得记录的一条**：验证规则本身是对的（防止抽错岗位），错的是我上一轮把**规范化后的展示名**当成了**页面字面身份**登记进 manifest。

必须如实说明一个限制：**失败时模型返回的 company_name/job_title 没有被持久化**（`experience_agent_step_runs.output_json` 对 `job_jd` 只存用量与状态摘要，不存原始响应），所以我无法 100% 还原它到底选了带空格版还是带「校招-」前缀版。上面的判断是**基于两个候选字面串的高置信推断**，不是直接观测。

---

## 三、Requirement 抽取与分类

### 麒麟软件 AI研发工程师（10 条）

| # | 分类 | 类型 | 要求（原文子串） |
|---|---|---|---|
| 1 | REQUIRED | EDUCATION | 计算机、人工智能、数学、软件工程等相关专业，2027届本/硕/博毕业生均可投递 |
| 2 | REQUIRED | SKILL | 熟练掌握Python，熟悉C/C++/Go/Java等至少一种编程语言 |
| 3 | REQUIRED | SKILL | 熟悉深度学习框架（PyTorch/TensorFlow），掌握Transformer、大模型训练等核心技术原理 |
| 4 | REQUIRED | SKILL | 了解LLM大模型原理及LangChain等主流AI编程框架，了解工具调用、Prompt工程、RAG向量知识库相关技术 |
| 5 | PREFERRED | SKILL | 了解AI计算框架基本原理和算子实现逻辑……有算子开发或推理部署经验者优先 |
| 6 | PREFERRED | SKILL | 熟悉至少一种推理引擎（onnx-runtime、tf-lite、mnn、TensorRT、vLLM、Triton等）……有NVIDIA GPU、Jetson等平台模型部署经验者优先 |
| 7 | PREFERRED | SKILL | 熟悉Linux操作系统……有CUDA开发、高性能计算经验者优先 |
| 8 | PREFERRED | SKILL | 具备良好的数学基础，有大模型微调、智能体开发、端侧部署或安卓AI应用开发经验者优先 |
| 9 | PREFERRED | EXPERIENCE | 有操作系统AI助手、智能体项目落地经验或国产操作系统、飞腾/龙芯等国产硬件平台开发经验者优先 |
| 10 | REQUIRED | SOFT_SKILL | 具备良好的问题分析能力、文档编写能力与跨团队协作意识，自我驱动，学习能力强 |

属性 Claim 9 条：`LOCATION` 北京市,天津市,湖南省·长沙市 / `PUBLISHED_AT` 2026-09-04 发布 / `RESPONSIBILITY` ×7。

分类统计：**REQUIRED 5 / PREFERRED 5 / 其他 0**。

### 沐瞳科技 Agent开发工程师（平台）（12 条）

| # | 分类 | 类型 | 要求 |
|---|---|---|---|
| 1 | required | 学历/专业 | 本科及以上学历，计算机、软件工程、人工智能等相关专业优先 |
| 2 | required | 编程能力 | 具备扎实的编程基础，熟悉 Go、Python 等主流编程语言…… |
| 3 | required | 计算机基础 | 了解数据结构、算法、操作系统、计算机网络、数据库等基础知识……（**跨 2 个证据块**） |
| 4 | required | 后端开发 | 了解服务端开发基础，熟悉 HTTP/gRPC API、数据库、缓存…… |
| 5 | preferred | 后端开发 | 有 Go 服务端开发经验优先 |
| 6 | required | 领域兴趣/经验 | 对大模型、AI Agent、开发者工具和智能工作流有浓厚兴趣…… |
| 7 | required | AI/Agent 技术知识 | 了解 Prompt Engineering、LLM API、RAG、Function Calling / Tool Use…… |
| 8 | required | 通用能力 | 具备较强的学习能力、问题拆解能力和动手能力…… |
| 9 | required | 软技能 | 具备良好的沟通协作能力…… |
| 10 | preferred | AI/Agent 技术知识 | 了解 Agent SDK、LangChain、AutoGen、MCP、RAG……者优先 |
| 11 | preferred | AI/Agent 工程实践 | 了解 Agent 调试、运行追踪、效果评测、日志分析、Token 成本分析……者优先 |
| 12 | preferred | 领域理解 | 对 AI Native 产品、人机协作方式、Agent 工作台形态……有自己的思考或实践 |

属性 Claim 8 条：`LOCATION` 上海 / `RESPONSIBILITY` ×7。

分类统计（按模型给出的小写值）：**required 9 / preferred 3 / 其他 0**；按 `"REQUIRED"` 精确匹配统计则为 **0 / 0 / 12**（见 §六 词表问题）。

---

## 四、Evidence 逐字核验（不只 offset_exact）

对 22 条 Requirement 逐条做了四层校验：

| 校验 | 麒麟 | 沐瞳 |
|---|---|---|
| 有 `claim_id` | 10/10 | 12/12 |
| `claim_evidence` 行数 ≥1 | 10/10 | 12/12 |
| `content_text[start:end] == excerpt` 且快照 sha256 一致 | 10/10 | 12/12 |
| statement 是「引用证据拼接后」的子串 | 10/10 | 12/12 |
| statement 是「JD 全文」的子串 | 10/10 | 12/12 |

补充：`raw_text` 与快照正文**逐字相等**（2/2），`content_fingerprint` 由快照正文计算，不取模型片段。

**跨块拼接**：沐瞳第 3 条横跨两个 600 字证据块（`ev_099ffa31…` → `ev_abc49cb3…`），`EvidenceCatalog.supports_literal` 的相邻块拼接逻辑生效，逐字校验通过。Quality 模型把这件事报为风险（「JD 文本在证据间被截断」）——**属保守提示，实测不是错误**。

---

## 五、Quality 评估

| JD | `suitable` | 门禁结论 | 风险条数 | 主要风险（模型原话摘要） |
|---|---|---|---|---|
| 麒麟 | `true` | `WAITING_REVIEW`（`quality_risk_requires_review`） | 5 | 职责覆盖过广可能多团队合并招聘；多项资格为「优先」；2027 届跨度大；部分职责跨度大；单一来源无交叉验证 |
| 沐瞳 | `true` | `WAITING_REVIEW`（`quality_risk_requires_review`） | 4 | 校招非社招；证据间文本截断需核对；岗位归属「研发-大数据」与职责聚焦 Agent 平台不一致；多项「了解/优先」 |

门禁之所以是 `WAITING_REVIEW` 而非 `APPROVED`：`job_publication_gate` 里 `risk_flags` 非空即进入人工复核。这符合「不因官方域名绕过审核」的要求——`public_research_official_domains` **本轮未修改**（仍为 `{}`），也没有任何代码路径被绕开。

值得肯定的是：两条风险提示（「多项为优先」与「证据间截断」）都指向了真实存在的边界情况，模型没有凭空编造风险。

---

## 六、幻觉 / 遗漏 / 错误归类

**幻觉：0。** 22 条 statement 全部能在 JD 原文里找到逐字片段；7+7 条职责、2 条地点、1 条发布日期也都是原文子串。没有出现「JD 里没有的技术栈」「被拔高的职级」「编造的薪资/团队规模」。

**遗漏：0（按行覆盖）。** 麒麟 9 条任职资格 → 10 行（资格第 2 条拆成「编程语言」+「深度学习框架」两行）；沐瞳 11 条（8 资格 + 3 加分）→ 12 行（资格第 4 条拆出「Go 服务端经验优先」）。拆分都是忠实切分，没有增删语义。

**错误归类：2 类，都是真问题。**

1. **「尾部优先」污染整句判定**（麒麟 5 条，id 5–9）。
   `熟悉Linux操作系统，了解系统API、进程权限机制，了解计算机体系结构与异构计算基础，有CUDA开发、高性能计算经验者优先`
   → 模型把**整条**判为 `PREFERRED`。但「熟悉 Linux / 熟悉系统 API」是硬性的，只有最后半句是加分。同理第 5、6、8、9 条。
   影响：强制要求会被少算 3–5 条；如果后续按 `REQUIRED` 生成面试重点，Linux 与推理引擎这类核心项会被漏掉。

2. **词表漂移（跨 JD 不一致）**。
   - 麒麟：`REQUIRED/PREFERRED` + `SKILL/EDUCATION/EXPERIENCE/SOFT_SKILL`
   - 沐瞳：`required/preferred` + `学历/专业`、`编程能力`、`AI/Agent 技术知识`……
   `schemas.py` 里 `requirement_type: str`、`importance: str` 都只是自由文本（≤64/32 字符），没有枚举约束，所以两套都合法通过。
   下游后果是具体的：`job_matching/policy.py:76` 用 `norm(requirement_type) in {"education","major",...}` 判断「硬资格条件不计作技术适配」。`norm` 只做 casefold，所以麒麟的 `EDUCATION` **能**命中，沐瞳的 `学历/专业` **不能**命中——沐瞳的学历要求会被当成技术匹配项来打分。

**另有一条流程性缺陷（非模型问题）**：失败时模型返回的身份值不留痕，导致复盘只能推断（见 §二）。

---

## 七、JD 版本与状态

| JD 版本 | 企业/岗位 | 版本号 | 发布状态 | company/job 状态 | 审计日志 |
|---|---|---|---|---|---|
| `#1` | 麒麟软件 / AI研发工程师 | v1 | `WAITING_REVIEW` | `WAITING_REVIEW` / `WAITING_REVIEW` | 0（未发布） |
| `#2` | 沐瞳科技 / Agent开发工程师（平台） | v1 | `WAITING_REVIEW` | `WAITING_REVIEW` / `WAITING_REVIEW` | 0（未发布） |

幂等已验证：对已完成的麒麟任务再次 `prepare` → `reused=true`、`status=COMPLETED`、`refresh_task_id` 仍是 `#1`，**没有新建任务、没有新建版本**。全库 `job_description_versions` 仍为 2 行。

---

## 八、Admin 审核发布条件

我按 `PublicCareerReviewService.publish_jd` 的检查项做了**只读预演**（未发布、未写审计）：

| 检查 | 麒麟#1 | 沐瞳#2 |
|---|---|---|
| 状态 ∈ {WAITING_REVIEW, APPROVED} | ✅ | ✅ |
| company/job 未关闭 | ✅ | ✅ |
| 每条 Requirement 有 Claim | ✅ 10/10 | ✅ 12/12 |
| 身份与属性 Claim 齐全 | ✅ | ✅ |
| 每条 ClaimEvidence 偏移精确 + 快照 hash 一致 | ✅ | ✅ |
| **结论** | **可发布** | **可发布** |

但**我建议不要直接发布沐瞳**：`importance` 是 `required/preferred`、`requirement_type` 是中文自由文本，发布后 `job_matching` 会把「本科及以上学历」当成技术适配项去比对，产生一条假的「技术缺口」。麒麟的 `EDUCATION` 能正常被排除，没有这个问题。

---

## 九、能否扩展到剩余 49 份

可以，但**必须先解决两件事**，否则 49 份会按比例重复踩坑。

**必解 1：身份标签必须与页面字面一致。**
本轮 1/3 因这个原因失败（33%）。修法在数据侧，不改代码：manifest 的 `company`/`job_title` 直接取页面 `h1`/标题行的字面串（含空格、含「校招-」「【2027届秋招】」前缀）。或者更稳的做法是——**从页面标题行自动生成 manifest 标签**，而不是人工写展示名。

**必解 2：`importance` / `requirement_type` 需要受控词表。**
两条路（都属核心改动，本轮未做）：
- 在 `job_jd` prompt 里显式限定取值域（改 Prompt = 改冻结产物，需单独批准）；
- 或在 `_publish_job` 落库前做一次确定性归一（改核心逻辑，需单独批准）。
在新词表落地前，建议：**验收时按「大小写不敏感 + 中文类型白名单」自行归一后再统计**，不要直接按 `"REQUIRED"` 过滤。

**规模与预算外推**（按本轮实测 **5,640 token/份、2 HTTP/份**，即 16,921 ÷ 3）：

| 批次 | JD 数 | HTTP | Token 估算 |
|---|---|---|---|
| 已试点 | 3（2 成功） | 6 | 16.9K |
| 剩余官方 | 45 | 90 | ≈254K |
| 4 份策展（单独标注） | 4 | 8 | ≈23K |
| 合计剩余 | 49 | 98 | ≈276K |

按每次 10 份分批：每批 20 HTTP / ≈56K token。

---

## 十、沙箱数据 vs 生产数据（必须分清）

| 口径 | 数量 | 说明 |
|---|---|---|
| **生产库** `interview_agent` 的 `job_description_versions` | **4**（未变） | 移动嘉兴/工行广东/移动湖北/华为，均 `PUBLISHED` |
| **生产库**其他基线 | 5 users / 38 resumes / 19 sessions / 41 evaluations / 5 reports / 0 model_admissions | 全部未变 |
| **本轮沙箱** `interview_agent_wb_jd_r31_20261116` 真实模型产出 | **2 份 JD 版本**（麒麟#1、沐瞳#2），`WAITING_REVIEW` | `companies=2 / jobs=2 / job_requirements=22 / claims=41 / claim_evidence=43 / tasks=3 / audit_logs=0 / model_admissions=6` |
| 预演库 `..._replay2_...` | 3 份 REPLAY 版本 | **不计入**（规则抽取 fixture，非模型产物，库已写 `COMMENT`） |
| 失败任务 | 1（蔚来 #2） | 零实体残留 |

**沙箱这 2 份不是生产增长**。生产库仍是 4 份；等 Admin 审核发布后，也只会在沙箱内变成 `PUBLISHED`，离"进生产"还差一次明确的迁移决定。

---

## 十一、资产与 Git

| 项 | 状态 |
|---|---|
| 生产库 / 历史 Live / 备份 | 未触碰 |
| 库总数 | 249（本轮未新增库） |
| MinIO | 生产桶 `interview-agent` 48 对象；隔离桶 `interview-agent-r31-jd` 0 对象 |
| Redis | 沙箱 `db 7` 0 key；`db 1/10/13` 未触碰 |
| Evaluation Prompt | 2.3.4 / `e7704357b5fc37ba` 未改 |
| `public_research_official_domains` | 仍为 `{}`（未改配置，未绕过审核） |
| 删除/归档/清库/push/reset/clean | 均无 |

产物：`data/r34_target_jd/real_run/{real_run_report.json, real_run_moonton.json, audit.json, capture_replay.json}`。`capture_replay.json` 为脱敏包：只含公开 JD 文本、账本行、步骤元数据与结构化抽取，**不含 API Key、认证头、用户数据、原始响应体**。

---

## 十二、待你决定

1. **蔚来这一份**：是否批准 **+2 HTTP**（累计 8/10）用「与页面字面一致的身份标签」重跑同一任务（`force=True`，复用 task #2，不新建任务）。不重跑则保持 `FAILED`，该 JD 不计入。
2. **沐瞳词表**：发布前是否先做归一（需要你单独批准改 Prompt 或改核心逻辑），还是**把沐瞳暂缓发布、只发布麒麟**。
3. **Admin 发布**：等你单独批准后我再执行 `publish_jd`（0 模型调用），发布后前端 `GET /interviews/targets` 才会出现这两个岗位。

在收到批准前不再发起任何模型请求，不发布、不启动个人面试、不继续 L3/L4。
