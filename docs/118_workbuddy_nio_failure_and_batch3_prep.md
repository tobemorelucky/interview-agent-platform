# R35 真实 JD 待审问题定位与下一批入库准备

基线 `e639bec`（工作区无未提交的源码改动）。本轮**真实模型 HTTP = 0 / token = 0 / 数据库写入 = 0**，只读、只算、只出方案。蔚来两条失败任务（`#2`、`#4`）、两份 `WAITING_REVIEW`（`#4` 面壁、`#6` 蔚来推理）与 52 份原始 JD 全部原样保留。

一句话结论：**蔚来这次的失败不是"模型乱写"这么简单——抽取器把正文每 600 字硬切一块，正好把页面第 17 行从中间切开，逐字引用也会被判"不逐字"。这是一个可复现的假阴性通道，比"模型改写"更值得先修。** 面壁/蔚来两份待审稿没有任何合法改文本的现存通道，本轮不动。

---

## 一、蔚来车端 Agent：失败字段查不到，失败机制查到了

### 1.1 先回答"能不能直接读出来"

**不能。** 这是实测，不是推测：

| 检查 | 结果 |
|---|---|
| 沙箱 68 张表里有没有 capture/raw-response 表 | **没有**（`information_schema` 全量列举） |
| `experience_agent_step_runs.output_json`（task#4 的 job_jd 步骤） | 只有 `["model_invocations","schema","usage_complete"]` — **没有 attributes、没有 requirements、没有任何抽取文本** |
| 同 run 的 quality 步骤 | 多了 `risk_flags` / `suitable`，仍无抽取文本 |
| `source_snapshots.metadata_json` / `tool_invocations.response_json` / `audit_logs.metadata_json` | 均无抽取产物 |
| 代码层面的口径 | `research/audit.py:83` 注释写明 *"never store provider reasoning or raw model responses"*，白名单只保留 8 个标量键，`attributes` 不在其中 |

所以：**"是哪一条 attribute、哪一个词被改写"——无法从现有留痕直接确定。** 下面所有内容都是"能证明的"，不是"猜出来的"。

### 1.2 已经能钉死的事实

| # | 事实 | 证据 |
|---|---|---|
| 1 | 失败点在 `agent.py:102–104`，位于身份校验（`:94–95`）**之后** | 代码顺序；task#4 不再报 `MANUAL_EXTRACTED_IDENTITY_MISMATCH` |
| 2 | manual 路径下 **requirements 不做逐字校验，只有 attributes 做** | `agent.py:96–101` 只在 `if web:` 时才 `supports_literal`；`:102–104` 无条件校验 attribute |
| 3 | 实际跑的 prompt 是 `job_jd@1.0.1`（`d86cd522…`），全文 **0 次**提到 `attribute` | step#7 `input_json.prompt_hash` 与 registry 哈希一致；`prompt.system.count('attribute') == 0` |
| 4 | 同一 schema 的 web 版 `job_jd@1.1.0` **有**这句约束："with exact original statements and evidence_ids; unknown stays absent" | `prompts.py:53–56` |
| 5 | 该 JD 的 snapshot 是 1500 字符（含 CRLF），被切成 3 块：600 / 600 / 300 | `EvidenceCatalog` 实测 |
| 6 | 页面第 17 行（`参与设计车端 AI Agent 应用平台架构…`）占 585–649，**正好跨 600 边界** → 单块引用判 False，引用相邻两块判 True | `data/r44_nio_diagnosis/validator_probe.json`；其余 12 条元数据行单块全 True |
| 7 | 模型自己在第 2 次调用（quality）里就报了这个问题 | risk_flags 原文：`evidence_fragmentation: JD证据文本在句子中间被截断（如"参与设计车端 AI Agent""对'A'""I 如何成为…"）` |

### 1.3 五种可能原因的判定

| 假设 | 判定 | 依据 |
|---|---|---|
| 字段映射错 | **排除** | `attributes` 从 pydantic 到 DB 直通，schema 里 `field` 只有 4 个枚举 |
| 网页信息不足 | **排除** | 13 条元数据行逐条在页面上，逐字可验 |
| 引用缺失（该引没引） | **成立，且可枚举** | 第 17 行必须同时引 2 个 evidence id；只引 1 个必失败 |
| 校验器把逐字判错 | **部分成立（假阴性）** | 逐字句因切块被切成两半，单块 `supports_literal` 返回 False |
| 模型改写 | **既不能确认也不能排除** | 留痕不含抽取文本；历史上模型确实会"整理"（见第二节 r57） |

**结论**：这是一条"模型改写"与"分块边界"**共用同一个错误码**的路径。把蔚来简单归因为"模型不听话再跑一次"是不成立的——至少有一条原因在流程侧。

### 1.4 最小修复（方案，未执行）

**F1（推荐，5 行，不改校验规则）**：分块改成行对齐——`end` 落到下一个换行，超长行仍按 600 硬切。

```python
# research/evidence.py  EvidenceCatalog.__init__
end = start + min(span_chars, max_chars - used, per_source_budget - start)
if end < len(text):
    nl = text.find("\n", start + span_chars // 2)
    if 0 < nl < end + span_chars // 2:
        end = nl + 1
```

`supports_literal` 一个字不改：仍要求是**单块连续文本的子串**，既不放宽也不做语义匹配。离线模拟（`r48_span_fix_simulation.py`，未改代码）：

| JD | 现状：单块引用失败行 | 行对齐后 |
|---|---|---|
| NIO_AIA_025 | 2 / 38 | 0 / 38 |
| KYLINOS_SEC_5480b909 | 1 / 19 | 0 / 19 |
| KYLINOS_EMB_cd3ac686 | 1 / 17 | 0 / 17 |
| MOONTON_QA_008 | 3 / 50 | 0 / 50 |
| MOONTON_PM_013 | 1 / 25 | 0 / 25 |
| NIO_CV_023 | 1 / 28 | 0 / 28 |
| NIO_SUPPLY_024 | 2 / 29 | 0 / 29 |
| **合计** | **11 / 206** | **0 / 206** |

代价：evidence_id 是 `hash(snapshot,start,end,text)`，改动后**新导入**的 id 会变；已入库证据不受影响（存的是 offset + excerpt）；Replay 夹具若固定了 id 需要同步更新。

**F2（0 行代码）**：不改分块，只给蔚来 2 次重试。风险量化：该 JD 有 2/38 行落在边界上，模型若"偷懒只引一块"就会再次失败。

**F3（不做）**：放宽 `supports_literal`、或把 attribute 失败降级为告警。**拒绝** —— 那等于允许元数据非原文入库，第 4 节的整条质量标准就废了。

**安全重试方案（待批，未执行）**：沿用 task#4（`revision_of=2`，`reused=true`，不产生新版本），2 HTTP / 8,000 token；若先批 F1，则同一份证据下的假阴性风险归零。

---

## 二、面壁 #4、蔚来 #6：差在哪、能不能救

逐条比对（`r46_held_jd_review.py`，四档：`VERBATIM / FORMAT_VARIANT / HEADING_JOINED / PARAPHRASE`）：

### 2.1 面壁 #4（6 条要求：5 逐字 + 1 改写）

| 条 | 模型写的 | 原文 | 差异 | 语义 |
|---|---|---|---|---|
| r40 | `2027届应届毕业生，计算机、人工智能或相关专业，本科及以上学历` | `1. 计算机、人工智能或相关专业的2027届应届毕业生，本科及以上学历；` | 把"…相关专业的2027届应届毕业生"改成"2027届应届毕业生，…相关专业" | **等价**。修饰语换位，招聘条件集合完全相同；顺带去掉了列表序号与句末分号 |

判定：**只是表达顺序不同，没有改变招聘要求的含义**。但它确实不是原文，所以发布门禁拒它是对的——错的是"拒了之后没有合法纠正通道"。

### 2.2 蔚来 #6（13 条：3 逐字 + 7 格式 + 3 改写）

| 条 | 模型写的 | 原文 | 语义损失 |
|---|---|---|---|
| r57 | `…降低推理成本，包括算子/编译优化、异构推理、分布式并行等` | `…降低推理成本，包括但不限于:算子/编译优化、异构推理、分布式并行等；` | **实质收窄**。`包括但不限于` → `包括`，把开放式清单变成封闭清单，候选人会以为"只要这三类"。这条不能放行 |
| r58 | `…推理优化技术，包括KV Cache优化、模型量化、剪枝、跨机分布式推理优化等` | `…推理优化技术，KV Cache优化、模型量化(Quantization)、剪枝(Pruning)，跨机分布式推理优化等。` | **轻**。多加一个"包括"，删掉两个英文注释（与原词同义），句读改动。技术范围未变，但英文对照信息丢失 |
| r63 | `熟悉至少一种主流深度学习框架（如PyTorch、TensorFlow），了解其基本实现原理` | `熟悉至少一种主流的深度学习框架(如 PyTorch、TensorFlow)，了解其基本实现原理` | **无**。少一个"的" |

7 条 `FORMAT_VARIANT` 全是全半角括号、大小写（`cuda`→`CUDA`）、空格差异，按既定规则放行。

### 2.3 顺带查出的一个更影响"证据有效性"的问题

这两份 JD 的证据跨度都是**整篇一个 span**（`0–523` / `0–592`）——因为正文短于 600 字，`EvidenceCatalog` 只切出一块。也就是说 6 条 / 13 条 requirement 引的是**同一个 evidence id**，"offset 命中"在这里完全不具备区分度。这直接印证第 4 节：**不能拿 offset 命中当正确性依据。**

### 2.4 有没有合法的修订路径

| 通道 | 能否改 requirement 文本 | 结论 |
|---|---|---|
| `PublicCareerReviewService.publish_jd` | **不能**。它只做 `WAITING_REVIEW/APPROVED → PUBLISHED`，写 `manual_review` 审计；已发布后再调只返回 `reused=true`，改 `matching_constraints` 直接 `PUBLISHED_JD_IMMUTABLE` | 只能"确认"，不能"纠正" |
| `ManualImportService.prepare` | 幂等键 = `(canonical_url, content_hash, document_type)`，同一份原文第二次 `prepare` 命中旧任务（R33 实测抛 `MANUAL_IMPORT_IDENTITY_CONFLICT`） | 同一份原文无法再抽一次 |
| `prepare_identity_revision` | 只接受**前驱 FAILED**、同 URL 同 Hash、新标签须在快照里 | 不适用于 `WAITING_REVIEW` |
| 版本层 | `job_description_versions` 有 `version_number`，`published_jobs()` 取每个 job **已发布的最大版本号** | **v2 更正版天然可以替代 v1** ——语义是现成的，缺的是"谁来创建 v2" |

**结论：现有代码没有任何"改正 requirement 文本并留痕"的通道。** 本轮按你的要求保留 `WAITING_REVIEW`，不 SQL 改写、不放宽校验、不 UPDATE 成 PUBLISHED。

**最小必要方案 M1（待批，未实现）**：新增 `create_corrected_version(jd_id, corrections, reviewer_id, reason)`

1. 前驱须为 `WAITING_REVIEW` 或 `PUBLISHED`，取 `version_number + 1`，同 `job`、同 `snapshot`；
2. 每条更正必须给出 `evidence_ids`，并**复用同一个 `supports_literal`** 通过（不许放宽）；
3. 新版本落 `WAITING_REVIEW`，再走一次 `publish_jd`；
4. 写 `audit_logs`：`action=public_career.jd.correction`，`before_json` / `after_json` 存原句与更正句，`metadata_json` 存 reviewer 与理由；
5. **旧版本一行不改**，历史可回放。

这条方案把"人工确认改写可接受"从"口头放行"变成"有 diff、有署名、有审计"的可查动作——正是第 4 节要求的那一条"修改后的内容是否经过合法人工审核"。

---

## 三、下一批：6 份真实 JD（不同企业 × 不同方向）

零 HTTP 校验通过（`r47_build_batch3.py`，全部硬断言，任一项不过直接退出）：

| # | jd_key | 企业 | 页面字面岗位名 | 方向 | 正文 | job_id | 分块切行 |
|---|---|---|---|---|---|---|---|
| 1 | `KYLINOS_SEC_5480b909` | 麒麟软件 | `「2027校招」安全研发工程师` | 安全研发 | 920 字 | `5480b909-65bb-…` | 1 |
| 2 | `KYLINOS_EMB_cd3ac686` | 麒麟软件 | `「2027校招」嵌入式研发工程师` | 嵌入式 | 845 字 | `cd3ac686-922d-…` | 1 |
| 3 | `MOONTON_QA_008` | 沐瞳科技 | `【2027届秋招】AI测试工程师（web平台）` | 测试/QA | 1871 字 | `7668238439854967049` | 3 |
| 4 | `MOONTON_PM_013` | 沐瞳科技 | `【2027届秋招】AI Agent 产品经理（平台）` | AI 产品 | 899 字 | `7669735314894555411` | 1 |
| 5 | `NIO_CV_023` | 蔚来 | `校招-视觉算法工程师` | 视觉算法 | 823 字 | `7673027221096237363` | 1 |
| 6 | `NIO_SUPPLY_024` | 蔚来 | `校招-供应链算法工程师` | 供应链算法 | 1699 字 | `7673026521368004890` | 2 |

来源、校验与投影（每条都记录在 `data/r47_batch3/source_manifest.json`）：

| 检查 | 结果 |
|---|---|
| 官方来源 | 6/6：麒麟 `kylinos.zhiye.com`（`jobAdId` 详情页）、沐瞳/蔚来 `*.jobs.feishu.cn` 官方投递页 |
| 正文 SHA-256 与语料一致 | 6/6（语料哈希口径 = LF 归一化文本，逐条断言） |
| DOM 快照 | 6/6 存在，`dom_sha256` 已入清单 |
| 页面字面标题 | 6/6 从 DOM 标题元素取出，且在公司可见文本里命中 |
| 稳定岗位 ID | 6/6（4 个 `position_path`、2 个 `jobAdId`） |
| 投影方式 | 顶部两行 = 页面自身的「企业名 + 标题字面串」，正文块逐字未改，**新增小节标题 0 个** |
| 与已发布 5 份、待审 2 份重复 | **0 重复**：job_id 不与 R34 清单相交，标题（去空格归一）不与库内 7 条相同，正文哈希不与库内 `content_fingerprint` 相同 |
| 小节标题 | **6/6 有职责小节**（`工作职责` / `岗位职责：` / `职位描述`），其中 4 份另有资格小节（`任职资格` / `职位要求` / `岗位要求`）；`MOONTON_QA_008` 与 `NIO_CV_023` 只有职责小节、要求散在职责里 → 这两份按 R34 规律改写风险相对高，逐条查 |

方向覆盖：安全、嵌入式、测试、产品、CV、运筹——与已发布的 AI 研发 / Agent / 后端 / 内核、待审的 LLM 算法 / 推理引擎**均不重叠**；企业维度 3 家各 2 份（不是把某一家的同类岗堆进来）。

**替补 2 份**（仅在上述任一份被否时启用）：`沐瞳科技_POS_7683867480011622697`（AI 技术美术，2183 字，正文最长）、`KYLINOS_dd5441a9`（Android 系统研发，417 字，篇幅偏短）。

**为什么没用 `OFFICIAL_CAND_016`（车端 AI Agent 运行时基座）**：与已经连续失败的蔚来车端 Agent 岗同企业同方向，且同样无小节标题；在 F1 落定前不进队，避免把同一个坑再踩一次。

---

## 四、"22 份无小节"的重新核算：口径该改了

R34 报的 22 是"26 份带 `NO_SECTION_HEADERS` 减去 4 份短正文"，把两种不同性质的问题混在一起了。按"**只有小节标题这一个问题**"重新数：

| 口径 | 数量 | 说明 |
|---|---|---|
| 带 `NO_SECTION_HEADERS` 标记 | **26** | |
| 只带这一个问题 | **19** | 其余 7 份另有 `IDENTITY_LABEL_WORDING_DIFFERS`（4）、`SHORT_BODY`（2）、社区源无 DOM（1） |
| 满足正式 Research 准入 | **16** | 再排除已入库 3 份 |

准入 9 项（`r49_no_header_admission.py`，全过才算）：仅此一个 flag / DOM 存在 / 正文存在 / 页面字面可证 / 哈希一致 / 稳定 job_id / ≥300 字 / 未截断 / 无导航噪音 / 未被导入。

**16 份准入清单**（按方向）：

- 客户端：`OFFICIAL_CAND_012`（沐瞳 Unity客户端-MOBA，1278 字）、`OFFICIAL_CAND_011`（沐瞳 AI客户端-MOBA，834 字）
- AI Agent：`OFFICIAL_CAND_018`（蔚来 座舱大模型后训练/Agentic，996 字）、`OFFICIAL_CAND_014`（蔚来 Agent应用开发-销售运营，992 字）、`OFFICIAL_CAND_017`（蔚来 座舱Agent Harness，831 字）
- 后端/全栈：`OFFICIAL_CAND_006`（沐瞳 后端-IT信息化，781 字）、`OFFICIAL_CAND_010`（沐瞳 全栈，385 字）
- 平台/部署：`OFFICIAL_CAND_020`（蔚来 AI仿真与验证平台，726 字）、`OFFICIAL_CAND_022`（蔚来 AI模型部署与性能优化，713 字）、`OFFICIAL_CAND_026`（蔚来 BSP，662 字）、`OFFICIAL_CAND_027`（蔚来 产品系统工程师，579 字）
- 非技术/运营：`沐瞳人力成本分析专员`(509)、`资源运营-MOBA`(372)、`公共事务专员`(340)、`分镜画师`(321)
- 服务端：`C++服务器开发（MOBA）`(370)

**拒绝的 10 份及理由**：已入库 3（Unity 029 / 沐瞳后端 / 蔚来推理）、无 DOM + 社区源 1（工行 `MANUAL_JD03`）、`SHORT_BODY` 2（284 / 281 字）、标题措辞差异 4（`-` 前缀、命名与页面不一致，需人判）。

**处理建议**：
1. **不批量放行**。16 份进队要排优先级：优先 800 字以上、有独立方向的 7 份；300–600 字的"运营/画师/公共事务"类先不进——它们对面试题生成没有增量，却要花同样的 2 HTTP。
2. **无小节 ≠ 不能入库，但必须逐条查逐字**。R34 的实测规律很清楚：有小节的 `#5`/`#7` 10/10、9/9 全逐字；无小节的 `#4`/`#6` 出现改写。所以这批的验收必须跑第 4 节的完整检查表，不能只看 offset。

---

## 五、入库质量标准：把"offset 命中"降级为必要条件

今后每份 JD 发布前必须逐条过这 9 项，**全部可执行、可留痕**：

| # | 检查 | 判定方式 | 现状 |
|---|---|---|---|
| 1 | 企业与岗位身份 | 页面字面标题 + 企业名同时出现在同一快照；`validate_expected_identity` 通过 | 已有（R33 身份修订） |
| 2 | Requirement 是否忠实原文 | 四档分类，`PARAPHRASE` 一律拦 | 已有（`r42.classify_statement`） |
| 3 | 是否遗漏关键技术条件 | 正文的"要求类"行数 vs 抽取条数做区间比对，缺 30% 以上拦 | **缺**，建议新增 |
| 4 | 强度 REQUIRED/PREFERRED/UNKNOWN | 词表归一化 + null 不升级（已修） | 已有 |
| 5 | 学历等资格不误入技术匹配 | `can_be_technical` + 硬资格 `NOT_APPLICABLE` | 已有 |
| 6 | "优先""不限于"等限定词保留 | 原文含限定词而抽取句不含 → 直接拦 | **缺**，建议新增（本次 r57 就是它漏的） |
| 7 | 标题合并只改格式 | `HEADING_JOINED` 单独成档，且要求该标题在页面上独立成行 | 已有 |
| 8 | 证据定位是否有区分度 | 若多条 requirement 共用同一 span，标注"证据粒度=整篇"，不得声称精确定位 | **缺**，建议在审计报告里披露 |
| 9 | 修改是否经合法人工审核 | `publish_jd` 必须有 reviewer + reason；文本更正必须走 M1（版本 + diff + 审计） | 部分：发布有，文本更正**没有** |

第 6 条是最容易补、收益最大的一条：`包括但不限于` / `优先` / `至少一种` / `等` 这些词一旦被删，requirement 的**约束强度就变了**——这比错一个字的危害大得多。

---

## 六、下一批预算与验收

### 6.1 预算（按 R34 实测拟合：token ≈ 2,500 + 2.2 × 正文字数）

| JD | 正文 | 预计 token | HTTP |
|---|---|---|---|
| KYLINOS_SEC_5480b909 | 920 | 4,500 | 2 |
| KYLINOS_EMB_cd3ac686 | 845 | 4,400 | 2 |
| MOONTON_QA_008 | 1871 | 6,600 | 2 |
| MOONTON_PM_013 | 899 | 4,500 | 2 |
| NIO_CV_023 | 823 | 4,300 | 2 |
| NIO_SUPPLY_024 | 1699 | 6,200 | 2 |
| **合计** | | **≈30,500** | **12** |

- **正常预算：12 HTTP / 31,000 token**
- **硬上限：16 HTTP / 46,000 token**（含 4 次余量，不允许跨份挪用；以进程启动时账本快照为基线算增量）
- 每份固定 2 次调用（`job_jd` + `quality`），与现有正式 Research 流程完全一致，**不新增 Agent、不新增基础设施**

### 6.2 失败时停止条件（沿用 R34 口径）

1. 单份 `FAILED` → 该份停止，不自动重试；
2. 连续 2 份 `FAILED` → **整批停止**（说明是系统性问题，不是个案）；
3. 任何 `UNKNOWN` / 配额错误 / 账本归属异常 → 立即停止，不换 Provider；
4. 账本增量触达硬上限 → 立即停止；
5. 抽取成功但审核不通过（`PARAPHRASE` / 缺限定词）→ 该份留 `WAITING_REVIEW`，不为了凑数放行。

### 6.3 结构化 Repair 预算

- 默认 **0 次 Repair**（R31–R34 实测 `structured_retry` 全为 0，没有触发过）；
- 若失败原因是**schema 校验**（字段缺失/类型错），允许该份追加 1 次 bounded repair，输入字节上限沿用 `max_repair_input_bytes`，**计入同一份的 2 次预算之外，从 4 次余量里扣**；
- 若失败原因是**证据/逐字校验**（如蔚来这次），**不允许 Repair** —— 重试同一 prompt 期望它"这次老实点"不是工程手段，先修 F1 或改 prompt 版本。

### 6.4 发布前核验（每份都要跑）

四档逐条分类 → 9 项检查表 → 身份/哈希/证据回读 → `publish_jd`（ADMIN + reason）→ 连调两次验幂等 → `published_jobs()` 复查版本号与条数。

---

## 七、沙箱资产状态（未变）

| 项 | 值 |
|---|---|
| 企业 / 岗位 / JD 版本 | 5 / 7 / 7 |
| 发布状态 | **5 PUBLISHED**（麒麟 AI研发、沐瞳 Agent、Unity AI Agent、沐瞳 后端、麒麟 内核）+ **2 WAITING_REVIEW**（面壁、蔚来推理） |
| Requirement / Claim / Evidence | 77 / 125 / 130 |
| 模型账本 | 18 行全 `SETTLED`，累计 46,153 token，单一模型 `deepseek-ai/DeepSeek-V4.1-Flash-1017` |
| 审计日志 | 6 行（5 发布 + 1 身份修订），本轮**未新增** |
| `published_jobs()` | 返回 **5** 行（`#4`/`#6` 正确排除） |
| 失败任务 | `#2`（身份不匹配）、`#4`（元数据非逐字）均保持 `FAILED`，manifest 与错误原文一字未改 |

52 份原始 JD：48 份抓取正文哈希 **48/48 与语料一致**，4 份策展件（华为/移动×2/工行）原样保留，无删除、无归档、无清库。

---

## 八、Git 与测试

| 项 | 状态 |
|---|---|
| HEAD | `e639bec`（未提交新 commit，工作区只新增本轮脚本/数据/文档） |
| 源码改动 | **0**（`src/` 未动，F1 与 M1 都只出方案） |
| 新增脚本 | `r44_nio_failure_diagnosis.py`、`r45_nio_validator_probe.py`、`r46_held_jd_review.py`、`r47_build_batch3.py`、`r48_span_fix_simulation.py`、`r49_no_header_admission.py`、`r50_sandbox_state.py` |
| 新增测试 | `tests/test_r35_span_boundary_defect.py`（6 条，把"逐字句会被切块判错"钉成回归特征） |
| R33–R35 契约测试 | **135 passed** |
| 公共岗位相关回归（`-k "public_career or requirement or evidence or matching"`） | **191 passed / 71 skipped / 0 failed** |
| 生产库 / Evaluation 2.3.4 / MinIO / 备份 | 未触碰 |
| push / reset / clean / 删除 / 归档 | 均未执行 |

---

## 九、需要你决定的三件事

1. **F1（分块行对齐，5 行）批不批？** 批了下一批 6 份的假阴性风险从 11/206 行降到 0，且不改校验强度。
2. **蔚来 task#4 要不要在 F1 之后再给 2 HTTP？** 身份问题已确认修好，只差这一道。
3. **M1（更正版本通道）要不要做？** 不做的话，面壁 #4 这类"等价改写"只能长期挂在 `WAITING_REVIEW`，或者由人工口头确认后发布。
