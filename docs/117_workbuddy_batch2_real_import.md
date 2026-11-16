# R34 真实 JD 第二批正式入库

基线 `039feca`。本轮**真实模型 12 次 HTTP / 29,232 token**（授权上限 16 次 / 48,000）：蔚来修订 2 次 / 6,206，新增 5 份 10 次 / 23,026。账本全部 `SETTLED`，模型只有 `deepseek-ai/DeepSeek-V4.1-Flash-1017`，无 Repair、无回退、无重试。

结论：**蔚来身份已修好，但这一轮又卡在下一道更严的校验上；新增 5 份全部抽取成功，其中 3 份发布、2 份因语句不是原文而被我扣下。**沙箱累计可用真实 JD = **5 份**。

---

## 一、预算执行

| 子任务 | 正常 | 硬上限 | 实际 HTTP | 硬上限 | 实际 Token |
|---|---|---|---|---|---|
| 蔚来修订 | 2 | 2 | **2** | 8,000 | **6,206** |
| 新增 5 份 | 10 | 14 | **10** | 40,000 | **23,026** |
| 合计 | 12 | 16 | **12** | 48,000 | **29,232** |

两份预算独立计量（以进程启动时的账本快照为基线算增量），没有互相挪用。逐份：Unity 7,844 · 面壁 3,192 · 沐瞳后端 4,581 · 蔚来推理 3,696 · 麒麟内核 3,713。

---

## 二、蔚来：身份修好了，卡在下一关

### 做了什么

1. 零 HTTP 复核：task `#2` 仍 `FAILED`，冻结 manifest 的 `content_sha256 = aef49bb3…8055` 与 `NIO_AIA_025.txt` **逐字节相同**，`canonical_url` 一致；
2. `prepare_identity_revision` 用页面字面 `校招-车端 AI Agent 与应用生态系统架构工程师` 建修订任务 `#4`（`revision_of=2`），**第二次调用返回同一 task（`reused=true`）**，全程 0 HTTP、0 token；
3. 审计表新增 `public_career.jd.identity_revision`（`resource_id=4`，`SYSTEM`）；
4. 旧任务 `#2` 状态、错误、manifest **一字未改**。

### 结果：FAILED，但错误换了

```
EvidenceValidationError: Job metadata must be literal and cite exact evidence
```

这条错误在 `research/agent.py` 里**位于身份校验之后**：`_validate_identity` 与 `validate_expected_identity` 都已通过，才走到 attribute（职位元数据，如地点/雇佣类型）逐字校验。所以：

- **身份问题确实解决了**——同一份 JD 在 R31 报 `MANUAL_EXTRACTED_IDENTITY_MISMATCH`，本轮不再报；
- 新的阻断是**模型把某条元数据写成了非原文表述**（校验器拒绝落库，属正确行为）。

按规则停在这里：2 次预算已用完，未重试、未扩预算。零实体残留（`company/job/jd_version` 都没有第 3 家蔚来）。

### 为什么这次不能"再修一下继续跑"

预算条写明"HTTP 硬上限 2"，2 次已用尽。而且根因不是身份，是模型对元数据的改写倾向——这属于**抽取质量**问题，不是流程 bug，再花 2 次也可能重复。要往前走只有两条路，都需要你批：① 下一轮给蔚来单独 2 次预算重试（同一修订任务，`reused` 复用，不会产生新版本）；② 先查清是哪条 attribute 被改写（当前不落库，只能从 capture 里看）。

---

## 三、新增 5 份：全部抽取成功

| JD | 企业 | 岗位（页面字面） | 正文 | Requirement | 存储强度 | 消费口径 | 语句逐字 | Skill |
|---|---|---|---|---|---|---|---|---|
| `#3` | Unity中国 | AI Agent 算法研发工程师 | 2,408 字 | 17 | 8R / 9P | 8R / 9P | 7 逐字 + 2 格式 + 8 标题合并 | `RECOMMENDED → ai_agent_application` |
| `#4` | 面壁智能 | 大模型算法工程师 - 智能体方向 | 509 字 | 6 | 4R / 2P | 5R / 1P | 5 逐字 + **1 改写** | `RECOMMENDED → ai_agent_application` |
| `#5` | 沐瞳科技 | 【2027届秋招】后端开发工程师（AI向/数据平台） | 857 字 | 10 | 6R / 4P | 7R / 3P | **10 逐字** | `RECOMMENDED → java_backend` |
| `#6` | 蔚来 | 校招-AI推理引擎开发工程师 | 580 字 | 13 | 5R / 4P / **4 null** | 5R / 4P / **4 UNKNOWN** | 3 逐字 + 7 格式 + **3 改写** | `NO_APPLICABLE_SKILL` |
| `#7` | 麒麟软件 | 「2027校招」Linux内核研发工程师 | 533 字 | 9 | 6R / 3P | 6R / 3P | **9 逐字** | `NO_APPLICABLE_SKILL` |

**华为被替换**：`MANUAL_JD01` 只有策展文本、**没有 DOM 快照**，不满足你列的"必须有原始 DOM"，属明显不合格；中国移动 2 份与工行 1 份是高校转载页，同样无 DOM。因此第 5 个名额给了麒麟软件**内核岗**（官方详情页、独立 `jobAdId=aa9be729…`、与被替换的 AI 研发岗不是同一岗位），方向为操作系统内核，与其余 4 份不重叠。

**投影方式**：正文块逐字未改，只在顶部加了页面自己的两行（企业名 + 标题元素字面串），**没有添加任何招聘网站上不存在的小节标题**。5 份的 `text_sha256`、`dom_sha256`、`job_id` 都落在 `data/r40_batch2/source_manifest.json`。

---

## 四、人工审核发现的三个真实错误

### 1. 模型把原文改写了（2 份被扣下）

我对每条 Requirement 做了逐字核对，分四档：`VERBATIM`（原文子串）、`FORMAT_VARIANT`（只差全半角/大小写/空格）、`HEADING_JOINED`（把页面上独立成行的小标题并进句子，文字本身在页面上）、`PARAPHRASE`（改词、增删、换序）。

被扣下的具体差异（都能逐字对照）：

| JD | 条 | 模型写的 | 原文 |
|---|---|---|---|
| `#4` 面壁 | req40 | `2027届应届毕业生，计算机、人工智能或相关专业，本科及以上学历` | `计算机、人工智能或相关专业的2027届应届毕业生，本科及以上学历；`（**语序被改**） |
| `#6` 蔚来 | req57 | `…降低推理成本，包括算子/编译优化…` | `…降低推理成本，包括但不限于:算子/编译优化…`（**删掉"但不限于"**，语义收窄） |
| `#6` 蔚来 | req58 | `…推理优化技术，包括KV Cache优化、模型量化、剪枝…` | 原文 `包括但不限于:KV Cache优化、模型量化(Quantization)、剪枝(Pruning)…`（**同样删限定词 + 删英文注释**） |
| `#6` 蔚来 | req63 | `熟悉至少一种主流深度学习框架（如PyTorch…）` | 原文 `熟悉至少一种主流的深度学习框架(如 PyTorch…`（**删了一个"的"**） |

前两条是实质改写（删限定词会把"不限于"变成"就是这些"），后两条接近无害。我按同一条线处理：**只差宽度/大小写/标点/空格的放行，动一个词的扣下**。

有意思的是规律：**`#5`、`#7`（有 `岗位职责/任职资格` 小节）10/9 条全逐字；`#4`、`#6`（无小节标题）出现改写。** 这直接回应你 §四 的问题——允许模型自行识别小节是可行的，但它会顺手把句子"整理"一遍。所以"无小节标题"不是不能入库，而是**入库后必须逐条查逐字**。

### 2. 模型给了 `null` 强度，而消费层把它变成了 REQUIRED

`#6` 有 **4 条** Requirement 的 `importance` 是 `null`。查下来是 `effective_strength` 的兜底逻辑：无强度标记的普通句 + 记录值为 UNKNOWN/null → 返回 **REQUIRED**。这等于**替雇主发明了一条硬性门槛**。

已修：无标记句一律以记录值为准，`null`/空一律保留 `UNKNOWN`（`requirement_vocabulary.py`，新增测试 `test_null_recorded_strength_is_not_promoted_to_required`）。修后 `#6` 的消费口径从 9R/4P 变为 **5R/4P/4 UNKNOWN**。已发布的 3 份（`#1 #2 #3 #5 #7`）没有 null 行，口径不变。

### 3. 模型把页面小标题并进了句子（Unity 8 条）

`#3` 有 8 条以 `加分项：` 开头。查正文第 30 行确实有独立小标题 `加分项`，第 31 行才是 `- 有 Coding Agent…`。也就是说**这 8 条的每个字都在页面上**，只是标题与正文被合并成一行。我把它单列为 `HEADING_JOINED` 并放行，同时在报告里披露——因为严格说它也不是"原样的一行"。

---

## 五、发布结果

| JD | 结论 | 理由 |
|---|---|---|
| `#3` Unity AI Agent | **PUBLISHED** | 0 阻断、0 改写、0 标签臆造、17 条全部有精确证据 |
| `#5` 沐瞳后端 | **PUBLISHED** | 10/10 逐字 |
| `#7` 麒麟内核 | **PUBLISHED** | 9/9 逐字 |
| `#4` 面壁 | **HELD**（`WAITING_REVIEW`） | 1 条改写 |
| `#6` 蔚来推理 | **HELD**（`WAITING_REVIEW`） | 3 条改写（其中 2 条删了"但不限于"） |

发布走 `PublicCareerReviewService.publish_jd`（ADMIN，0 模型），每份都连调两次验证幂等：`reused=true`、`jd_version_id` 不变。审计表新增 3 行 `public_career.jd.publish`（`#3/#5/#7`），连同上一轮的 2 行共 5 行 `ADMIN` + 1 行 `SYSTEM`（身份修订）。

**没有直接 UPDATE 任何状态。** 两份被扣下的仍停在 `WAITING_REVIEW`，随时可以人工确认后发布。

---

## 六、发布后验证

| 检查 | 结果 |
|---|---|
| `GET /api/v1/interview/targets` | **200**，返回 **5** 个岗位（Unity AI Agent、沐瞳 Agent、沐瞳后端、麒麟 AI研发、麒麟内核） |
| `GET /api/v1/public-career/jobs` | **200**，**5** 条 |
| 前端详情 `GET /public-career/jobs/{id}` | 5 个 job 全部 200；详情里的强度取消费口径（如 `#3` 8R/9P、`#5` 7R/3P） |
| Requirement/Evidence 读回 | 沙箱 77 条 requirement / 125 claim / 130 evidence，偏移精确 |
| Skill 推荐 | Unity `ai_agent_application`、沐瞳后端 `java_backend`、麒麟内核 `NO_APPLICABLE_SKILL`（理由："没有充分适用的专业规则"） |
| Job Matching 分类 | 学历类仍 `NOT_APPLICABLE`；技术类 `NO_EVIDENCE`（无画像） |
| Interview Context | 用**明确标注的合成画像**跑通真实 `build_context`：10 条要求、**7R/3P/0 UNKNOWN**、指纹唯一、`jd_fingerprint` 与库内一致、0 模型调用 |
| 重复导入/发布幂等 | `prepare` 复用 task、`publish` 复用版本，7 个版本没多出来 |
| 未审核不可选 | `#4`/`#6` 未发布 → targets 只有 5 个 |

**关于合成画像**：`users#2`（`synthetic.r34@example.com`）+ `resumes#1`（文件名 `SYNTHETIC_FIXTURE_NOT_A_REAL_RESUME.txt`，`raw_text` 首句即声明非真人）+ `candidate_profile_versions#1`（`producer=SYNTHETIC_ENGINEERING_FIXTURE_R34`，无任何 fact、无 evidence）。它**只存在于沙箱**，不写任何 Memory，不参与任何面试。这是你 §六 允许的工程测试对象，我按"明确标记、不冒充"处理。

---

## 七、沙箱当前状态

| 项 | 值 |
|---|---|
| 公司 / 岗位 / JD 版本 | **5 / 7 / 7**（其中 **5 个 PUBLISHED**、2 个 `WAITING_REVIEW`） |
| Requirement / Claim / Evidence | 77 / 125 / 130 |
| 模型账本 | 18 行，全部 `SETTLED`，累计 46,153 token（含 R31 的 6 次） |
| 审计日志 | 6 行（5 发布 + 1 身份修订） |
| 用户 | 2（1 个沙箱审核员 ADMIN + 1 个合成测试候选人） |

**累计可用真实 JD = 5 份**（全部 PUBLISHED 且能通过 `targets` 选到）。另有 2 份已抽取但被扣在 `WAITING_REVIEW`，不计入"可用"。

---

## 八、剩余候选

预检的 49 份里本轮用掉 5 份，**剩 44 份**：

| 结论 | 数量 | 说明 |
|---|---|---|
| PASS | **12** | 沐瞳 5 / 麒麟 4 / 蔚来 3，可直接进下一批 |
| NEEDS_REVIEW | 31 | 其中 **22 份只是"无小节标题"**（本轮证明可入库，但要逐条查逐字）；5 份措辞差异需人判；4 份正文 <300 字；3 份需换详情页 URL |
| REJECT | 1 | 工行招聘公告，抽不出要求 |

按本轮实测 **5,640 token/份、2 HTTP/份**：下一批 5 份 = 10 HTTP / ≈28K，硬上限建议 14 / 40K。若要把 `#4`/`#6` 救回来，只有"人工确认改写可接受"或"改 Prompt 重抽"两条路，前者 0 成本、后者要动冻结 Prompt，我不建议。

---

## 九、Git 与受保护资产

| 项 | 状态 |
|---|---|
| 生产库 | 5 users / 38 简历 / 19 场 / 41 评价 / 5 报告 / **4 份 JD** —— 未变 |
| 数据库总数 | 250（本轮未新建） |
| MinIO | 生产桶 48 对象、`interview-agent-r31-jd` 0 对象 |
| 52 份原始 JD / DOM / URL / Hash | 全部保留，未改动 |
| Evaluation 2.3.4 | `e7704357b5fc37ba` 未变 |
| `public_research_official_domains` | 仍为空，未改配置绕过审核 |
| 删除 / 归档 / 清库 / push / reset / clean | 均未执行 |

测试：本轮契约 `test_r34_batch2_real_run.py` **14 passed**；词表 **65 passed**（含新增的 null 强度用例）；跨模块回归 **163 passed / 0 failed**。
