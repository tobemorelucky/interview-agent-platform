# R33 真实 JD 审核发布与批量导入前置修复

基线 `2146dcd`。本轮**真实模型 HTTP = 0**：没有新增任何模型调用，沙箱账本仍是 R31 的 6 行 / 16,921 token。

本轮做了四件事：把麒麟与沐瞳在沙箱里走完 Admin 审核发布并逐项验收；为蔚来的身份问题设计了显式修订任务并用 Replay 跑通八种情形；对剩余 49 份真实 JD 做零 HTTP 身份与结构预检；给出下一批真实调用预算。

---

## 一、结论先说

| 问题 | 结果 |
|---|---|
| 麒麟是否成功 Admin 发布 | **是**，`job_description_versions#1` → `PUBLISHED`，`published_at 2026-11-16T10:38:27Z` |
| 沐瞳是否成功 Admin 发布 | **是**，`#2` → `PUBLISHED`，`10:38:27Z` |
| 发布后接口是否正常 | `GET /api/v1/interview/targets` 200 返回 2 个岗位；`GET /api/v1/public-career/jobs` 200 返回 2 条；Skill 均 `RECOMMENDED → ai_agent_application` |
| 蔚来 | **保持 FAILED**，未发请求。已给出显式身份修订任务的最小实现 + 8 项 Replay 验证，等批准后才跑 |
| 剩余 49 份预检 | **PASS 13 / NEEDS_REVIEW 35 / REJECT 1** |
| 批量导入共性阻断 | 3 条（身份标签须取页面字面、详情页 URL、无小节标题），其中 2 条可零成本确定性解决 |
| 下一批 5 份预算 | **10 HTTP / ≈28.2K token**，硬上限 **14 HTTP / 40K** |
| 生产库 | 未变：5 users / 38 简历 / 19 场 / 41 评价 / 5 报告 / **4 份 JD** / 0 账本 |

---

## 二、发布前必须核对的五条读取路径

发布之所以能通过，不是"看起来没报错"，而是把要求强度的每条正式读取路径都查了一遍：

| 路径 | 入口 | 用什么强度 | 结果 |
|---|---|---|---|
| 岗位选择列表 | `GET /api/v1/interview/targets` → `recommend_jobs` | 只取 `id + statement`，不读 importance | 无强度泄漏 |
| Skill 推荐 | `interview/skill_recommendation.py` | 全模块无 `importance` 引用 | 无强度泄漏 |
| Job Matching | `job_matching/retrieval.py:93` | `effective_strength(r.statement, r.importance)` | 已归一 |
| Interview Context | `interview/context.py` → 新增 `project_requirements()` | ORM 分支 `effective_strength`；用户目标分支 `consumer_strength` | 已归一 |
| 前端岗位详情 | `public_career/library.py:55` | `describe(...)["effective_strength"]` / `["type"]` | 已归一 |

**本轮补掉的最后一条**：`project_requirements()` 里的用户目标分支原来仍把 `requirements_json` 的原始 `importance` 直接抄进冻结快照，与公共 JD 分支口径不一致。补了 `consumer_strength()`：只允许两种结果——

- `PREFERRED` + 混合句 → `REQUIRED`（强制分句仍然成立）
- 其余一律不动，**`UNKNOWN` 不会被提升为 `REQUIRED`**（"只列在任职要求里"不等于硬性门槛，这是 R31 就定下的语义）

同一段文本的三种行为已验证：混合句 → REQUIRED；`本科及以上学历`（UNKNOWN）→ UNKNOWN；`持有PMP证书者优先`（全句优先）→ PREFERRED。

---

## 三、麒麟：4 条混合句的原文依据

存储口径 **5 REQUIRED / 5 PREFERRED**，下游口径 **9 REQUIRED / 1 PREFERRED**。差异全部来自这 4 条，每条的分句都能在正文里逐字指认：

| # | 判为强制（原文分句） | 判为优先（原文分句） |
|---|---|---|
| 5 | 了解AI计算框架基本原理和算子实现逻辑；了解CPU/GPU硬件架构 | 有算子开发或推理部署经验者优先 |
| 6 | 熟悉至少一种推理引擎（onnx-runtime、tf-lite、mnn、TensorRT、vLLM、Triton等）；了解模型部署流程 | 有NVIDIA GPU、Jetson等平台模型部署经验者优先 |
| 7 | 熟悉Linux操作系统；了解系统API、进程权限机制；了解计算机体系结构与异构计算基础 | 有CUDA开发、高性能计算经验者优先 |
| 8 | 具备良好的数学基础 | 有大模型微调、智能体开发、端侧部署或安卓AI应用开发经验者优先 |

第 9 条 `有操作系统AI助手…经验者优先` 是全句优先，**保持 PREFERRED**。

三条自我约束都守住了：

1. **没有把整句改成 REQUIRED 落库**——`job_requirements.importance` 一个字节没动，5/5 仍是 5/5；
2. **没有生成派生 Requirement 行**（那需要新服务写非模型产生的证据行，你已明确暂不批准）；
3. **加分项仍然可见**——下游拿到的是"REQUIRED + 完整原句"，`者优先` 四个字还在句子里，模型读得到。

这里有一个必须讲清的代价：如果哪个外部系统**直接读 `job_requirements.importance`**，它看到的是 5/5，仍然会低估 4 条强制要求。本项目的消费层已修正，但这条存储事实不能假装不存在。

---

## 四、沐瞳：学历/专业的确认与剩余瑕疵

- 类型 `学历/专业` 在三态判定里是 **AMBIGUOUS**。按原文核对：`本科及以上学历，计算机、软件工程、人工智能等相关专业优先` —— 学历是硬资格，专业是加分。归一为 `EDUCATION`（硬资格）成立，因此**允许发布**，并把确认写进了审核理由。
- 效果可验证：Matching 里该条状态 **`NOT_APPLICABLE`（"硬资格条件独立处理，不计作技术适配证据"）**，其余 11 条才是技术对齐（无画像 → `NO_EVIDENCE`，未凭空判缺口）。
- 该条本身也是混合句：`学历 REQUIRED + 专业优先 PREFERRED`，下游判 REQUIRED，正确。
- **一处未修的瑕疵**：第 12 条 `Python 等主流编程语言` 比原文 `Python等` 多一个空格。属空白差异，逐字证据（Evidence excerpt）是精确的，不影响语义，已记为 advisory 而非阻断。
- **risk_flags 一条没清**：两份 JD 的 `quality` 复核 `risk_flags` 非空触发人工复核这一事实保留在 `metadata_json` 里。

---

## 五、发布后十项验收

| # | 检查 | 证据 | 结果 |
|---|---|---|---|
| 1 | 状态 PUBLISHED | 2/2 版本，`published_at 10:38:27Z` | ✅ |
| 2 | Admin 审计日志 | `audit_logs#1/#2`，`actor_role=ADMIN`、`action=public_career.jd.publish`、`status=SUCCESS`、`before_json.publication_status=WAITING_REVIEW` | ✅ |
| 3 | GET /interviews/targets | HTTP **200**，返回麒麟 + 沐瞳 | ✅ |
| 4 | 前端展示 | `GET /api/v1/public-career/jobs` 200 / 2 条；详情要求强度取 `effective_strength` | ✅ |
| 5 | Skill 依据 | 两份均 `RECOMMENDED → ai_agent_application`，理由文本分别为"AI研发、RAG、AI应用…相符"/"RAG…相符" | ✅ |
| 6 | Matching 分类 | 麒麟 9R/1P，沐瞳 8R/4P；学历条 `NOT_APPLICABLE` | ✅ |
| 7 | Interview Context | `project_requirements` 对真实行投影：10 条 → 9R/1P，12 条 → 8R/4P，指纹全唯一 | ✅（见下方限制） |
| 8 | 重复 publish | 第二次调用 `reused=true`，`jd_version_id` 不变，版本数仍 2 | ✅ |
| 9 | 未审核不可选 | 审计 `before_json` 保留 `WAITING_REVIEW`；发布前 dry-run 实测可选岗位 **0** | ✅ |
| 10 | 仅在沙箱 | 生产库 4 份 JD 未变；沙箱才 2 份 | ✅ |

**第 7 项的限制要说清楚**：`build_context()` 先解析候选人画像（`PROFILE_REQUIRED` 409）再读 JD，所以没有画像就走不到 JD 分支。我没有为了凑一张"完整快照"去伪造合成简历/画像——那正好是你反复拒绝的那类数据。替代做法是把 JD→快照的投影抽成 `project_requirements()`，用**真实数据库行**跑同一段代码。这条比"启动一个假会话"更可信，因为它验的是投影逻辑本身。

---

## 六、蔚来：为什么不重试，以及最小修订方案

**官方字面**：`校招-车端 AI Agent 与应用生态系统架构工程师`（DOM `data-test="jobTitle"`；正文另有职位 ID `A25338A`）
**R31 冻结的 manifest**：`车端AI Agent与应用生态系统架构工程师`

两者差两处：`校招-` 渠道前缀 + 空格。证据校验能过（模型没抽错），挂在 `validate_expected_identity` 的逐字相等；而修正标签后重新 `prepare` 会抛 `MANUAL_IMPORT_IDENTITY_CONFLICT`——幂等键 `sha256(url+content_hash+doc_type)` 不含身份，旧 task 里又冻结了旧 manifest。**一行"忽略空格"的改法解决不了前缀差异，也不是你批准的方向，所以没做。**

### 方案：显式身份修订任务（已实现最小版，未跑真实请求）

新增 `ManualImportService.prepare_identity_revision(document, *, predecessor_task_id, reason, authorized_by)`，**不改旧任务、不改 URL、不改正文 Hash**，而是新建一个可追溯的修订任务：

1. 前驱必须存在且状态为 **FAILED**（否则 `PREDECESSOR_NOT_FAILED`）；
2. `canonical_url`、`content_sha256`、`document_type` 必须与前驱**完全相同**（否则 `SOURCE_CHANGED`）——用造假变化绕幂等键的路直接堵死；
3. 公司名必须相同（`COMPANY_CHANGED`）；
4. 新标签必须与前驱不同（`LABEL_UNCHANGED`）；
5. 新标签必须**在快照正文里出现**（`LABEL_NOT_IN_SNAPSHOT`）——只能是页面自己写的；
6. 新标签在忽略空白后必须**仍包含旧标签**（`LABEL_UNRELATED`）——允许加渠道前缀，不允许换成另一个岗位；
7. 新任务 key = `sha256(前驱ID + url + content_hash + doc_type + 新标签)`，**确定性**，重复提交返回同一个任务；
8. manifest 里写 `identity_revision{of_task_id, of_job_title, reason, authorized_by, authorized_at, previous_error, same_canonical_url, same_content_sha256, label_occurs_in_snapshot}`，并写一条 `audit_logs`（`public_career.jd.identity_revision`）；
9. 前驱的 `status=FAILED`、`error_message`、冻结 manifest **原样保留**。

没有 schema 变更（`idempotency_key` 是字符串，结构化信息进 manifest JSON），也没有改动任何既有调用方——新方法目前**零调用点**。

### Replay 验证（全新库 `interview_agent_wb_jd_replay3_r33_20261116`，0 模型）

| 用例 | 期望 | 实测 |
|---|---|---|
| 复现原失败（manifest=展示名，抽取=页面字面） | `MANUAL_EXTRACTED_IDENTITY_MISMATCH` | ✅ 一致 |
| 用页面字面创建修订 | `READY_FOR_EXTRACTION`，`revision_of=1` | ✅ task#2 |
| 修订任务跑真实 Agent（回放） | `COMPLETED`，生成 JD v1 / 15 条要求 | ✅ |
| 重复提交同一修订 | 同一 task，`reused=true` | ✅ |
| 错误公司 | `MANUAL_REVISION_COMPANY_CHANGED` | ✅ |
| 另一个岗位的标题 | `MANUAL_REVISION_LABEL_NOT_IN_SNAPSHOT` | ✅ |
| 过短/歧义标签（去掉"车端"） | `MANUAL_REVISION_LABEL_UNRELATED` | ✅ |
| 标签未变 | `MANUAL_REVISION_LABEL_UNCHANGED` | ✅ |
| 前驱已 COMPLETED | `MANUAL_REVISION_PREDECESSOR_NOT_FAILED` | ✅ |
| 前驱是否被动过 | 仍 `FAILED`、manifest 未变、无 revision 字段 | ✅ |

**真任务未受影响**：`interview_agent_wb_jd_r31_20261116` 的 task#2 仍是 `FAILED / MANUAL_EXTRACTED_IDENTITY_MISMATCH`，快照 1,500 字哈希一致，蔚来实体 0。

---

## 七、剩余 49 份真实 JD 的零 HTTP 预检

脚本 `apps/api/scripts/r37_jd_precheck_49.py`，只读本地正文 + 渲染 DOM，0 网络 0 数据库写。

### 分组

| 结论 | 数量 | 含义 |
|---|---|---|
| **PASS** | **13** | 身份可按页面字面建立、有独立岗位 ID、正文与 Hash 一致、有职责与要求、无污染无重复 |
| **NEEDS_REVIEW** | **35** | 有具体待判项（见下表），多数可确定性解决 |
| **REJECT** | **1** | `MANUAL_JD03`（工行科技菁英）：正文是招聘公告，抽不出任何要求行 |

### 原因分布（可多重命中）

| 原因 | 数量 | 性质 |
|---|---|---|
| 身份标签带渠道前缀（`【2027届秋招】`/`校招-`/`「2027校招」`） | 36 | **确定性**：改用页面字面即可 |
| 正文需确定性投影（只存了 JD 块，缺标题行） | 42 | **确定性**：按 R31 已验的投影步骤重建 |
| 仅空格差异 | 3 | **确定性** |
| 无小节标题（`工作职责/任职要求`） | 26 | 需人判：Research 分节信号弱，可能把职责当要求 |
| 标签措辞不同（加"方向""-园区"等） | 6 | 需人判：确认是否同一岗位 |
| 正文 < 300 字 | 4 | 需人判：麒麟虚拟化 125 字、C/C++ 261 字等真实短页 |
| 标题带前导 `-`（清洗残留） | 3 | **确定性** |
| 只有列表页 URL，需换详情页 | 3 | **确定性**：麒麟 001/003/004，详情 `jobAdId` 已有 |
| 无 DOM 快照（策展原件） | 4 | 需人判：无法复核页面字面 |
| 社区/转载来源 | 3 | 需人判：不得计作官方直采 |
| 只有文章 ID 无岗位 ID | 3 | 需人判 |
| 规则解析出 0 条要求 | 2 | 需人判（AI技术美术、视觉算法） |
| 只有广告 ID | 1 | 需人判 |

**没有一条是靠规则硬凑成 PASS 的。** 一个反例值得一提：我最初的判定把"结尾没有句号"当成截断，一次误判了 10 份（`…有嵌入式Linux开发经验` 这类列表项本来就不写句号）；把规则改成"悬空连接词/括号不配对"后降到 0。同样，列表页 `h4[title]` 一开始取到了页面第一个岗位（把"云原生研发工程师"读成"Python研发工程师"），改成按记录标题精确匹配后才正确。这两处都是**检查器的错**，不是数据的错，已修。

### 企业分布与方向

企业：蔚来 17、沐瞳 16、麒麟 10、Unity 1、面壁 1、华为 1、移动 2、工行 1 —— **88% 来自三家企业**，这是飞书列表分页没做完造成的，不是抽样选择。

方向：`OTHER` 22（多为美术/商务/运营类）、AI Agent 7、LLM/算法 4、后端 3、云原生/基础软件 3、测试 2、客户端 2，其余各 1。

PASS 的 13 份里，企业是沐瞳 5 / 麒麟 5 / 蔚来 3，方向覆盖 AI Agent 2、LLM/算法 2、测试 2、云原生、安全、嵌入式、数据各 1，另 3 份属美术/商务类。**继续用这三家会把样本绑死在一类 ATS 上**，下批建议先补第 4 家（Unity、面壁、华为）的真实正文。

---

## 八、派生 Requirement：本轮结论

不建新服务。理由是现有机制已经能把"混合强度"表达给所有消费方：存储保留原文与原始强度，消费层按分句计算有效强度，审核时逐条列出分句与依据。

如果将来必须**在数据层**把一句话拆成多行（例如为了让 `job_requirements` 直读方也看到 9R/1P），最小可审计方案是：

1. 不新增表、不加迁移——用现有 `JobDescriptionVersion` **升一个新版本**（v2），旧 v1 保持 `PUBLISHED` 只读；
2. 新版本的要求行全部由人工审核动作生成，每行 `claim_id` 指向**同一个 Claim**，`normalized_value` 记录该行对应的原句分片，`evidence` 仍指向原偏移；
3. 审核理由里写清"由 v1 第 5/6/7/8 条按分句拆分"，并在 `metadata_json` 记录 `derived_from_version_id`；
4. 拆分后必须重跑 Skill/匹配/Context 三项离线断言才能发布。

这个方案不动历史模型输出、不伪造 Evidence，但会多一个 JD 版本，属于"数据层变更"，需要你单独批准。

---

## 九、下一批真实调用预算（只报，不执行）

按 R31 实测 **5,640 token / 份、2 HTTP / 份**（16,921 ÷ 3，含一次失败任务）外推：

| 项目 | HTTP | Token | 硬上限 |
|---|---|---|---|
| 蔚来身份修订后重跑 1 份 | 2 | ≈6.1K（实测 6,047） | 2 / 8,000 |
| 首批 5 份新 JD | 10 | ≈28.2K | 14 / 40,000 |
| 后续 44 份（分 5 批，每批 ~9 份） | 88 | ≈248K | 每批 24 / 70,000 |
| 全量 49 份合计 | 98 | ≈276K | — |

模型固定 `modelscope / deepseek-ai/DeepSeek-V4.1-Flash-1017`，Prompt 固定 `job_jd@1.0.1` + `quality@1.0.0`。

**人工审核抽检比例**：首批 5 份 100% 逐条核 Requirement/Evidence/强度；之后每批——新企业首次出现 100%，混合句记录 100%，类型 AMBIGUOUS/UNKNOWN 100%，其余随机 30%。

**失败处理**：UNKNOWN 立即停并查账本，不自动重试；`prepare` 幂等复用不新建版本；失败任务保持 FAILED，只有**身份类**失败才用 `prepare_identity_revision`，绝不用 `force=True` 赌一次；同一份连续 2 次证据拒绝即停。

---

## 十、Git 与资产对账

| 项 | 状态 |
|---|---|
| 生产库 | 5 users / 38 简历 / 19 场 / 41 评价 / 5 报告 / **4 份 JD** / 0 账本 —— 未变 |
| 沙箱 `..._jd_r31_20261116` | 2 公司 / 2 岗位 / 2 JD 版本（**均 PUBLISHED**）/ 22 要求 / 41 Claim / 43 Evidence / 6 账本 / 2 审计 / 1 用户（沙箱审核员） |
| 回放库 | `..._jd_replay3_r33_20261116` 新建（0029），仅回放数据，**不计入任何统计** |
| 数据库总数 | 249 → **250** |
| MinIO | 生产桶 48 对象未变；`interview-agent-r31-jd` 0 对象 |
| Redis | 未使用（本轮无队列写入） |
| Evaluation 2.3.4 | `e7704357b5fc37ba` 未变 |
| `public_research_official_domains` | 仍为空，未改配置绕过审核 |
| 删除/归档/push/reset/clean | 均未执行 |

沙箱审核员是为本轮 Admin 流程建的本地测试账号（`r33.reviewer@example.com`，role=ADMIN）。最初用了 `.invalid` 保留域名，登录 schema 直接 422 拒掉——这也是"用真实接口验"才发现的问题。

---

## 十一、测试

- `test_r33_publish_and_identity_revision.py`：**25 passed**（发布幂等、审计、targets/前端、强度口径、学历不计技术缺口、修订八种情形、预检分组与"不冒充字面"）
- 回归：`test_manual_public_career_import` / `test_public_career_publication` / `test_m2b_research_agent` / `test_r35_requirement_vocabulary` / `test_r33...` 合并 **134 passed / 0 failed**
- 匹配与上下文回归：`test_interview_topic_policy` / `test_context_projection_target_refs` / `test_job_target` / `test_m6_job_matching` 合并 **133 passed / 0 failed**
